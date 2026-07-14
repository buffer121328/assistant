from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_SAFE_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


class SkillLoadError(Exception):
    pass


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    instructions: str
    source: str

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

        instructions = skill_path.read_text(encoding="utf-8").strip()
        if not instructions:
            raise SkillLoadError(f"Enabled skill is empty: {name}")
        return SkillDefinition(
            name=name,
            instructions=instructions,
            source=str(skill_path),
        )
