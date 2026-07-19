from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re


_SAFE_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
ALLOWED_SKILL_RESOURCE_DIRS = frozenset({"data", "templates"})
MAX_SKILL_RESOURCE_BYTES = 64 * 1024


class SkillLoadError(Exception):
    pass


class SkillResourceError(Exception):
    pass


_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*(?:\r?\n)(.*?)(?:\r?\n)---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)


def strip_skill_frontmatter(content: str) -> str:
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return content.strip()
    return content[match.end() :].strip()


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    instructions: str
    source: str
    resources_root: Path | None = None

    def resource(self, rel: str) -> str:
        if self.resources_root is None:
            raise SkillResourceError("Skill resources are unavailable")
        rel_path = _validated_resource_path(rel)
        root = self.resources_root.resolve(strict=True)
        candidate = root.joinpath(*rel_path.parts)
        _reject_symlink_path(root, candidate)
        try:
            target = candidate.resolve(strict=True)
        except OSError as exc:
            raise SkillResourceError("Skill resource is unavailable") from exc
        if not target.is_relative_to(root) or not target.is_file():
            raise SkillResourceError("Skill resource path is unsafe")
        try:
            with target.open("rb") as resource_file:
                content = resource_file.read(MAX_SKILL_RESOURCE_BYTES + 1)
        except OSError as exc:
            raise SkillResourceError("Skill resource is unavailable") from exc
        if len(content) > MAX_SKILL_RESOURCE_BYTES:
            raise SkillResourceError("Skill resource is oversized")
        try:
            return content.decode("utf-8")
        except UnicodeError as exc:
            raise SkillResourceError("Skill resource must be UTF-8 text") from exc

    @property
    def summary(self) -> str:
        for line in self.instructions.splitlines():
            normalized = line.strip().lstrip("#").strip()
            if normalized:
                return normalized
        return self.name


class SkillsLoader:
    def __init__(self, skills_root: Path) -> None:
        self.skills_root = skills_root.resolve()

    def load(self, enabled_names: tuple[str, ...]) -> tuple[SkillDefinition, ...]:
        loaded: list[SkillDefinition] = []
        seen: set[str] = set()
        for name in enabled_names:
            if name in seen:
                continue
            seen.add(name)
            loaded.append(self._load_one(name))
        return tuple(loaded)

    def _load_one(self, name: str) -> SkillDefinition:
        if not _SAFE_SKILL_NAME.fullmatch(name):
            raise SkillLoadError(f"Invalid skill name: {name}")

        skill_path = (self.skills_root / name / "SKILL.md").resolve()
        if not skill_path.is_relative_to(self.skills_root):
            raise SkillLoadError(f"Skill path escapes configured root: {name}")
        if not skill_path.is_file():
            raise SkillLoadError(f"Enabled skill is missing: {name}")

        instructions = strip_skill_frontmatter(
            skill_path.read_text(encoding="utf-8")
        )
        if not instructions:
            raise SkillLoadError(f"Enabled skill is empty: {name}")
        return SkillDefinition(
            name=name,
            instructions=instructions,
            source=str(skill_path),
            resources_root=skill_path.parent,
        )


def _validated_resource_path(rel: str) -> PurePosixPath:
    try:
        rel_path = PurePosixPath(rel)
    except TypeError as exc:
        raise SkillResourceError("Skill resource path is invalid") from exc
    if (
        not rel
        or rel_path.is_absolute()
        or any(part in {"", ".", ".."} for part in rel_path.parts)
        or len(rel_path.parts) < 2
        or rel_path.parts[0] not in ALLOWED_SKILL_RESOURCE_DIRS
    ):
        raise SkillResourceError("Skill resource path is invalid")
    return rel_path


def _reject_symlink_path(root: Path, candidate: Path) -> None:
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise SkillResourceError("Skill resource path is unsafe")
