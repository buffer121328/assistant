from pathlib import Path
import pytest
from domain.models import Memory, MemoryLink
from memory.export import export_memory_notes


def test_obsidian_export_is_safe_redacted_linked_and_idempotent(tmp_path: Path) -> None:
    public = Memory(
        id="memory-public",
        user_id="u",
        content="回答先给结论",
        memory_type="preference",
        status="active",
        sensitivity="public",
    )
    secret = Memory(
        id="memory-secret",
        user_id="u",
        content="synthetic sensitive value",
        memory_type="fact",
        status="active",
        sensitivity="sensitive",
    )
    link = MemoryLink(
        source_memory_id=public.id,
        target_memory_id=secret.id,
        link_type="related_to",
        created_by="user",
    )
    first = export_memory_notes(root=tmp_path, memories=(public, secret), links=(link,))
    second = export_memory_notes(
        root=tmp_path, memories=(public, secret), links=(link,)
    )
    assert first == second
    assert len(list(tmp_path.rglob("*.md"))) == 2
    assert "回答先给结论" in first[0].read_text(encoding="utf-8")
    assert "[[memory-secret]]" in first[0].read_text(encoding="utf-8")
    assert "synthetic sensitive value" not in first[1].read_text(encoding="utf-8")
    assert "[REDACTED]" in first[1].read_text(encoding="utf-8")


def test_obsidian_export_rejects_unsafe_memory_id(tmp_path: Path) -> None:
    memory = Memory(id="../escape", user_id="u", content="x", memory_type="fact")
    with pytest.raises(ValueError, match="id is invalid"):
        export_memory_notes(root=tmp_path, memories=(memory,))


def test_obsidian_export_does_not_create_forbidden_notes(tmp_path: Path) -> None:
    forbidden = Memory(
        id="memory-forbidden",
        user_id="u",
        content="synthetic forbidden placeholder",
        memory_type="fact",
        status="active",
        sensitivity="forbidden",
    )
    assert export_memory_notes(root=tmp_path, memories=(forbidden,)) == ()
    assert list(tmp_path.rglob("*.md")) == []
