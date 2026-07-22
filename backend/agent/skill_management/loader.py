from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re


_SAFE_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
ALLOWED_SKILL_RESOURCE_DIRS = frozenset({"data", "templates"})
MAX_SKILL_RESOURCE_BYTES = 64 * 1024


class SkillLoadError(Exception):
    """表示 处理 skill load error 的后端数据结构或服务对象。"""

    pass


class SkillResourceError(Exception):
    """表示 处理 skill resource error 的后端数据结构或服务对象。"""

    pass


_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*(?:\r?\n)(.*?)(?:\r?\n)---[ \t]*(?:\r?\n|\Z)",
    re.DOTALL,
)


def strip_skill_frontmatter(content: str) -> str:
    """处理 strip skill frontmatter。

    Args:
        content: content 参数。
    """
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return content.strip()
    return content[match.end() :].strip()


@dataclass(frozen=True)
class SkillDefinition:
    """表示 处理 skill definition 的后端数据结构或服务对象。"""

    name: str
    instructions: str
    source: str
    resources_root: Path | None = None

    def resource(self, rel: str) -> str:
        """处理 resource。

        Args:
            rel: rel 参数。
        """
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
        """处理 summary。"""
        for line in self.instructions.splitlines():
            normalized = line.strip().lstrip("#").strip()
            if normalized:
                return normalized
        return self.name


class SkillsLoader:
    """表示 处理 skills loader 的后端数据结构或服务对象。"""

    def __init__(self, skills_root: Path) -> None:
        """初始化对象实例。

        Args:
            skills_root: skills_root 参数。
        """
        self.skills_root = skills_root.resolve()

    def load(self, enabled_names: tuple[str, ...]) -> tuple[SkillDefinition, ...]:
        """加载。

        Args:
            enabled_names: enabled_names 参数。
        """
        loaded: list[SkillDefinition] = []
        seen: set[str] = set()
        for name in enabled_names:
            if name in seen:
                continue
            seen.add(name)
            loaded.append(self._load_one(name))
        return tuple(loaded)

    def _load_one(self, name: str) -> SkillDefinition:
        """执行 加载 one 的内部辅助逻辑。

        Args:
            name: name 参数。
        """
        if not _SAFE_SKILL_NAME.fullmatch(name):
            raise SkillLoadError(f"Invalid skill name: {name}")

        skill_path = (self.skills_root / name / "SKILL.md").resolve()
        if not skill_path.is_relative_to(self.skills_root):
            raise SkillLoadError(f"Skill path escapes configured root: {name}")
        if not skill_path.is_file():
            raise SkillLoadError(f"Enabled skill is missing: {name}")

        instructions = strip_skill_frontmatter(skill_path.read_text(encoding="utf-8"))
        if not instructions:
            raise SkillLoadError(f"Enabled skill is empty: {name}")
        return SkillDefinition(
            name=name,
            instructions=instructions,
            source=str(skill_path),
            resources_root=skill_path.parent,
        )


def _validated_resource_path(rel: str) -> PurePosixPath:
    """执行 处理 validated resource path 的内部辅助逻辑。

    Args:
        rel: rel 参数。
    """
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
    """执行 拒绝 symlink path 的内部辅助逻辑。

    Args:
        root: root 参数。
        candidate: candidate 参数。
    """
    current = root
    for part in candidate.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise SkillResourceError("Skill resource path is unsafe")
