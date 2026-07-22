from __future__ import annotations

# ruff: noqa: E402

from collections.abc import AsyncIterator
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

docx_module = pytest.importorskip("docx")
openpyxl_module = pytest.importorskip("openpyxl")
pptx_module = pytest.importorskip("pptx")

Document = docx_module.Document
Workbook = openpyxl_module.Workbook
Presentation = pptx_module.Presentation

from domain.models import (
    Base,
    ImportAudit,
    KnowledgeChunk,
    KnowledgeDocument,
    User,
    Task,
)
from infrastructure.settings.config import Settings
from app.main import create_app
from rag import KnowledgeError, KnowledgeService, SUPPORTED_MEDIA_TYPES
from rag.extractors import PARSER_VERSION
from tools import ToolInvocation, ToolRegistry
from tools.builtin.knowledge import (
    build_knowledge_tool_descriptor,
    build_knowledge_tool_spec,
)


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/knowledge.db", poolclass=NullPool
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def add_user(sessionmaker: async_sessionmaker[AsyncSession], name: str) -> str:
    async with sessionmaker() as session:
        user = User(display_name=name)
        session.add(user)
        await session.commit()
        return user.id


@pytest.mark.asyncio
async def test_ingestion_is_idempotent_atomic_safe_and_user_isolated(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    owner_id = await add_user(sessionmaker, "Owner")
    other_id = await add_user(sessionmaker, "Other")
    root = tmp_path / "imports"
    owner_root = root / owner_id
    other_root = root / other_id
    owner_root.mkdir(parents=True)
    other_root.mkdir(parents=True)
    owner_file = owner_root / "notes.txt"
    owner_file.write_text("sharedterm owner first", encoding="utf-8")
    other_file = other_root / "notes.txt"
    other_file.write_text("sharedterm other only", encoding="utf-8")

    async with sessionmaker() as session:
        service = KnowledgeService(session, import_root=root)
        first = await service.ingest(user_id=owner_id, source=owner_file)
        unchanged = await service.ingest(user_id=owner_id, source=owner_file)
        await service.ingest(user_id=other_id, source=other_file)
        assert first.document_id == unchanged.document_id
        assert unchanged.unchanged is True

        owner_file.write_text("sharedterm owner updated", encoding="utf-8")
        updated = await service.ingest(user_id=owner_id, source=owner_file)
        assert updated.document_id == first.document_id
        assert updated.unchanged is False
        duplicate_file = owner_root / "copy.txt"
        duplicate_file.write_text("sharedterm owner updated", encoding="utf-8")
        duplicate = await service.ingest(user_id=owner_id, source=duplicate_file)
        assert duplicate.document_id == updated.document_id
        assert duplicate.unchanged is True
        before_failure = await service.search(user_id=owner_id, query="updated")

        owner_file.write_bytes(b"\xff\xfe invalid utf8")
        with pytest.raises(KnowledgeError, match="knowledge_parse_failed"):
            await service.ingest(user_id=owner_id, source=owner_file)
        after_failure = await service.search(user_id=owner_id, query="updated")
        assert after_failure == before_failure

        outside = tmp_path / "outside.txt"
        outside.write_text("must never index", encoding="utf-8")
        symlink = owner_root / "escape.txt"
        symlink.symlink_to(outside)
        with pytest.raises(KnowledgeError, match="knowledge_path_outside_root"):
            await service.ingest(user_id=owner_id, source=symlink)

        owner_results = await service.search(user_id=owner_id, query="sharedterm")
        other_results = await service.search(user_id=other_id, query="sharedterm")
        assert {item.content for item in owner_results} == {"sharedterm owner updated"}
        assert {item.content for item in other_results} == {"sharedterm other only"}
        assert all(item.source_label == "notes.txt" for item in owner_results)
        assert all(str(root) not in item.content for item in owner_results)

        document_count = await session.scalar(select(func.count(KnowledgeDocument.id)))
        owner_chunk_count = await session.scalar(
            select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.user_id == owner_id)
        )
        rejected = list(
            await session.scalars(
                select(ImportAudit).where(
                    ImportAudit.user_id == owner_id,
                    ImportAudit.status == "rejected",
                )
            )
        )
    assert document_count == 2
    assert owner_chunk_count == 1
    assert {audit.error_code for audit in rejected} == {
        "knowledge_parse_failed",
        "knowledge_path_outside_root",
    }


@pytest.mark.asyncio
async def test_uploaded_content_is_idempotent_by_checksum_and_parser_version(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    user_id = await add_user(sessionmaker, "Owner")
    root = tmp_path / "uploads"
    content = b"same uploaded knowledge"

    async with sessionmaker() as session:
        service = KnowledgeService(session, import_root=root)
        first = await service.store_upload(
            user_id=user_id, filename="first.txt", content=content
        )
        repeated = await service.store_upload(
            user_id=user_id, filename="renamed.txt", content=content
        )
        document_count = await session.scalar(
            select(func.count(KnowledgeDocument.id))
        )
        chunk_count = await session.scalar(select(func.count(KnowledgeChunk.id)))

    assert repeated.document_id == first.document_id
    assert repeated.unchanged is True
    assert document_count == 1
    assert chunk_count == 1


@pytest.mark.asyncio
async def test_uploaded_content_recovers_from_concurrent_checksum_conflict(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await add_user(sessionmaker, "Owner")
    root = tmp_path / "uploads"
    content = b"concurrent uploaded knowledge"
    checksum = sha256(content).hexdigest()

    async with sessionmaker() as session:
        service = KnowledgeService(session, import_root=root)

        async def conflicting_ingest(*, user_id: str, source: Path) -> object:
            del source
            async with sessionmaker() as concurrent_session:
                concurrent_session.add(
                    KnowledgeDocument(
                        user_id=user_id,
                        source_label="concurrent.txt",
                        source_path=str(tmp_path / "concurrent.txt"),
                        media_type=SUPPORTED_MEDIA_TYPES[".txt"],
                        checksum=checksum,
                        parser_version=PARSER_VERSION,
                        status="ready",
                        chunk_count=0,
                    )
                )
                await concurrent_session.commit()
            raise IntegrityError("insert", {}, RuntimeError("duplicate"))

        monkeypatch.setattr(service, "ingest", conflicting_ingest)
        result = await service.store_upload(
            user_id=user_id, filename="notes.txt", content=content
        )
        session.add(
            KnowledgeDocument(
                user_id=user_id,
                source_label="duplicate.txt",
                source_path=str(tmp_path / "duplicate.txt"),
                media_type=SUPPORTED_MEDIA_TYPES[".txt"],
                checksum=checksum,
                parser_version=PARSER_VERSION,
                status="ready",
                chunk_count=0,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        document_count = await session.scalar(
            select(func.count(KnowledgeDocument.id))
        )

    assert result.unchanged is True
    assert document_count == 1


@pytest.mark.asyncio
async def test_office_and_markdown_extractors_create_searchable_chunks(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    user_id = await add_user(sessionmaker, "Owner")
    root = tmp_path / "imports"
    user_root = root / user_id
    user_root.mkdir(parents=True)

    markdown = user_root / "notes.md"
    markdown.write_text("markdown-token", encoding="utf-8")
    document = Document()
    document.add_paragraph("docx-token")
    document.save(str(user_root / "notes.docx"))
    workbook = Workbook()
    workbook.active["A1"] = "xlsx-token"
    workbook.save(user_root / "notes.xlsx")
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "pptx-token"
    presentation.save(str(user_root / "notes.pptx"))

    async with sessionmaker() as session:
        service = KnowledgeService(session, import_root=root)
        for path in sorted(user_root.iterdir()):
            await service.ingest(user_id=user_id, source=path)
        for token in ("markdown-token", "docx-token", "xlsx-token", "pptx-token"):
            results = await service.search(user_id=user_id, query=token)
            assert len(results) == 1
            assert token in results[0].content

    assert set(SUPPORTED_MEDIA_TYPES) == {".txt", ".md", ".pdf", ".docx", ".xlsx", ".pptx"}


@pytest.mark.asyncio
async def test_knowledge_api_and_tool_return_sourced_results_without_raw_paths(
    sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    owner_id = await add_user(sessionmaker, "Owner")
    other_id = await add_user(sessionmaker, "Other")
    root = tmp_path / "managed-knowledge"
    app = create_app(Settings(knowledge_root=root))
    app.state.db_sessionmaker = sessionmaker

    with TestClient(app) as client:
        imported = client.post(
            "/api/knowledge/import",
            data={"user_id": owner_id},
            files={"document": ("api-notes.txt", b"api-search-token", "text/plain")},
        )
        documents = client.get(
            "/api/knowledge/documents", params={"user_id": owner_id}
        )
        searched = client.get(
            "/api/knowledge/search",
            params={"user_id": owner_id, "query": "api-search-token"},
        )
        isolated = client.get(
            "/api/knowledge/search",
            params={"user_id": other_id, "query": "api-search-token"},
        )

    assert imported.status_code == 201
    assert imported.json()["source_label"] == "api-notes.txt"
    assert documents.json()["items"][0]["source_label"] == "api-notes.txt"
    assert searched.json()["items"][0]["content"] == "api-search-token"
    assert isolated.json()["items"] == []
    assert str(root) not in documents.text
    assert str(root) not in searched.text

    async with sessionmaker() as session:
        task = Task(
            user_id=owner_id,
            platform="desktop",
            task_type="agent",
            input_text="search knowledge",
            status="running",
        )
        session.add(task)
        await session.flush()
        registry = ToolRegistry(session=session)
        registry.register(
            build_knowledge_tool_spec(KnowledgeService(session, import_root=root))
        )
        result = await registry.execute(
            ToolInvocation(
                task_id=task.id,
                user_id=owner_id,
                name="knowledge.search",
                arguments={"query": "api-search-token"},
            ),
            allowed_tools=("knowledge.search",),
            approval_required_tools=(),
        )
    descriptor = build_knowledge_tool_descriptor()
    assert descriptor.risk_level == "L1"
    assert descriptor.requires_approval is False
    assert result[0]["source_label"] == "api-notes.txt"


def test_desktop_knowledge_client_and_dialog_use_safe_file_contract(tmp_path: Path) -> None:
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from assistant_desktop.client import DesktopApiClient
    from assistant_desktop.knowledge_dialog import KnowledgeManagerDialog

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/knowledge/import":
            return httpx.Response(
                201,
                json={
                    "document_id": "document-1",
                    "source_label": "notes.txt",
                    "status": "ready",
                    "chunk_count": 1,
                    "unchanged": False,
                },
            )
        if request.url.path == "/api/knowledge/documents":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(200, json={"items": []})

    source = tmp_path / "notes.txt"
    source.write_text("desktop-token", encoding="utf-8")
    client = DesktopApiClient(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.import_knowledge(source)
        client.list_knowledge_documents()
        client.search_knowledge("desktop-token")
    finally:
        client.close()
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/api/knowledge/import"),
        ("GET", "/api/knowledge/documents"),
        ("GET", "/api/knowledge/search"),
    ]

    application = QApplication.instance() or QApplication([])
    dialog = KnowledgeManagerDialog(
        base_url="http://127.0.0.1:8000", user_id="user-1"
    )
    dialog._documents_refreshed(  # noqa: SLF001 - verify safe rendered metadata
        [
            {
                "document_id": "document-1",
                "source_label": "notes.txt",
                "status": "ready",
                "chunk_count": 1,
            }
        ]
    )
    dialog._search_refreshed(  # noqa: SLF001 - verify safe sourced result
        [
            {
                "document_id": "document-1",
                "source_label": "notes.txt",
                "content": "desktop-token",
                "ordinal": 0,
                "score": 1,
            }
        ]
    )
    assert dialog.document_list.item(0).text() == "notes.txt · ready · 1 chunks"
    assert dialog.result_list.item(0).text() == "notes.txt · desktop-token"
    assert str(tmp_path) not in dialog.document_list.item(0).text()
    dialog.close()
    application.processEvents()
