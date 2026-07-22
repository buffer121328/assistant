from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tools import ToolInvocation, ToolRegistry
from tools.builtin.workspace_context import (
    ReadonlyShellRunner,
    WorkspaceContextError,
    WorkspaceContextStore,
    build_workspace_tool_descriptors,
    build_workspace_tool_specs,
)
from domain.models import Base, Task, ToolLog, User


def make_store(tmp_path: Path, **kwargs: Any) -> WorkspaceContextStore:
    return WorkspaceContextStore(root=tmp_path, sensitive_values=("secret-token",), **kwargs)


def test_workspace_list_read_search_and_find_are_bounded_readonly(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Demo\nToolRegistry context\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.txt").write_text("Use workspace.search_text for context\n", encoding="utf-8")
    (docs / "image.png").write_bytes(b"\x89PNG\x00binary")

    store = make_store(tmp_path, max_results=10)

    listed = store.list_dir(path=".")
    assert [entry["name"] for entry in listed["entries"]] == ["docs", "README.md"]

    readme = store.read_doc(path="README.md")
    assert "ToolRegistry context" in readme["content"]

    matches = store.search_text(query="context", path=".")
    assert {match["path"] for match in matches["matches"]} == {
        "README.md",
        "docs/guide.txt",
    }
    assert all("image.png" not in match["path"] for match in matches["matches"])

    found = store.find_files(pattern="*.txt", path=".")
    assert [entry["path"] for entry in found["matches"]] == ["docs/guide.txt"]
    assert not (tmp_path / "README.md").read_text(encoding="utf-8").endswith("modified")


@pytest.mark.parametrize("path", ["../secret.txt", "/tmp/secret.txt", ".env"])
def test_workspace_tools_reject_escape_and_denied_paths(tmp_path: Path, path: str) -> None:
    (tmp_path / ".env").write_text("API_KEY=secret-token", encoding="utf-8")
    store = make_store(tmp_path)

    with pytest.raises(WorkspaceContextError):
        store.read_file(path=path)


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlink support unavailable")
def test_workspace_tools_reject_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-workspace-secret.txt"
    outside.write_text("secret-token", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(outside)
    store = make_store(tmp_path)

    with pytest.raises(WorkspaceContextError):
        store.read_file(path="link.txt")


@pytest.mark.parametrize("content", [b"abc\x00def", "x" * 2001])
def test_workspace_read_rejects_binary_or_oversized_files(tmp_path: Path, content: bytes | str) -> None:
    if isinstance(content, str):
        (tmp_path / "large.txt").write_text(content, encoding="utf-8")
        filename = "large.txt"
    else:
        (tmp_path / "binary.txt").write_bytes(content)
        filename = "binary.txt"
    store = make_store(tmp_path, max_file_bytes=2000)

    with pytest.raises(WorkspaceContextError):
        store.read_file(path=filename)



@pytest.mark.asyncio
async def test_workspace_tool_execution_records_safe_tool_log(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello secret-token context\n", encoding="utf-8")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/workspace-tools.db",
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            user = User(display_name="Workspace User")
            session.add(user)
            await session.flush()
            task = Task(
                user_id=user.id,
                platform="test",
                task_type="learn",
                input_text="read context",
            )
            session.add(task)
            await session.flush()

            store = make_store(tmp_path)
            registry = ToolRegistry(session=session, sensitive_values=("secret-token",))
            for spec in build_workspace_tool_specs(store=store):
                registry.register(spec)

            result = await registry.execute(
                ToolInvocation(
                    task_id=task.id,
                    user_id=user.id,
                    name="workspace.read_file",
                    arguments={"path": "README.md"},
                ),
                allowed_tools=("workspace.read_file",),
                approval_required_tools=(),
            )

            assert result["path"] == "README.md"
            [log] = (await session.scalars(select(ToolLog))).all()
            assert log.tool_name == "workspace.read_file"
            assert log.status == "succeeded"
            assert "[REDACTED]" in (log.output_text or "")
            assert "secret-token" not in (log.output_text or "")
    finally:
        await engine.dispose()

def test_workspace_descriptors_are_l1_and_readonly_shell_is_disabled_by_default() -> None:
    descriptors = build_workspace_tool_descriptors(enabled=True)
    by_name = {item.name: item for item in descriptors}

    assert by_name["workspace.list"].risk_level == "L1"
    assert by_name["workspace.read_file"].requires_approval is False
    assert by_name["workspace.search_text"].enabled is True
    assert by_name["shell.readonly_exec"].risk_level == "L2"
    assert by_name["shell.readonly_exec"].enabled is False


@pytest.mark.asyncio
async def test_readonly_shell_executes_allowed_argv_when_enabled(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("workspace context\n", encoding="utf-8")
    store = make_store(tmp_path)
    runner = ReadonlyShellRunner(store=store, enabled=True)

    result = await runner.execute(("cat", "README.md"))
    runner.validate(("find", ".", "-name", "*.md"))

    assert result.exit_code == 0
    assert result.stdout == "workspace context\n"
    assert result.timed_out is False


@pytest.mark.parametrize(
    "command",
    [
        ("rm", "README.md"),
        ("sh", "-c", "cat README.md"),
        ("find", ".", "-delete"),
        ("grep", "context", ".", ">", "out.txt"),
        ("cat", "../README.md"),
        ("cat", ".env"),
    ],
)
def test_readonly_shell_rejects_unsafe_argv(tmp_path: Path, command: tuple[str, ...]) -> None:
    (tmp_path / "README.md").write_text("workspace context\n", encoding="utf-8")
    (tmp_path / ".env").write_text("API_KEY=secret-token\n", encoding="utf-8")
    store = make_store(tmp_path)
    runner = ReadonlyShellRunner(store=store, enabled=True)

    with pytest.raises(WorkspaceContextError):
        runner.validate(command)


def test_readonly_shell_is_disabled_by_default(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    runner = ReadonlyShellRunner(store=store)

    assert runner.available is False
    with pytest.raises(WorkspaceContextError, match="disabled"):
        runner.validate(("cat", "README.md"))
