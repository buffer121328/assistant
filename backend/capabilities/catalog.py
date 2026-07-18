from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agent.skill_management.store import ManagedSkillStore
    from agent.tool_management import ToolCatalogSnapshot


_SAFE_SKILL_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_SAFE_CAPABILITY_ID = re.compile(
    r"^[a-z][a-z0-9]*(?:[.-][a-z0-9][a-z0-9-]*)+$"
)
_MAX_SKILL_METADATA_BYTES = 16 * 1024

CapabilityLoader = Callable[[], object]
CapabilityRiskLevel = Literal["L1", "L2", "L3"]


class CapabilityKind(str, Enum):
    CODE = "code"
    AGENT_PROFILE = "agent_profile"
    SKILL = "skill"
    TOOL = "tool"


class CapabilityRegistryError(Exception):
    pass


class DuplicateCapabilityError(CapabilityRegistryError):
    pass


class CapabilityNotFoundError(CapabilityRegistryError):
    pass


class CapabilityDisabledError(CapabilityRegistryError):
    pass


class CapabilityLoaderMissingError(CapabilityRegistryError):
    pass


class CapabilityLoadError(CapabilityRegistryError):
    pass


@dataclass(frozen=True)
class CapabilityMetadata:
    id: str
    kind: CapabilityKind
    display_name: str
    summary: str
    source: str
    enabled: bool
    risk_level: CapabilityRiskLevel
    requires_approval: bool

    def __post_init__(self) -> None:
        if not _SAFE_CAPABILITY_ID.fullmatch(self.id):
            raise ValueError(f"Invalid capability id: {self.id}")
        if not self.display_name.strip():
            raise ValueError("Capability display name must not be empty")
        if not self.summary.strip():
            raise ValueError("Capability summary must not be empty")
        if not self.source.strip():
            raise ValueError("Capability source must not be empty")


class CapabilityRegistry:
    def __init__(self) -> None:
        self._metadata: dict[str, CapabilityMetadata] = {}
        self._loaders: dict[str, CapabilityLoader] = {}
        self._resolved: dict[str, object] = {}
        self._revision = 0
        self._tool_snapshot_revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def tool_snapshot_revision(self) -> int:
        return self._tool_snapshot_revision

    def register(
        self,
        metadata: CapabilityMetadata,
        *,
        loader: CapabilityLoader | None = None,
    ) -> None:
        if metadata.id in self._metadata:
            raise DuplicateCapabilityError(
                f"Capability is already registered: {metadata.id}"
            )
        self._metadata[metadata.id] = metadata
        if loader is not None:
            self._loaders[metadata.id] = loader
        self._revision += 1
        self._resolved.clear()

    def list(
        self,
        *,
        kind: CapabilityKind | None = None,
        enabled: bool | None = None,
    ) -> tuple[CapabilityMetadata, ...]:
        return tuple(
            metadata
            for metadata in sorted(self._metadata.values(), key=lambda item: item.id)
            if (kind is None or metadata.kind is kind)
            and (enabled is None or metadata.enabled is enabled)
        )

    def get(self, capability_id: str) -> CapabilityMetadata:
        try:
            return self._metadata[capability_id]
        except KeyError as exc:
            raise CapabilityNotFoundError(
                f"Capability is not registered: {capability_id}"
            ) from exc

    def resolve(self, capability_id: str) -> object:
        metadata = self.get(capability_id)
        if not metadata.enabled:
            raise CapabilityDisabledError(
                f"Capability is disabled: {capability_id}"
            )
        if capability_id in self._resolved:
            return self._resolved[capability_id]
        try:
            loader = self._loaders[capability_id]
        except KeyError as exc:
            raise CapabilityLoaderMissingError(
                f"Capability has no implementation loader: {capability_id}"
            ) from exc
        try:
            instance = loader()
        except Exception as exc:
            raise CapabilityLoadError(
                f"Capability implementation failed to load: {capability_id}"
            ) from exc
        self._resolved[capability_id] = instance
        return instance

    def replace_tool_projection(self, snapshot: ToolCatalogSnapshot) -> None:
        next_metadata = {
            capability_id: metadata
            for capability_id, metadata in self._metadata.items()
            if metadata.kind is not CapabilityKind.TOOL
        }
        next_loaders = {
            capability_id: loader
            for capability_id, loader in self._loaders.items()
            if self._metadata[capability_id].kind is not CapabilityKind.TOOL
        }
        for descriptor in snapshot.descriptors:
            capability_id = f"tool.{descriptor.name}"
            if capability_id in next_metadata:
                raise DuplicateCapabilityError(
                    f"Capability is already registered: {capability_id}"
                )
            next_metadata[capability_id] = CapabilityMetadata(
                id=capability_id,
                kind=CapabilityKind.TOOL,
                display_name=descriptor.name,
                summary=descriptor.description,
                source=f"{descriptor.source_kind}:{descriptor.source_id}",
                enabled=(descriptor.enabled and snapshot.is_available(descriptor)),
                risk_level=descriptor.risk_level,
                requires_approval=descriptor.requires_approval,
            )
        self._metadata = next_metadata
        self._loaders = next_loaders
        self._resolved.clear()
        self._tool_snapshot_revision = snapshot.revision
        self._revision += 1


def discover_skill_metadata(skills_root: Path) -> tuple[CapabilityMetadata, ...]:
    try:
        root = skills_root.resolve(strict=True)
    except (FileNotFoundError, OSError):
        return ()
    if not root.is_dir():
        return ()

    discovered: list[CapabilityMetadata] = []
    for skill_dir in sorted(root.iterdir(), key=lambda path: path.name):
        if (
            skill_dir.is_symlink()
            or not skill_dir.is_dir()
            or not _SAFE_SKILL_NAME.fullmatch(skill_dir.name)
        ):
            continue
        skill_file = skill_dir / "SKILL.md"
        if skill_file.is_symlink() or not skill_file.is_file():
            continue
        try:
            resolved_file = skill_file.resolve(strict=True)
            if not resolved_file.is_relative_to(root):
                continue
            if resolved_file.stat().st_size > _MAX_SKILL_METADATA_BYTES:
                continue
            content = resolved_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        parsed = _parse_skill_metadata(content)
        if parsed is None:
            continue
        display_name, summary = parsed
        discovered.append(
            CapabilityMetadata(
                id=f"skill.{skill_dir.name}",
                kind=CapabilityKind.SKILL,
                display_name=display_name,
                summary=summary,
                source="builtin",
                enabled=True,
                risk_level="L1",
                requires_approval=False,
            )
        )
    return tuple(discovered)


def build_default_registry(
    skills_root: Path,
    *,
    loaders: Mapping[str, CapabilityLoader] | None = None,
    managed_store: ManagedSkillStore | None = None,
    tool_snapshot: ToolCatalogSnapshot | None = None,
) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    loader_map = loaders or {}
    builtin_skills = discover_skill_metadata(skills_root)
    for metadata in (*_builtin_metadata(), *builtin_skills):
        loader = loader_map.get(metadata.id)
        if loader is None and metadata.kind is CapabilityKind.SKILL:
            skill_name = metadata.id.removeprefix("skill.")

            def load_builtin(name: str = skill_name) -> object:
                return _load_builtin_skill(skills_root, name)

            loader = load_builtin
        registry.register(metadata, loader=loader)

    if managed_store is not None:
        registered_ids = {metadata.id for metadata in registry.list()}
        for record in managed_store.list_managed():
            capability_id = f"skill.{record.name}"
            if capability_id in registered_ids:
                continue
            loader = loader_map.get(capability_id)
            if loader is None:

                def load_managed(name: str = record.name) -> object:
                    return managed_store.load(name)

                loader = load_managed
            registry.register(
                CapabilityMetadata(
                    id=capability_id,
                    kind=CapabilityKind.SKILL,
                    display_name=record.display_name,
                    summary=record.summary,
                    source="managed",
                    enabled=record.enabled,
                    risk_level="L1",
                    requires_approval=False,
                ),
                loader=loader,
            )
            registered_ids.add(capability_id)
    if tool_snapshot is not None:
        registry.replace_tool_projection(tool_snapshot)
    return registry


def _load_builtin_skill(skills_root: Path, name: str) -> object:
    from agent.skill_management import SkillsLoader

    return SkillsLoader(skills_root).load((name,))[0]


def _parse_skill_metadata(content: str) -> tuple[str, str] | None:
    lines = content.splitlines()
    first_index = next(
        (index for index, line in enumerate(lines) if line.strip()), None
    )
    if first_index is None:
        return None
    heading = lines[first_index].strip()
    if not heading.startswith("# "):
        return None
    display_name = heading[2:].strip()
    if not display_name:
        return None
    summary = next(
        (
            line.strip()
            for line in lines[first_index + 1 :]
            if line.strip() and not line.lstrip().startswith("#")
        ),
        "",
    )
    if not summary:
        return None
    return display_name[:120], summary[:500]


def _builtin_metadata() -> tuple[CapabilityMetadata, ...]:
    definitions: tuple[
        tuple[
            str,
            CapabilityKind,
            str,
            str,
            CapabilityRiskLevel,
            bool,
        ],
        ...,
    ] = (
        (
            "code.memory",
            CapabilityKind.CODE,
            "Memory",
            "Store, list, and remove user-owned assistant memory.",
            "L1",
            False,
        ),
        (
            "code.status",
            CapabilityKind.CODE,
            "Task Status",
            "Read the current user's task status without model execution.",
            "L1",
            False,
        ),
        (
            "profile.plan",
            CapabilityKind.AGENT_PROFILE,
            "Planner",
            "Create structured plans through the current Agent Harness.",
            "L1",
            False,
        ),
        (
            "profile.learn",
            CapabilityKind.AGENT_PROFILE,
            "Researcher",
            "Research a topic with source-aware output.",
            "L2",
            False,
        ),
        (
            "profile.daily",
            CapabilityKind.AGENT_PROFILE,
            "Daily Reporter",
            "Produce a concise sourced daily report.",
            "L2",
            False,
        ),
        (
            "profile.office",
            CapabilityKind.AGENT_PROFILE,
            "Office Writer",
            "Produce structured office text from supplied material.",
            "L1",
            False,
        ),
        (
            "tool.search.web",
            CapabilityKind.TOOL,
            "Web Search",
            "Search public web sources through the existing Tool Registry.",
            "L2",
            False,
        ),
    )
    return tuple(
        CapabilityMetadata(
            id=capability_id,
            kind=kind,
            display_name=display_name,
            summary=summary,
            source="builtin",
            enabled=True,
            risk_level=risk_level,
            requires_approval=requires_approval,
        )
        for (
            capability_id,
            kind,
            display_name,
            summary,
            risk_level,
            requires_approval,
        ) in definitions
    )
