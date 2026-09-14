from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

from domain.models import Memory, MemoryLink

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")


def export_memory_notes(
    *, root: Path, memories: Iterable[Memory], links: Iterable[MemoryLink] = ()
) -> tuple[Path, ...]:
    """处理 export memory notes。

    Args:
        root: root 参数。
        memories: memories 参数。
        links: links 参数。
    """
    export_root = root.expanduser().resolve()
    export_root.mkdir(parents=True, exist_ok=True)
    links_by_memory: dict[str, list[str]] = {}
    for link in links:
        links_by_memory.setdefault(link.source_memory_id, []).append(
            f"[[{link.target_memory_id}]] ({link.link_type})"
        )
        links_by_memory.setdefault(link.target_memory_id, []).append(
            f"[[{link.source_memory_id}]] ({link.link_type})"
        )
    written: list[Path] = []
    for memory in memories:
        if memory.sensitivity == "forbidden":
            continue
        if not _SAFE_ID.fullmatch(memory.id):
            raise ValueError("memory export id is invalid")
        target = (export_root / "memories" / f"{memory.id}.md").resolve()
        if export_root not in target.parents:
            raise ValueError("memory export path escapes root")
        target.parent.mkdir(parents=True, exist_ok=True)
        content = memory.content
        if memory.sensitivity == "sensitive":
            content = "[REDACTED]"
        properties = {
            "id": memory.id,
            "type": memory.memory_type,
            "status": memory.status,
            "scope": memory.scope_kind,
            "sensitivity": memory.sensitivity,
            "source_kind": memory.source_kind,
            "reason_code": memory.reason_code,
            "valid_from": memory.valid_from.isoformat() if memory.valid_from else "",
            "valid_to": memory.valid_to.isoformat() if memory.valid_to else "",
        }
        yaml = "\n".join(f"{key}: {_yaml(value)}" for key, value in properties.items())
        backlinks = "\n".join(
            f"- {item}" for item in sorted(links_by_memory.get(memory.id, []))
        )
        text = f"---\n{yaml}\n---\n\n# Memory {memory.id}\n\n{content}\n"
        if backlinks:
            text += f"\n## Links\n\n{backlinks}\n"
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(target)
        written.append(target)
    return tuple(written)


def _yaml(value: object) -> str:
    """执行 处理 yaml 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    return (
        '"'
        + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        + '"'
    )
