from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import Task, TaskContextSnapshot, utc_now
from domain.policies.enterprise import GovernedAgentProfile, GovernanceValidationError

_MAX_SCOPE_ITEMS = 256
_MAX_SCOPE_VALUE_LENGTH = 512


class TaskContextSnapshotNotFoundError(LookupError):
    """定义当前组件可安全处理的错误类型。"""


@dataclass(frozen=True)
class TaskContextSnapshotView:
    """定义当前组件的职责和边界。"""

    id: str  # id 对应的数据字段。
    task_id: str  # task_id 对应的数据字段。
    conversation_id: str | None  # conversation_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    tenant_id: str  # tenant_id 对应的数据字段。
    organization_id: str | None  # organization_id 对应的数据字段。
    owner_type: str  # owner_type 对应的数据字段。
    owner_id: str  # owner_id 对应的数据字段。
    visibility: str  # visibility 对应的数据字段。
    command_id: str | None  # command_id 对应的数据字段。
    state: str  # state 对应的数据字段。
    resource_reference_ids: tuple[str, ...]  # resource_reference_ids 对应的数据字段。
    resolved_resource_versions: dict[str, str]  # resolved_resource_versions 对应的数据字段。
    memory_scope_snapshot: tuple[str, ...]  # memory_scope_snapshot 对应的数据字段。
    knowledge_scope_snapshot: tuple[str, ...]  # knowledge_scope_snapshot 对应的数据字段。
    capability_snapshot: tuple[str, ...]  # capability_snapshot 对应的数据字段。
    agent_profile_schema_version: str | None  # agent_profile_schema_version 对应的数据字段。
    agent_profile_snapshot: str | None  # agent_profile_snapshot 对应的数据字段。
    created_at: datetime  # created_at 对应的数据字段。
    finalized_at: datetime | None  # finalized_at 对应的数据字段。


class TaskContextSnapshotService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Bind snapshot operations to the caller's existing database transaction.

        Args:
            session: 当前数据库异步会话。
        """
        self.session = session

    async def create_initial(
        self, task: Task, *, command_id: str | None = None
    ) -> TaskContextSnapshot:
        """Create an initial snapshot solely from trusted persisted Task fields.

        Args:
            task: 需要处理的任务对象。
            command_id: 用于执行当前操作的 command id 参数。
        """
        existing = await self.session.scalar(
            select(TaskContextSnapshot).where(TaskContextSnapshot.task_id == task.id)
        )
        if existing is not None:
            self._assert_identity(existing, task)
            return existing
        snapshot = TaskContextSnapshot(
            task_id=task.id,
            conversation_id=task.conversation_id,
            user_id=task.user_id,
            tenant_id=task.tenant_id,
            organization_id=task.organization_id,
            owner_type=task.owner_type,
            owner_id=task.owner_id or task.user_id,
            visibility=task.visibility,
            command_id=command_id,
            state="initial",
            resource_reference_ids_json="[]",
            resolved_resource_versions_json="{}",
            memory_scope_snapshot_json="[]",
            knowledge_scope_snapshot_json="[]",
            capability_snapshot_json="[]",
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def finalize(
        self, *, task: Task, profile: GovernedAgentProfile
    ) -> TaskContextSnapshotView:
        """Finalize the first governed context atomically and idempotently.

        Args:
            task: 需要处理的任务对象。
            profile: 用于执行当前操作的 profile 参数。
        """
        if (
            profile.subject.user_id != task.user_id
            or profile.subject.tenant_id != task.tenant_id
        ):
            raise GovernanceValidationError(
                "Task context profile identity is inconsistent"
            )
        snapshot = await self.create_initial(task)
        if snapshot.state == "finalized":
            return self._view(snapshot)
        snapshot.state = "finalized"
        snapshot.capability_snapshot_json = _encode_string_list(profile.capabilities)
        snapshot.knowledge_scope_snapshot_json = _encode_string_list(
            profile.knowledge_scopes
        )
        snapshot.agent_profile_schema_version = (
            task.agent_profile_schema_version or profile.schema_version
        )
        snapshot.agent_profile_snapshot = (
            task.agent_profile_snapshot or profile.to_snapshot()
        )
        snapshot.finalized_at = utc_now()
        await self.session.flush()
        return self._view(snapshot)

    async def bind_resources(
        self,
        *,
        task: Task,
        resource_versions: tuple[tuple[str, str], ...],
    ) -> TaskContextSnapshotView:
        """Bind resolved resource IDs and versions before profile finalization.

        Args:
            task: 需要处理的任务对象。
            resource_versions: 用于执行当前操作的 resource versions 参数。
        """
        snapshot = await self.create_initial(task)
        normalized = tuple(
            dict.fromkeys(
                (reference_id.strip(), version.strip())
                for reference_id, version in resource_versions
            )
        )
        if any(not reference_id or not version for reference_id, version in normalized):
            raise GovernanceValidationError("Task context resource binding is invalid")
        desired_ids = tuple(reference_id for reference_id, _version in normalized)
        desired_versions = dict(normalized)
        current = self._view(snapshot)
        if current.resource_reference_ids or current.resolved_resource_versions:
            if (
                current.resource_reference_ids == desired_ids
                and current.resolved_resource_versions == desired_versions
            ):
                return current
            raise GovernanceValidationError("Task context resources are already bound")
        if snapshot.state != "initial":
            raise GovernanceValidationError(
                "Finalized Task context cannot bind new resources"
            )
        snapshot.resource_reference_ids_json = _encode_string_list(desired_ids)
        snapshot.resolved_resource_versions_json = _encode_string_map(desired_versions)
        await self.session.flush()
        return self._view(snapshot)

    async def get_owned(
        self, *, task_id: str, user_id: str
    ) -> TaskContextSnapshotView:
        """Return a bounded snapshot only when both Task and snapshot are owned.

        Args:
            task_id: 目标任务 ID。
            user_id: 目标用户 ID。
        """
        snapshot = await self.session.scalar(
            select(TaskContextSnapshot)
            .join(Task, Task.id == TaskContextSnapshot.task_id)
            .where(
                Task.id == task_id,
                Task.user_id == user_id,
                TaskContextSnapshot.user_id == user_id,
            )
        )
        if snapshot is None:
            raise TaskContextSnapshotNotFoundError("Task context snapshot not found")
        return self._view(snapshot)

    @staticmethod
    def _assert_identity(snapshot: TaskContextSnapshot, task: Task) -> None:
        """Reject an existing snapshot whose trusted identity no longer matches.

        Args:
            snapshot: 用于执行当前操作的 snapshot 参数。
            task: 需要处理的任务对象。
        """
        if (
            snapshot.task_id != task.id
            or snapshot.conversation_id != task.conversation_id
            or snapshot.user_id != task.user_id
            or snapshot.tenant_id != task.tenant_id
            or snapshot.organization_id != task.organization_id
            or snapshot.owner_type != task.owner_type
            or snapshot.owner_id != (task.owner_id or task.user_id)
            or snapshot.visibility != task.visibility
        ):
            raise GovernanceValidationError("Task context snapshot identity mismatch")

    @staticmethod
    def _view(snapshot: TaskContextSnapshot) -> TaskContextSnapshotView:
        """Decode only bounded string collections from persisted snapshot JSON.

        Args:
            snapshot: 用于执行当前操作的 snapshot 参数。
        """
        return TaskContextSnapshotView(
            id=snapshot.id,
            task_id=snapshot.task_id,
            conversation_id=snapshot.conversation_id,
            user_id=snapshot.user_id,
            tenant_id=snapshot.tenant_id,
            organization_id=snapshot.organization_id,
            owner_type=snapshot.owner_type,
            owner_id=snapshot.owner_id,
            visibility=snapshot.visibility,
            command_id=snapshot.command_id,
            state=snapshot.state,
            resource_reference_ids=_decode_string_list(
                snapshot.resource_reference_ids_json
            ),
            resolved_resource_versions=_decode_string_map(
                snapshot.resolved_resource_versions_json
            ),
            memory_scope_snapshot=_decode_string_list(
                snapshot.memory_scope_snapshot_json
            ),
            knowledge_scope_snapshot=_decode_string_list(
                snapshot.knowledge_scope_snapshot_json
            ),
            capability_snapshot=_decode_string_list(
                snapshot.capability_snapshot_json
            ),
            agent_profile_schema_version=snapshot.agent_profile_schema_version,
            agent_profile_snapshot=snapshot.agent_profile_snapshot,
            created_at=snapshot.created_at,
            finalized_at=snapshot.finalized_at,
        )


def _encode_string_list(values: tuple[str, ...]) -> str:
    """Encode a bounded ordered set of non-empty context identifiers.

    Args:
        values: 用于执行当前操作的 values 参数。
    """
    normalized = tuple(
        dict.fromkeys(
            value.strip()[:_MAX_SCOPE_VALUE_LENGTH]
            for value in values[:_MAX_SCOPE_ITEMS]
            if value.strip()
        )
    )
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _decode_string_list(value: str) -> tuple[str, ...]:
    """Decode a stored list defensively without returning malformed raw content.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, list):
        return ()
    return tuple(
        item[:_MAX_SCOPE_VALUE_LENGTH]
        for item in payload[:_MAX_SCOPE_ITEMS]
        if isinstance(item, str) and item
    )


def _decode_string_map(value: str) -> dict[str, str]:
    """Decode a bounded string map used for future resolved resource versions.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, Mapping):
        return {}
    decoded: dict[str, str] = {}
    for key, item in list(payload.items())[:_MAX_SCOPE_ITEMS]:
        if isinstance(key, str) and isinstance(item, str) and key and item:
            decoded[key[:_MAX_SCOPE_VALUE_LENGTH]] = item[:_MAX_SCOPE_VALUE_LENGTH]
    return decoded


def _encode_string_map(values: Mapping[str, str]) -> str:
    """Encode a bounded ordered string map for resolved resource versions.

    Args:
        values: 用于执行当前操作的 values 参数。
    """
    normalized = {
        key.strip()[:_MAX_SCOPE_VALUE_LENGTH]: item.strip()[:_MAX_SCOPE_VALUE_LENGTH]
        for key, item in list(values.items())[:_MAX_SCOPE_ITEMS]
        if key.strip() and item.strip()
    }
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "TaskContextSnapshotNotFoundError",
    "TaskContextSnapshotService",
    "TaskContextSnapshotView",
]
