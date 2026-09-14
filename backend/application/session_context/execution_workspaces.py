from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.session_context.context_snapshots import (
    TaskContextSnapshotNotFoundError,
    TaskContextSnapshotService,
)
from application.session_context.resource_references import (
    ResourceReferenceError,
    ResourceReferenceService,
)
from domain.models import ArtifactRecord, Conversation, Task
from tools.builtin.workspace import (
    SessionWorkspaceStore,
    WorkspaceSourceChangedError,
    WorkspaceSourceFile,
)

WorkspacePreparationState = Literal["not_created", "active"]
WorkspaceCleanupDecision = Literal["success", "failed", "diagnostic_retained"]
_TERMINAL_STATES = {"success", "failed", "cancelled"}


@dataclass(frozen=True)
class PreparedWorkspaceResource:
    """定义当前组件的职责和边界。"""

    reference_id: str  # reference_id 对应的数据字段。
    version: str  # version 对应的数据字段。
    relative_path: str  # relative_path 对应的数据字段。
    content_hash: str  # content_hash 对应的数据字段。
    size_bytes: int  # size_bytes 对应的数据字段。


@dataclass(frozen=True)
class ExecutionWorkspaceResult:
    """定义当前组件的职责和边界。"""

    state: WorkspacePreparationState  # state 对应的数据字段。
    conversation_id: str | None  # conversation_id 对应的数据字段。
    task_id: str  # task_id 对应的数据字段。
    resources: tuple[PreparedWorkspaceResource, ...]  # resources 对应的数据字段。


class ExecutionWorkspaceService:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        root: Path,
        uploaded_resources_root: Path = Path("var/resource-inputs"),
        artifacts_root: Path = Path("var/artifacts"),
    ) -> None:
        """Bind database authorization and filesystem output to trusted settings.

        Args:
            session: 当前数据库异步会话。
            root: 用于执行当前操作的 root 参数。
            uploaded_resources_root: 用于执行当前操作的 uploaded resources root 参数。
            artifacts_root: 用于执行当前操作的 artifacts root 参数。
        """
        self.session = session
        self.root = root
        self.uploaded_resources_root = uploaded_resources_root
        self.artifacts_root = artifacts_root

    async def prepare(self, task: Task) -> ExecutionWorkspaceResult:
        """Lazily create and populate a workspace only for selected resources.

        Args:
            task: 需要处理的任务对象。
        """
        try:
            snapshot = await TaskContextSnapshotService(self.session).get_owned(
                task_id=task.id,
                user_id=task.user_id,
            )
        except TaskContextSnapshotNotFoundError:
            # Additive migration backfills production Tasks, but legacy imports and
            # narrow tests can still construct a Task without a context row. Such a
            # Task has no authoritative resource selection and therefore stays lazy.
            return ExecutionWorkspaceResult(
                state="not_created",
                conversation_id=task.conversation_id,
                task_id=task.id,
                resources=(),
            )
        if not _workspace_is_needed(snapshot.resource_reference_ids):
            return ExecutionWorkspaceResult(
                state="not_created",
                conversation_id=task.conversation_id,
                task_id=task.id,
                resources=(),
            )
        conversation_id = task.conversation_id
        if conversation_id is None:
            raise ResourceReferenceError("resource_conversation_required", 422)
        await self._lock_conversation(task)
        resources = await ResourceReferenceService(
            self.session,
            uploaded_resources_root=self.uploaded_resources_root,
            artifacts_root=self.artifacts_root,
        ).resolve_for_workspace(
            task=task,
            reference_ids=snapshot.resource_reference_ids,
            expected_versions=snapshot.resolved_resource_versions,
        )
        store = SessionWorkspaceStore(self.root)
        task_workspace = store.activate_task(
            session_id=conversation_id,
            task_id=task.id,
        )
        manifest = store.read_manifest(session_id=conversation_id) or {
            "schema_version": "2",
            "conversation_id": conversation_id,
            "resource_order": [],
            "resources": {},
            "tasks": {},
        }
        resource_order = cast(list[str], manifest["resource_order"])
        resource_items = cast(dict[str, dict[str, object]], manifest["resources"])
        prepared: list[PreparedWorkspaceResource] = []
        for resource in resources:
            workspace_filename = _workspace_filename(
                resource.display_name,
                reference_id=resource.id,
            )
            try:
                materialized = store.materialize_resource_input(
                    session_id=conversation_id,
                    reference_id=resource.id,
                    filename=workspace_filename,
                    source=WorkspaceSourceFile(
                        path=resource.source_path,
                        device=resource.device,
                        inode=resource.inode,
                        size_bytes=resource.size_bytes,
                        modified_ns=resource.modified_ns,
                    ),
                )
            except WorkspaceSourceChangedError as exc:
                raise ResourceReferenceError("resource_reference_stale", 409) from exc
            if resource.id not in resource_order:
                resource_order.append(resource.id)
            resource_items[resource.id] = {
                "version": resource.version,
                "content_hash": materialized.content_hash,
                "size_bytes": materialized.size_bytes,
                "relative_path": materialized.relative_path,
            }
            prepared.append(
                PreparedWorkspaceResource(
                    reference_id=resource.id,
                    version=resource.version,
                    relative_path=materialized.relative_path,
                    content_hash=materialized.content_hash,
                    size_bytes=materialized.size_bytes,
                )
            )
        task_items = cast(dict[str, dict[str, object]], manifest["tasks"])
        previous = task_items.get(task.id, {})
        state = task.status if task.status in _TERMINAL_STATES else "active"
        previous_cleanup_state = previous.get("cleanup_state")
        previous_cleanup_policy = previous.get("cleanup_policy")
        cleanup_state = (
            previous_cleanup_state
            if state in _TERMINAL_STATES
            and previous_cleanup_state
            in {"retaining", "cleaned", "diagnostic_retained"}
            else "retaining"
            if state in _TERMINAL_STATES
            else "not_started"
        )
        cleanup_policy = (
            previous_cleanup_policy
            if state in _TERMINAL_STATES
            and previous_cleanup_policy
            in {"success", "failed", "diagnostic_retained"}
            else _default_cleanup_policy(state)
        )
        task_items[task.id] = {
            "state": state,
            "work_dir": task_workspace.work_dir.relative_to(
                task_workspace.session_root
            ).as_posix(),
            "output_dir": task_workspace.output_dir.relative_to(
                task_workspace.session_root
            ).as_posix(),
            "audit_dir": task_workspace.audit_dir.relative_to(
                task_workspace.session_root
            ).as_posix(),
            "resource_ids": [resource.id for resource in resources],
            "workspace_need": True,
            "artifact_ids": list(
                cast(list[str], previous.get("artifact_ids", []))
            ),
            "cleanup_state": cleanup_state,
            "cleanup_policy": cleanup_policy,
            "diagnostic": {"error_present": bool(task.error_message)},
        }
        store.write_manifest(session_id=conversation_id, payload=manifest)
        return ExecutionWorkspaceResult(
            state="active",
            conversation_id=conversation_id,
            task_id=task.id,
            resources=tuple(prepared),
        )

    async def associate_artifact(
        self,
        task: Task,
        *,
        artifact_id: str,
    ) -> ExecutionWorkspaceResult:
        """Associate one already-governed Artifact ID without copying its bytes.

        Args:
            task: 需要处理的任务对象。
            artifact_id: 目标产物 ID。
        """
        conversation_id = task.conversation_id
        if conversation_id is None:
            return ExecutionWorkspaceResult("not_created", None, task.id, ())
        store = SessionWorkspaceStore(self.root)
        if not store.manifest_exists(session_id=conversation_id):
            return ExecutionWorkspaceResult(
                "not_created", conversation_id, task.id, ()
            )
        await self._lock_conversation(task)
        artifact = await self.session.scalar(
            select(ArtifactRecord).where(
                ArtifactRecord.id == artifact_id,
                ArtifactRecord.task_id == task.id,
                ArtifactRecord.conversation_id == conversation_id,
            )
        )
        if artifact is None:
            raise ResourceReferenceError("artifact_unavailable", 404)
        manifest = store.read_manifest(session_id=conversation_id)
        if manifest is None:
            return ExecutionWorkspaceResult(
                "not_created", conversation_id, task.id, ()
            )
        tasks = cast(dict[str, dict[str, object]], manifest["tasks"])
        task_item = tasks.get(task.id)
        if task_item is None:
            return ExecutionWorkspaceResult(
                "not_created", conversation_id, task.id, ()
            )
        artifact_ids = cast(list[str], task_item["artifact_ids"])
        if artifact_id not in artifact_ids:
            artifact_ids.append(artifact_id)
            store.write_manifest(session_id=conversation_id, payload=manifest)
        return self._result_from_manifest(task=task, manifest=manifest)

    async def cleanup_task(
        self,
        task: Task,
        *,
        decision: WorkspaceCleanupDecision,
    ) -> ExecutionWorkspaceResult:
        """Apply one explicit terminal retention decision to Task-scoped paths.

        Args:
            task: 需要处理的任务对象。
            decision: 当前操作的治理决策。
        """
        if decision not in {"success", "failed", "diagnostic_retained"}:
            raise ResourceReferenceError("workspace_cleanup_decision_invalid", 422)
        conversation_id = task.conversation_id
        if conversation_id is None:
            return ExecutionWorkspaceResult("not_created", None, task.id, ())
        store = SessionWorkspaceStore(self.root)
        if not store.manifest_exists(session_id=conversation_id):
            return ExecutionWorkspaceResult(
                "not_created", conversation_id, task.id, ()
            )
        await self._lock_conversation(task)
        manifest = store.read_manifest(session_id=conversation_id)
        if manifest is None:
            return ExecutionWorkspaceResult(
                "not_created", conversation_id, task.id, ()
            )
        tasks = cast(dict[str, dict[str, object]], manifest["tasks"])
        task_item = tasks.get(task.id)
        if task_item is None:
            return ExecutionWorkspaceResult(
                "not_created", conversation_id, task.id, ()
            )
        terminal_state = task.status
        if terminal_state not in _TERMINAL_STATES:
            raise ResourceReferenceError("workspace_cleanup_requires_terminal_task", 409)
        if decision == "success" and terminal_state != "success":
            raise ResourceReferenceError("workspace_cleanup_decision_invalid", 422)
        if decision != "success" and terminal_state == "success":
            raise ResourceReferenceError("workspace_cleanup_decision_invalid", 422)

        areas: tuple[Literal["work", "output", "audit"], ...] = (
            ("work", "output")
            if decision == "diagnostic_retained"
            else ("work", "output", "audit")
        )
        store.cleanup_task_areas(
            session_id=conversation_id,
            task_id=task.id,
            areas=areas,
        )
        task_item["state"] = terminal_state
        task_item["cleanup_state"] = (
            "diagnostic_retained"
            if decision == "diagnostic_retained"
            else "cleaned"
        )
        task_item["cleanup_policy"] = decision
        task_item["diagnostic"] = {
            "error_present": bool(task.error_message),
        }
        store.write_manifest(session_id=conversation_id, payload=manifest)
        return self._result_from_manifest(task=task, manifest=manifest)

    async def finalize(
        self,
        task: Task,
        *,
        state: str | None = None,
        error_present: bool | None = None,
    ) -> ExecutionWorkspaceResult:
        """Record terminal retention facts only when a workspace already exists.

        Args:
            task: 需要处理的任务对象。
            state: 用于执行当前操作的 state 参数。
            error_present: 用于执行当前操作的 error present 参数。
        """
        conversation_id = task.conversation_id
        if conversation_id is None:
            return ExecutionWorkspaceResult("not_created", None, task.id, ())
        store = SessionWorkspaceStore(self.root)
        if not store.manifest_exists(session_id=conversation_id):
            return ExecutionWorkspaceResult(
                "not_created", conversation_id, task.id, ()
            )
        await self._lock_conversation(task)
        manifest = store.read_manifest(session_id=conversation_id)
        if manifest is None:
            return ExecutionWorkspaceResult(
                "not_created", conversation_id, task.id, ()
            )
        tasks = cast(dict[str, dict[str, object]], manifest["tasks"])
        task_item = tasks.get(task.id)
        if task_item is None:
            return ExecutionWorkspaceResult(
                "not_created", conversation_id, task.id, ()
            )
        terminal_state = state or task.status
        if terminal_state in _TERMINAL_STATES:
            task_item["state"] = terminal_state
            if task_item["cleanup_state"] not in {
                "cleaned",
                "diagnostic_retained",
            }:
                task_item["cleanup_state"] = "retaining"
                task_item["cleanup_policy"] = _default_cleanup_policy(
                    terminal_state
                )
            task_item["diagnostic"] = {
                "error_present": (
                    bool(task.error_message)
                    if error_present is None
                    else error_present
                ),
            }
            store.write_manifest(session_id=conversation_id, payload=manifest)
        return self._result_from_manifest(task=task, manifest=manifest)

    async def _lock_conversation(self, task: Task) -> Conversation:
        """Serialize shared input/manifest changes and recheck active ownership.

        Args:
            task: 需要处理的任务对象。
        """
        conversation = await self.session.scalar(
            select(Conversation)
            .where(
                Conversation.id == task.conversation_id,
                Conversation.user_id == task.user_id,
                Conversation.archived_at.is_(None),
            )
            .with_for_update()
        )
        if conversation is None:
            raise ResourceReferenceError("resource_reference_not_found", 404)
        return conversation

    @staticmethod
    def _result_from_manifest(
        *, task: Task, manifest: dict[str, object]
    ) -> ExecutionWorkspaceResult:
        """Project safe Task resource facts from a validated manifest.

        Args:
            task: 需要处理的任务对象。
            manifest: 用于执行当前操作的 manifest 参数。
        """
        resources = cast(dict[str, dict[str, object]], manifest["resources"])
        tasks = cast(dict[str, dict[str, object]], manifest["tasks"])
        task_item = tasks.get(task.id, {})
        resource_ids = cast(list[str], task_item.get("resource_ids", []))
        prepared = tuple(
            PreparedWorkspaceResource(
                reference_id=reference_id,
                version=cast(str, resources[reference_id]["version"]),
                relative_path=cast(str, resources[reference_id]["relative_path"]),
                content_hash=cast(str, resources[reference_id]["content_hash"]),
                size_bytes=cast(int, resources[reference_id]["size_bytes"]),
            )
            for reference_id in resource_ids
            if reference_id in resources
        )
        return ExecutionWorkspaceResult(
            state="active",
            conversation_id=task.conversation_id,
            task_id=task.id,
            resources=prepared,
        )


def _workspace_filename(display_name: str, *, reference_id: str) -> str:
    """Generate a bounded safe input filename without changing display metadata.

    Args:
        display_name: 用于执行当前操作的 display name 参数。
        reference_id: 用于执行当前操作的 reference id 参数。
    """
    normalized = (
        display_name.strip()
        .replace("/", "_")
        .replace("\\", "_")
        .replace("\x00", "_")
    )
    if not normalized or normalized in {".", ".."}:
        normalized = reference_id
    if len(normalized) <= 128:
        return normalized
    suffix = Path(normalized).suffix
    if len(suffix) > 16:
        suffix = ""
    stem_limit = 128 - len(suffix)
    return f"{normalized[:stem_limit]}{suffix}"


def _workspace_is_needed(resource_reference_ids: tuple[str, ...]) -> bool:
    """Make the trusted snapshot resource decision explicit and deterministic.

    Args:
        resource_reference_ids: 用于执行当前操作的 resource reference ids 参数。
    """
    return bool(resource_reference_ids)


def _default_cleanup_policy(state: str) -> str:
    """Return the bounded terminal retention decision implied by Task state.

    Args:
        state: 用于执行当前操作的 state 参数。
    """
    if state == "success":
        return "success"
    if state in {"failed", "cancelled"}:
        return "failed"
    return "pending"


__all__ = [
    "ExecutionWorkspaceResult",
    "ExecutionWorkspaceService",
    "PreparedWorkspaceResource",
    "WorkspaceCleanupDecision",
    "WorkspacePreparationState",
]
