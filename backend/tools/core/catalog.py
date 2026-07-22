from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import re
from types import MappingProxyType
from typing import Any, Literal, Protocol, cast

from common.redaction import sanitize_text

from tools.core.registry import ToolRiskLevel


ToolSourceKind = Literal["builtin", "mcp"]

_SAFE_TOOL_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9][a-z0-9-]*)*$")
_SAFE_SOURCE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_SAFE_TAG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_DESCRIPTION_LENGTH = 500
_MAX_SCHEMA_BYTES = 16 * 1024
_MAX_VERSION_LENGTH = 128


class ToolDescriptorError(ValueError):
    """表示 处理 tool descriptor error 的后端数据结构或服务对象。"""

    pass


@dataclass(frozen=True)
class ToolDescriptor:
    """表示 处理 tool descriptor 的后端数据结构或服务对象。"""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    source_id: str
    source_kind: ToolSourceKind
    version: str
    enabled: bool
    risk_level: ToolRiskLevel
    requires_approval: bool
    tags: tuple[str, ...] = ()
    always_available: bool = False
    parallel_safe: bool = False

    def __post_init__(self) -> None:
        """完成数据类初始化后的补充处理。"""
        name = self.name.strip()
        description = self.description.strip()
        source_id = self.source_id.strip()
        version = self.version.strip()
        if not _SAFE_TOOL_NAME.fullmatch(name):
            raise ToolDescriptorError(f"Invalid tool name: {name}")
        if not description or len(description) > _MAX_DESCRIPTION_LENGTH:
            raise ToolDescriptorError("Tool description is empty or too long")
        if not _SAFE_SOURCE_ID.fullmatch(source_id):
            raise ToolDescriptorError(f"Invalid tool source id: {source_id}")
        if self.source_kind not in {"builtin", "mcp"}:
            raise ToolDescriptorError(f"Invalid tool source kind: {self.source_kind}")
        if not version or len(version) > _MAX_VERSION_LENGTH:
            raise ToolDescriptorError("Tool version is empty or too long")
        tags = tuple(dict.fromkeys(tag.strip() for tag in self.tags))
        if any(not _SAFE_TAG.fullmatch(tag) for tag in tags):
            raise ToolDescriptorError("Tool tags contain an invalid value")

        schema = _normalize_schema(self.input_schema)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "tags", tags)
        object.__setattr__(self, "input_schema", _freeze_json(schema))

    def function_schema(self) -> dict[str, Any]:
        """处理 function schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": _thaw_json(self.input_schema),
            },
        }

    def content_fingerprint(self) -> str:
        """处理 content fingerprint。"""
        payload = {
            "name": self.name,
            "description": self.description,
            "input_schema": _thaw_json(self.input_schema),
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "version": self.version,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "tags": self.tags,
            "always_available": self.always_available,
            "parallel_safe": self.parallel_safe,
        }
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


@dataclass(frozen=True)
class ToolSourceStatus:
    """表示 处理 tool source status 的后端数据结构或服务对象。"""

    source_id: str
    source_kind: ToolSourceKind
    available: bool
    error: str | None = None


@dataclass(frozen=True)
class ToolCatalogDiff:
    """表示 处理 tool catalog diff 的后端数据结构或服务对象。"""

    added: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCatalogSnapshot:
    """表示 处理 tool catalog snapshot 的后端数据结构或服务对象。"""

    revision: int
    descriptors: tuple[ToolDescriptor, ...]
    sources: tuple[ToolSourceStatus, ...]
    diff: ToolCatalogDiff = field(default_factory=ToolCatalogDiff)

    def __post_init__(self) -> None:
        """完成数据类初始化后的补充处理。"""
        descriptors = tuple(sorted(self.descriptors, key=lambda item: item.name))
        sources = tuple(sorted(self.sources, key=lambda item: item.source_id))
        if len({item.name for item in descriptors}) != len(descriptors):
            raise ToolDescriptorError("Tool snapshot contains duplicate names")
        if len({item.source_id for item in sources}) != len(sources):
            raise ToolDescriptorError("Tool snapshot contains duplicate sources")
        object.__setattr__(self, "descriptors", descriptors)
        object.__setattr__(self, "sources", sources)

    def get(self, name: str) -> ToolDescriptor | None:
        """获取。

        Args:
            name: name 参数。
        """
        return next((item for item in self.descriptors if item.name == name), None)

    def source_status(self, source_id: str) -> ToolSourceStatus:
        """处理 source status。

        Args:
            source_id: source_id 参数。
        """
        status = next(
            (item for item in self.sources if item.source_id == source_id),
            None,
        )
        if status is None:
            raise KeyError(f"Unknown tool source: {source_id}")
        return status

    def is_available(self, descriptor: ToolDescriptor) -> bool:
        """处理 is available。

        Args:
            descriptor: descriptor 参数。
        """
        try:
            return self.source_status(descriptor.source_id).available
        except KeyError:
            return False


@dataclass(frozen=True)
class ToolRefreshAudit:
    """表示 处理 tool refresh audit 的后端数据结构或服务对象。"""

    revision: int
    sources: tuple[ToolSourceStatus, ...]
    diff: ToolCatalogDiff


class ToolSource(Protocol):
    """表示 处理 tool source 的后端数据结构或服务对象。"""

    source_id: str
    source_kind: ToolSourceKind

    async def discover(self) -> Sequence[ToolDescriptor]:
        """处理 discover。"""
        ...


class StaticToolSource:
    """表示 处理 static tool source 的后端数据结构或服务对象。"""

    source_kind: ToolSourceKind = "builtin"

    def __init__(
        self,
        source_id: str,
        descriptors: Sequence[ToolDescriptor],
    ) -> None:
        """初始化对象实例。

        Args:
            source_id: source_id 参数。
            descriptors: descriptors 参数。
        """
        self.source_id = source_id
        self._descriptors = tuple(descriptors)

    async def discover(self) -> tuple[ToolDescriptor, ...]:
        """处理 discover。"""
        return self._descriptors


class ToolCatalog:
    """表示 处理 tool catalog 的后端数据结构或服务对象。"""

    def __init__(
        self,
        sources: Sequence[ToolSource],
        *,
        sensitive_values: Sequence[str | None] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            sources: sources 参数。
            sensitive_values: sensitive_values 参数。
        """
        self._sources = tuple(sources)
        self._sensitive_values = tuple(sensitive_values)
        self._current = self.snapshot(revision=0)
        self._audit_events: list[ToolRefreshAudit] = []

    @property
    def current(self) -> ToolCatalogSnapshot:
        """处理 current。"""
        return self._current

    @property
    def audit_events(self) -> tuple[ToolRefreshAudit, ...]:
        """处理 audit events。"""
        return tuple(self._audit_events)

    @staticmethod
    def snapshot(
        *,
        revision: int,
        descriptors: Sequence[ToolDescriptor] = (),
        sources: Sequence[ToolSourceStatus] = (),
        diff: ToolCatalogDiff | None = None,
    ) -> ToolCatalogSnapshot:
        """处理 snapshot。

        Args:
            revision: revision 参数。
            descriptors: descriptors 参数。
            sources: sources 参数。
            diff: diff 参数。
        """
        return ToolCatalogSnapshot(
            revision=revision,
            descriptors=tuple(descriptors),
            sources=tuple(sources),
            diff=diff or ToolCatalogDiff(),
        )

    async def refresh(self) -> ToolCatalogSnapshot:
        """处理 refresh。"""
        previous = self._current
        previous_by_source: dict[str, tuple[ToolDescriptor, ...]] = {}
        for item in previous.descriptors:
            previous_by_source.setdefault(item.source_id, ())
            previous_by_source[item.source_id] += (item,)

        next_by_name: dict[str, ToolDescriptor] = {}
        statuses: list[ToolSourceStatus] = []
        for source in self._sources:
            issues: list[str] = []
            try:
                discovered = tuple(await source.discover())
            except Exception as exc:
                safe_error = self._safe_error(exc)
                statuses.append(
                    ToolSourceStatus(
                        source_id=source.source_id,
                        source_kind=source.source_kind,
                        available=False,
                        error=safe_error,
                    )
                )
                for descriptor in previous_by_source.get(source.source_id, ()):
                    next_by_name.setdefault(descriptor.name, descriptor)
                continue

            source_names: set[str] = set()
            for descriptor in discovered:
                if not isinstance(descriptor, ToolDescriptor):
                    issues.append("invalid descriptor type")
                    continue
                if (
                    descriptor.source_id != source.source_id
                    or descriptor.source_kind != source.source_kind
                ):
                    issues.append(f"source mismatch for {descriptor.name}")
                    continue
                if descriptor.name in source_names or descriptor.name in next_by_name:
                    issues.append(f"duplicate tool name: {descriptor.name}")
                    continue
                source_names.add(descriptor.name)
                next_by_name[descriptor.name] = descriptor

            statuses.append(
                ToolSourceStatus(
                    source_id=source.source_id,
                    source_kind=source.source_kind,
                    available=True,
                    error=("; ".join(issues) if issues else None),
                )
            )

        revision = previous.revision + 1
        descriptors = tuple(sorted(next_by_name.values(), key=lambda item: item.name))
        status_tuple = tuple(sorted(statuses, key=lambda item: item.source_id))
        diff = _build_diff(previous, descriptors, status_tuple)
        snapshot = self.snapshot(
            revision=revision,
            descriptors=descriptors,
            sources=status_tuple,
            diff=diff,
        )
        self._current = snapshot
        self._audit_events.append(
            ToolRefreshAudit(revision=revision, sources=status_tuple, diff=diff)
        )
        return snapshot

    def _safe_error(self, value: object) -> str:
        """执行 处理 safe error 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        text = sanitize_text(value, extra_sensitive_values=self._sensitive_values)
        if "traceback" in text.lower():
            return "内部错误已脱敏"
        return text[:1000]


@dataclass(frozen=True)
class ToolSelectionResult:
    """表示 处理 tool selection result 的后端数据结构或服务对象。"""

    allowed_tools: tuple[str, ...]
    approval_required_tools: tuple[str, ...]
    reasons: tuple[tuple[str, str], ...]
    versions: tuple[tuple[str, str], ...]
    snapshot_revision: int

    @property
    def names(self) -> tuple[str, ...]:
        """处理 names。"""
        return self.allowed_tools + self.approval_required_tools


class ToolCandidateSelector:
    """表示 处理 tool candidate selector 的后端数据结构或服务对象。"""

    def select(
        self,
        snapshot: ToolCatalogSnapshot,
        *,
        task_type: str,
        profile_name: str,
        skill_names: Sequence[str],
        requested_tools: Sequence[str],
        core_tools: Sequence[str] = (),
        budget: int,
    ) -> ToolSelectionResult:
        """选择。

        Args:
            snapshot: snapshot 参数。
            task_type: task_type 参数。
            profile_name: profile_name 参数。
            skill_names: skill_names 参数。
            requested_tools: requested_tools 参数。
            core_tools: core_tools 参数。
            budget: budget 参数。
        """
        limit = max(0, budget)
        eligible: dict[str, ToolDescriptor] = {
            item.name: item
            for item in snapshot.descriptors
            if item.enabled and snapshot.is_available(item)
        }
        matched_tags = {task_type, profile_name, *skill_names}
        ordered: list[tuple[ToolDescriptor, str]] = []
        seen: set[str] = set()

        for name in core_tools:
            item = eligible.get(name)
            if item is None or name in seen:
                continue
            if not item.always_available and name not in core_tools:
                continue
            ordered.append((item, "core"))
            seen.add(name)

        for name in requested_tools:
            item = eligible.get(name)
            if item is None or name in seen:
                continue
            tag_matches = sorted(matched_tags.intersection(item.tags))
            if item.tags and not tag_matches:
                continue
            reason = "explicit_request"
            if task_type in tag_matches:
                reason += "+task"
            elif profile_name in tag_matches:
                reason += "+profile"
            elif tag_matches:
                reason += "+skill"
            ordered.append((item, reason))
            seen.add(name)

        selected = ordered[:limit]
        allowed = tuple(item.name for item, _ in selected if not item.requires_approval)
        approval_required = tuple(
            item.name for item, _ in selected if item.requires_approval
        )
        return ToolSelectionResult(
            allowed_tools=allowed,
            approval_required_tools=approval_required,
            reasons=tuple((item.name, reason) for item, reason in selected),
            versions=tuple((item.name, item.version) for item, _ in selected),
            snapshot_revision=snapshot.revision,
        )


def build_planned_tool_schemas(
    snapshot: ToolCatalogSnapshot,
    *,
    allowed_tools: Sequence[str],
    approval_required_tools: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    """构建 planned tool schemas。

    Args:
        snapshot: snapshot 参数。
        allowed_tools: allowed_tools 参数。
        approval_required_tools: approval_required_tools 参数。
    """
    schemas: list[dict[str, Any]] = []
    for name in dict.fromkeys((*allowed_tools, *approval_required_tools)):
        descriptor = snapshot.get(name)
        if (
            descriptor is None
            or not descriptor.enabled
            or not snapshot.is_available(descriptor)
        ):
            continue
        schemas.append(descriptor.function_schema())
    return tuple(schemas)


def build_search_tool_descriptor(*, enabled: bool = True) -> ToolDescriptor:
    """构建 search tool descriptor。

    Args:
        enabled: enabled 参数。
    """
    return ToolDescriptor(
        name="search.web",
        description="Search public web sources",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Public web search query",
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        source_id="builtin",
        source_kind="builtin",
        version="builtin-search-v1",
        enabled=enabled,
        risk_level="L2",
        requires_approval=False,
        tags=("learn", "daily", "v2.researcher", "v2.daily_reporter"),
        parallel_safe=False,
    )


def _normalize_schema(value: Mapping[str, Any]) -> dict[str, Any]:
    """执行 规范化 schema 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
        schema = cast(dict[str, Any], json.loads(serialized))
    except (TypeError, ValueError) as exc:
        raise ToolDescriptorError(
            "Tool input schema must be JSON serializable"
        ) from exc
    if len(serialized.encode("utf-8")) > _MAX_SCHEMA_BYTES:
        raise ToolDescriptorError("Tool input schema is too large")
    if schema.get("type") != "object":
        raise ToolDescriptorError("Tool input schema type must be object")
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ToolDescriptorError("Tool input schema properties must be an object")
    if schema.get("additionalProperties") is not False:
        raise ToolDescriptorError("Tool input schema must reject additional properties")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(
        not isinstance(name, str) or name not in properties for name in required
    ):
        raise ToolDescriptorError("Tool input schema required fields are invalid")
    return schema


def _freeze_json(value: Any) -> Any:
    """执行 处理 freeze json 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """执行 处理 thaw json 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _build_diff(
    previous: ToolCatalogSnapshot,
    current: Sequence[ToolDescriptor],
    statuses: Sequence[ToolSourceStatus],
) -> ToolCatalogDiff:
    """执行 构建 diff 的内部辅助逻辑。

    Args:
        previous: previous 参数。
        current: current 参数。
        statuses: statuses 参数。
    """
    old = {item.name: item for item in previous.descriptors}
    new = {item.name: item for item in current}
    old_status = {item.source_id: item for item in previous.sources}
    new_status = {item.source_id: item for item in statuses}
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    disabled: list[str] = []
    updated: list[str] = []
    for name in sorted(old.keys() & new.keys()):
        before = old[name]
        after = new[name]
        became_unavailable = (
            old_status.get(
                after.source_id,
                ToolSourceStatus(after.source_id, after.source_kind, True),
            ).available
            and not new_status.get(
                after.source_id,
                ToolSourceStatus(after.source_id, after.source_kind, False),
            ).available
        )
        if (before.enabled and not after.enabled) or became_unavailable:
            disabled.append(name)
        if before.content_fingerprint() != after.content_fingerprint():
            updated.append(name)
    return ToolCatalogDiff(
        added=tuple(added),
        updated=tuple(updated),
        disabled=tuple(disabled),
        removed=tuple(removed),
    )
