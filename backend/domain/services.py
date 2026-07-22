from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from model_gateway import sanitize_text
from agent.memory import (
    NoopSemanticMemory,
    SemanticMemory,
    classify_memory_sensitivity,
    memory_content_hash,
    normalize_memory_content,
)

from domain.models import (
    Memory,
    MemoryFeedback,
    MemoryIndexOutbox,
    MemoryLink,
    MemoryPolicy,
    MemoryRetrievalTrace,
    ProcessedMessage,
    Task,
    TaskStatus,
    utc_now,
)
from infrastructure.repositories import (
    MemoryCreate,
    MemoryRepository,
    MessageRepository,
    TaskRepository,
    ToolLogCreate,
    ToolLogRepository,
)

from domain.task_lifecycle import (
    ApprovalDecisionConflictError as ApprovalDecisionConflictError,
    ApprovalDecisionResult as ApprovalDecisionResult,
    ApprovalNotFoundError as ApprovalNotFoundError,
    ApprovalService as ApprovalService,
    DISPATCHABLE_TASK_STATUSES as DISPATCHABLE_TASK_STATUSES,
    InvalidCommandTaskError as InvalidCommandTaskError,
    InvalidTaskStatusTransitionError as InvalidTaskStatusTransitionError,
    TERMINAL_TASK_STATUSES as TERMINAL_TASK_STATUSES,
    TaskNotFoundError as TaskNotFoundError,
    TaskService as TaskService,
    TaskServiceError as TaskServiceError,
    UserNotFoundError as UserNotFoundError,
)


class MemoryNotFoundError(TaskServiceError):
    """表示 处理 memory not found error 的后端数据结构或服务对象。"""

    code = "memory_not_found"
    status_code = 404


class InvalidMemoryCommandError(TaskServiceError):
    """表示 处理 invalid memory command error 的后端数据结构或服务对象。"""

    code = "invalid_memory_command"
    status_code = 400


class ForbiddenMemoryContentError(TaskServiceError):
    """表示 处理 forbidden memory content error 的后端数据结构或服务对象。"""

    code = "forbidden_memory_content"
    status_code = 400


class MemoryService:
    """表示 处理 memory service 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        semantic_memory: SemanticMemory | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            semantic_memory: semantic_memory 参数。
        """
        self.session = session
        self.repository = MemoryRepository(session)
        self.task_repository = TaskRepository(session)
        self.semantic_memory = semantic_memory or NoopSemanticMemory()

    async def create_memory(
        self,
        *,
        user_id: str,
        content: str,
        memory_type: str = "preference",
        source_kind: str = "explicit_service",
        source_trust: str = "trusted_user",
        source_spans_json: str = "[]",
        candidate_links_json: str = "[]",
        reason_code: str = "explicit_user_request",
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        source_task_id: str | None = None,
        supersedes_id: str | None = None,
        confirmed_by_user: bool = True,
    ) -> Memory:
        """创建 memory。

        Args:
            user_id: user_id 参数。
            content: content 参数。
            memory_type: memory_type 参数。
            source_kind: source_kind 参数。
            source_trust: source_trust 参数。
            source_spans_json: source_spans_json 参数。
            candidate_links_json: candidate_links_json 参数。
            reason_code: reason_code 参数。
            source_conversation_id: source_conversation_id 参数。
            source_message_id: source_message_id 参数。
            source_task_id: source_task_id 参数。
            supersedes_id: supersedes_id 参数。
            confirmed_by_user: confirmed_by_user 参数。
        """
        normalized_content = normalize_memory_content(content)
        if not normalized_content:
            raise InvalidMemoryCommandError("记忆内容不能为空")
        safety = classify_memory_sensitivity(normalized_content)
        if safety.sensitivity == "forbidden":
            raise ForbiddenMemoryContentError("记忆内容包含禁止保存的凭据类型")
        if source_message_id is not None:
            existing = await self.repository.get_by_source_message(
                user_id=user_id,
                source_kind=source_kind,
                source_message_id=source_message_id,
            )
            if existing is not None:
                return existing
        now = utc_now()
        return await self.repository.create_memory(
            MemoryCreate(
                user_id=user_id,
                content=normalized_content,
                normalized_content=normalized_content,
                content_hash=memory_content_hash(normalized_content),
                memory_type=memory_type,
                status="active" if confirmed_by_user else "candidate",
                sensitivity=safety.sensitivity,
                confirmed_by_user=confirmed_by_user,
                confirmed_at=now if confirmed_by_user else None,
                source_kind=source_kind,
                source_trust=source_trust,
                source_spans_json=source_spans_json,
                candidate_links_json=candidate_links_json,
                reason_code=reason_code,
                source_conversation_id=source_conversation_id,
                source_message_id=source_message_id,
                source_task_id=source_task_id,
                supersedes_id=supersedes_id,
            )
        )

    async def list_active_memories(self, user_id: str) -> list[Memory]:
        """列出 active memories。

        Args:
            user_id: user_id 参数。
        """
        return await self.repository.list_active_memories(user_id)

    async def list_memories(
        self,
        *,
        user_id: str,
        status: str | None = None,
        scope_kind: str | None = None,
    ) -> list[Memory]:
        """列出 memories。

        Args:
            user_id: user_id 参数。
            status: status 参数。
            scope_kind: scope_kind 参数。
        """
        return await self.repository.list_memories(
            user_id=user_id, status=status, scope_kind=scope_kind
        )

    async def get_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """获取 memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.repository.get_memory_by_user(
            user_id=user_id, memory_id=memory_id
        )
        if memory is None:
            raise MemoryNotFoundError("未找到记忆或无权访问")
        return memory

    async def confirm_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """处理 confirm memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        if memory.status not in {"candidate", "conflict_pending"}:
            raise InvalidMemoryCommandError("当前记忆状态不可确认")
        now = utc_now()
        memory.status = "active"
        memory.is_active = True
        memory.confirmed_by_user = True
        memory.confirmed_at = now
        memory.valid_from = memory.valid_from or now
        if memory.supersedes_id is not None:
            old = await self.get_memory(user_id=user_id, memory_id=memory.supersedes_id)
            old.status = "superseded"
            old.is_active = False
            old.valid_to = now
            await self.repository.create_link(
                source_memory_id=memory.id,
                target_memory_id=old.id,
                link_type="supersedes",
                created_by="user",
            )
        await self.session.flush()
        return memory

    async def reject_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """拒绝 memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        if memory.status not in {"candidate", "conflict_pending"}:
            raise InvalidMemoryCommandError("当前记忆状态不可拒绝")
        memory.status = "rejected"
        memory.is_active = False
        await self.session.flush()
        return memory

    async def correct_memory(
        self,
        *,
        user_id: str,
        memory_id: str,
        content: str,
        confirm: bool = False,
    ) -> Memory:
        """处理 correct memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
            content: content 参数。
            confirm: confirm 参数。
        """
        original = await self.get_memory(user_id=user_id, memory_id=memory_id)
        if original.status not in {"active", "candidate", "conflict_pending"}:
            raise InvalidMemoryCommandError("当前记忆状态不可修正")
        corrected = await self.create_memory(
            user_id=user_id,
            content=content,
            memory_type=original.memory_type,
            source_kind="user_correction",
            confirmed_by_user=False,
        )
        corrected.scope_kind = original.scope_kind
        corrected.scope_id = original.scope_id
        corrected.supersedes_id = original.id
        await self.session.flush()
        if confirm:
            return await self.confirm_memory(user_id=user_id, memory_id=corrected.id)
        return corrected

    async def archive_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """归档 memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        memory.status = "archived"
        memory.is_active = False
        memory.archived_at = utc_now()
        await self.session.flush()
        return memory

    async def set_memory_pinned(
        self, *, user_id: str, memory_id: str, pinned: bool
    ) -> Memory:
        """处理 set memory pinned。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
            pinned: pinned 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        memory.is_pinned = pinned
        await self.session.flush()
        return memory

    async def change_memory_scope(
        self,
        *,
        user_id: str,
        memory_id: str,
        scope_kind: str,
        scope_id: str | None = None,
    ) -> Memory:
        """处理 change memory scope。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
            scope_kind: scope_kind 参数。
            scope_id: scope_id 参数。
        """
        allowed = {
            "user/global",
            "user/project",
            "user/conversation",
            "agent/profile",
        }
        if scope_kind not in allowed or (scope_kind != "user/global" and not scope_id):
            raise InvalidMemoryCommandError("无效的记忆作用域")
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        memory.scope_kind = scope_kind
        memory.scope_id = None if scope_kind == "user/global" else scope_id
        await self.session.flush()
        return memory

    async def add_feedback(
        self,
        *,
        user_id: str,
        memory_id: str,
        feedback_type: str,
        task_id: str | None = None,
        conversation_id: str | None = None,
        retrieval_trace_id: str | None = None,
    ) -> MemoryFeedback:
        """处理 add feedback。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
            feedback_type: feedback_type 参数。
            task_id: task_id 参数。
            conversation_id: conversation_id 参数。
            retrieval_trace_id: retrieval_trace_id 参数。
        """
        allowed = {
            "helpful",
            "harmful",
            "incorrect",
            "confirmed",
            "scope_changed",
            "forgotten",
        }
        if feedback_type not in allowed:
            raise InvalidMemoryCommandError("无效的记忆反馈类型")
        await self.get_memory(user_id=user_id, memory_id=memory_id)
        return await self.repository.create_feedback(
            memory_id=memory_id,
            user_id=user_id,
            feedback_type=feedback_type,
            task_id=task_id,
            conversation_id=conversation_id,
            retrieval_trace_id=retrieval_trace_id,
        )

    async def add_link(
        self,
        *,
        user_id: str,
        source_memory_id: str,
        target_memory_id: str,
        link_type: str,
        created_by: str = "user",
    ) -> MemoryLink:
        """处理 add link。

        Args:
            user_id: user_id 参数。
            source_memory_id: source_memory_id 参数。
            target_memory_id: target_memory_id 参数。
            link_type: link_type 参数。
            created_by: created_by 参数。
        """
        allowed_links = {
            "related_to",
            "derived_from",
            "supports",
            "contradicts",
            "supersedes",
            "part_of",
            "applies_to_project",
        }
        if link_type not in allowed_links:
            raise InvalidMemoryCommandError("无效的记忆链接类型")
        await self.get_memory(user_id=user_id, memory_id=source_memory_id)
        await self.get_memory(user_id=user_id, memory_id=target_memory_id)
        return await self.repository.create_link(
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            link_type=link_type,
            created_by=created_by,
        )

    async def list_links(self, *, user_id: str, memory_id: str) -> list[MemoryLink]:
        """列出 links。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        await self.get_memory(user_id=user_id, memory_id=memory_id)
        return await self.repository.list_links_for_memory(memory_id=memory_id)

    async def list_feedback(
        self, *, user_id: str, memory_id: str
    ) -> list[MemoryFeedback]:
        """列出 feedback。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        await self.get_memory(user_id=user_id, memory_id=memory_id)
        return await self.repository.list_feedback_for_memory(
            memory_id=memory_id, user_id=user_id
        )

    async def list_index_outbox(
        self, *, user_id: str, status: str | None = None
    ) -> list[MemoryIndexOutbox]:
        """列出 index outbox。

        Args:
            user_id: user_id 参数。
            status: status 参数。
        """
        return await self.repository.list_index_outbox(user_id=user_id, status=status)

    async def forget_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """处理 forget memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.get_memory(user_id=user_id, memory_id=memory_id)
        memory.status = "deleted"
        memory.is_active = False
        memory.deleted_at = utc_now()
        await self.repository.create_feedback(
            memory_id=memory.id,
            user_id=user_id,
            feedback_type="forgotten",
        )
        await self.session.flush()
        return memory

    async def delete_memory(self, *, user_id: str, memory_id: str) -> Memory:
        """删除 memory。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        memory = await self.repository.get_active_memory_by_user(
            memory_id=memory_id,
            user_id=user_id,
        )
        if memory is None:
            raise MemoryNotFoundError("未找到可删除的记忆或无权访问")

        memory.is_active = False
        memory.status = "deleted"
        memory.deleted_at = utc_now()
        await self.session.flush()
        return memory

    async def execute_task(self, task_id: str) -> Task:
        """执行 task。

        Args:
            task_id: task_id 参数。
        """
        task = await _load_pending_task(
            self.session,
            task_id,
            expected_task_type="memory",
        )
        await _mark_running(self.session, task)

        try:
            result_text = await self._execute_memory_command(task)
        except TaskServiceError as exc:
            return await _fail_task(self.session, task, _safe_summary(exc))

        return await _succeed_task(self.session, task, result_text)

    async def _execute_memory_command(self, task: Task) -> str:
        """执行 执行 memory command 的内部辅助逻辑。

        Args:
            task: task 参数。
        """
        rest = _command_rest(task.input_text, "/memory")
        aliases = {
            "list": "查看",
            "remember": "记住",
            "forget": "删除",
            "correct": "纠正",
            "confirm": "确认",
            "reject": "拒绝",
            "why": "为什么",
            "policy": "策略",
        }
        first, _, remainder = rest.partition(" ")
        if first in aliases:
            rest = f"{aliases[first]} {remainder}".strip()
        if rest.startswith("记住"):
            content = rest.removeprefix("记住").strip()
            memory = await self.create_memory(
                user_id=task.user_id,
                content=content,
                source_kind="explicit_command",
                source_task_id=task.id,
            )
            synced = await self._semantic_add(task=task, memory=memory)
            if self.semantic_memory.enabled and not synced:
                await self.repository.queue_index_operation(
                    memory=memory,
                    operation="add",
                    error_code="semantic_add_failed",
                )
            status = "语义记忆已同步" if synced else "语义记忆不可用，已保留 SQL 记录"
            return f"已保存记忆：{memory.id}；{status}"

        if rest.startswith("确认"):
            memory_id = rest.removeprefix("确认").strip()
            if not memory_id:
                raise InvalidMemoryCommandError("请提供要确认的 memory_id")
            memory = await self.confirm_memory(
                user_id=task.user_id, memory_id=memory_id
            )
            return f"已确认记忆：{memory.id}"

        if rest.startswith("拒绝"):
            memory_id = rest.removeprefix("拒绝").strip()
            if not memory_id:
                raise InvalidMemoryCommandError("请提供要拒绝的 memory_id")
            memory = await self.reject_memory(user_id=task.user_id, memory_id=memory_id)
            return f"已拒绝记忆：{memory.id}"

        if rest.startswith("纠正"):
            parts = rest.removeprefix("纠正").strip().split(maxsplit=1)
            if len(parts) != 2:
                raise InvalidMemoryCommandError("请提供 memory_id 和纠正内容")
            memory = await self.correct_memory(
                user_id=task.user_id,
                memory_id=parts[0],
                content=parts[1],
                confirm=True,
            )
            return f"已纠正记忆：{memory.id}"

        if rest.startswith("反馈"):
            parts = rest.removeprefix("反馈").strip().split()
            if len(parts) != 2:
                raise InvalidMemoryCommandError("请提供 memory_id 和反馈类型")
            feedback = await self.add_feedback(
                user_id=task.user_id,
                memory_id=parts[0],
                feedback_type=parts[1],
                task_id=task.id,
                conversation_id=task.conversation_id,
            )
            return f"已记录记忆反馈：{feedback.feedback_type}"

        if rest.startswith("范围"):
            parts = rest.removeprefix("范围").strip().split()
            if len(parts) not in {2, 3}:
                raise InvalidMemoryCommandError(
                    "请提供 memory_id、scope_kind 和可选 scope_id"
                )
            memory = await self.change_memory_scope(
                user_id=task.user_id,
                memory_id=parts[0],
                scope_kind=parts[1],
                scope_id=parts[2] if len(parts) == 3 else None,
            )
            return f"已更新记忆范围：{memory.scope_kind}"

        if rest.startswith("不再记住"):
            memory_type = rest.removeprefix("不再记住").strip()
            if not memory_type:
                raise InvalidMemoryCommandError("请提供不再记住的记忆类型")
            from domain.memory_candidates import MemoryPolicyService

            await MemoryPolicyService(self.session).set_never_remember(
                user_id=task.user_id, memory_type=memory_type
            )
            return f"已设置不再记住：{memory_type}"

        if rest.startswith("为什么"):
            task_id = rest.removeprefix("为什么").strip()
            owned_task = await self.task_repository.get_task_by_user(
                task_id=task_id, user_id=task.user_id
            )
            if owned_task is None:
                raise TaskNotFoundError("未找到可解释的任务或无权访问")
            trace = await self.session.scalar(
                select(MemoryRetrievalTrace)
                .where(
                    MemoryRetrievalTrace.task_id == task_id,
                    MemoryRetrievalTrace.user_id == task.user_id,
                )
                .order_by(MemoryRetrievalTrace.created_at.desc())
                .limit(1)
            )
            if trace is None:
                return "该任务没有使用记忆。"
            return (
                f"该任务使用了 {trace.injected_count} 条记忆；"
                f"模式：{trace.retrieval_mode}；时间意图：{trace.time_intent}。"
            )

        if rest == "策略":
            policies = list(
                await self.session.scalars(
                    select(MemoryPolicy)
                    .where(MemoryPolicy.user_id == task.user_id)
                    .order_by(MemoryPolicy.policy_key)
                )
            )
            if not policies:
                return "当前没有自定义记忆策略。"
            return "当前记忆策略：\n" + "\n".join(
                f"- {item.policy_key}: {'启用' if item.enabled else '停用'}"
                for item in policies
            )

        if rest == "查看" or rest.startswith("查看 "):
            filter_value = rest.removeprefix("查看").strip()
            statement = select(Memory).where(
                Memory.user_id == task.user_id,
                Memory.sensitivity != "forbidden",
            )
            if filter_value:
                if filter_value in {
                    "episode",
                    "fact",
                    "preference",
                    "constraint",
                    "procedure",
                    "reflection",
                }:
                    statement = statement.where(Memory.memory_type == filter_value)
                elif filter_value in {
                    "active",
                    "candidate",
                    "conflict_pending",
                    "superseded",
                    "archived",
                }:
                    statement = statement.where(Memory.status == filter_value)
                elif filter_value in {
                    "user/global",
                    "user/project",
                    "user/conversation",
                    "agent/profile",
                }:
                    statement = statement.where(Memory.scope_kind == filter_value)
                else:
                    raise InvalidMemoryCommandError("无效的记忆筛选条件")
            else:
                statement = statement.where(Memory.status == "active")
            memories = list(
                await self.session.scalars(
                    statement.order_by(Memory.updated_at.desc()).limit(50)
                )
            )
            if not memories:
                return "暂无记忆。"
            lines = ["当前记忆："]
            lines.extend(
                f"- {memory.id}: "
                f"{'[SENSITIVE]' if memory.sensitivity == 'sensitive' else memory.content}"
                for memory in memories
            )
            return "\n".join(lines)

        if rest.startswith("删除"):
            memory_id = rest.removeprefix("删除").strip()
            if not memory_id:
                raise InvalidMemoryCommandError("请提供要删除的 memory_id")
            memory = await self.delete_memory(user_id=task.user_id, memory_id=memory_id)
            synced = await self._semantic_delete(
                user_id=task.user_id,
                memory_id=memory_id,
            )
            if self.semantic_memory.enabled and not synced:
                await self.repository.queue_index_operation(
                    memory=memory,
                    operation="delete",
                    error_code="semantic_delete_failed",
                )
            status = "语义记忆已同步" if synced else "语义记忆不可用，SQL 删除已生效"
            return f"已删除记忆：{memory_id}；{status}"

        raise InvalidMemoryCommandError(
            "不支持的 /memory 命令，请使用记住、查看、删除、确认、拒绝、纠正、"
            "反馈、范围、不再记住、为什么或策略"
        )

    async def _semantic_add(self, *, task: Task, memory: Memory) -> bool:
        """执行 处理 semantic add 的内部辅助逻辑。

        Args:
            task: task 参数。
            memory: memory 参数。
        """
        if not self.semantic_memory.enabled:
            return False
        try:
            return await self.semantic_memory.add(
                user_id=task.user_id,
                run_id=task.id,
                memory_id=memory.id,
                content=memory.content,
            )
        except Exception:
            return False

    async def _semantic_delete(self, *, user_id: str, memory_id: str) -> bool:
        """执行 处理 semantic delete 的内部辅助逻辑。

        Args:
            user_id: user_id 参数。
            memory_id: memory_id 参数。
        """
        if not self.semantic_memory.enabled:
            return False
        try:
            return await self.semantic_memory.delete(
                user_id=user_id,
                memory_id=memory_id,
            )
        except Exception:
            return False


class StatusService:
    """表示 处理 status service 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session
        self.task_repository = TaskRepository(session)

    async def execute_task(self, task_id: str) -> Task:
        """执行 task。

        Args:
            task_id: task_id 参数。
        """
        task = await _load_pending_task(
            self.session,
            task_id,
            expected_task_type="status",
        )
        await _mark_running(self.session, task)

        try:
            target = await self._resolve_target(task)
            result_text = (
                "暂无可查询的任务状态。"
                if target is None
                else self._format_status_summary(target)
            )
        except TaskServiceError as exc:
            return await _fail_task(self.session, task, _safe_summary(exc))

        return await _succeed_task(self.session, task, result_text)

    async def _resolve_target(self, task: Task) -> Task | None:
        """执行 解析 target 的内部辅助逻辑。

        Args:
            task: task 参数。
        """
        rest = _command_rest(task.input_text, "/status")
        if not rest:
            return await self.task_repository.get_latest_non_status_task(
                user_id=task.user_id,
                exclude_task_id=task.id,
            )

        task_id = rest.split(maxsplit=1)[0]
        target = await self.task_repository.get_task_by_user(
            task_id=task_id,
            user_id=task.user_id,
        )
        if target is None:
            raise TaskNotFoundError("未找到可查询的任务或无权访问")
        return target

    def _format_status_summary(self, task: Task) -> str:
        """执行 处理 format status summary 的内部辅助逻辑。

        Args:
            task: task 参数。
        """
        lines = [
            "任务状态：",
            f"任务ID: {task.id}",
            f"类型: {task.task_type}",
            f"状态: {task.status}",
            f"创建时间: {task.created_at.isoformat()}",
            f"更新时间: {task.updated_at.isoformat()}",
            f"当前阶段: {_phase_label(task.status)}",
        ]
        if task.result_text:
            lines.append(f"结果摘要: {_safe_summary(task.result_text)}")
        if task.error_message:
            lines.append(f"错误摘要: {_safe_summary(task.error_message)}")
        return "\n".join(lines)


class LangBotMessageClientProtocol(Protocol):
    """表示 处理 lang bot message client protocol 的后端数据结构或服务对象。"""

    async def send_message(
        self,
        *,
        adapter: str,
        conversation_id: str,
        conversation_type: str,
        text: str,
        idempotency_key: str | None = None,
    ) -> Any:
        """处理 send message。

        Args:
            adapter: adapter 参数。
            conversation_id: conversation_id 参数。
            conversation_type: conversation_type 参数。
            text: text 参数。
            idempotency_key: idempotency_key 参数。
        """
        pass


@dataclass(frozen=True)
class DispatchResult:
    """表示 分发 result 的后端数据结构或服务对象。"""

    status: str
    message: str


class ResultDispatcher:
    """表示 处理 result dispatcher 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        langbot_client: LangBotMessageClientProtocol | None = None,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            langbot_client: langbot_client 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.session = session
        self.langbot_client = langbot_client
        self.sensitive_values = tuple(sensitive_values)
        self.task_repository = TaskRepository(session)
        self.webhook_repository = MessageRepository(session)
        self.tool_log_repository = ToolLogRepository(session)

    async def dispatch_task(self, task_id: str) -> DispatchResult:
        """分发 task。

        Args:
            task_id: task_id 参数。
        """
        task = await self.task_repository.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(f"Task not found: {task_id}")

        if task.status not in DISPATCHABLE_TASK_STATUSES:
            return DispatchResult(status="skipped", message="任务尚未结束")

        if task.platform != "langbot":
            return DispatchResult(status="skipped", message="该来源不支持结果推送")

        tool_name = _dispatch_tool_name(task)
        already_dispatched = await self.tool_log_repository.has_successful_tool_log(
            task_id=task.id,
            tool_name=tool_name,
        )
        if already_dispatched:
            return DispatchResult(status="skipped", message="任务结果已推送")

        dispatch_record = await self.webhook_repository.get_task_dispatch_record(
            task.id
        )
        target = _resolve_dispatch_target(task=task, dispatch_record=dispatch_record)
        if target is None:
            message = _missing_target_message(task.platform)
            await self._record_dispatch(
                task=task,
                tool_name=tool_name,
                target_payload=None,
                status="failed",
                output_text=None,
                error_message=message,
            )
            await self.session.commit()
            return DispatchResult(status="failed", message=message)

        outbound_text = self._build_message(task)
        try:
            response = await self._send_message(
                task=task,
                target=target,
                outbound_text=outbound_text,
            )
        except Exception as exc:
            safe_error = _safe_summary(
                exc, extra_sensitive_values=self.sensitive_values
            )
            await self._record_dispatch(
                task=task,
                tool_name=tool_name,
                target_payload=target,
                status="failed",
                output_text=None,
                error_message=safe_error,
            )
            await self.session.commit()
            return DispatchResult(status="failed", message=safe_error)

        await self._record_dispatch(
            task=task,
            tool_name=tool_name,
            target_payload=target,
            status="succeeded",
            output_text=_safe_json(
                {"response": response},
                extra_sensitive_values=self.sensitive_values,
            ),
            error_message=None,
        )
        await self.session.commit()
        return DispatchResult(status="succeeded", message="任务结果已推送")

    def _build_message(self, task: Task) -> str:
        """执行 构建 message 的内部辅助逻辑。

        Args:
            task: task 参数。
        """
        if task.status == TaskStatus.SUCCESS.value:
            title = "任务已完成"
            summary = task.result_text or "任务已完成。"
        elif task.status == TaskStatus.WAITING_APPROVAL.value:
            title = "任务等待审批"
            summary = (
                task.result_text
                or task.error_message
                or "任务需要人工批准后才能继续执行。"
            )
        elif task.status == TaskStatus.CANCELLED.value:
            title = "任务已取消"
            summary = task.result_text or "任务已取消。"
        else:
            title = "任务失败"
            summary = task.error_message or "任务执行失败。"

        return "\n".join(
            [
                title,
                f"任务ID: {task.id}",
                f"类型: {task.task_type}",
                f"摘要: {_safe_summary(summary, extra_sensitive_values=self.sensitive_values)}",
            ]
        )

    async def _send_message(
        self,
        *,
        task: Task,
        target: dict[str, str],
        outbound_text: str,
    ) -> Any:
        """执行 处理 send message 的内部辅助逻辑。

        Args:
            task: task 参数。
            target: target 参数。
            outbound_text: outbound_text 参数。
        """
        if task.platform == "langbot":
            if self.langbot_client is None:
                raise RuntimeError("LangBot client is not configured")
            return await self.langbot_client.send_message(
                adapter=target["adapter"],
                conversation_id=target["conversation_id"],
                conversation_type=target["conversation_type"],
                text=outbound_text,
                idempotency_key=task.id,
            )

        raise RuntimeError(f"Unsupported dispatch platform: {task.platform}")

    async def _record_dispatch(
        self,
        *,
        task: Task,
        tool_name: str,
        target_payload: dict[str, str] | None,
        status: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        """执行 记录 dispatch 的内部辅助逻辑。

        Args:
            task: task 参数。
            tool_name: tool_name 参数。
            target_payload: target_payload 参数。
            status: status 参数。
            output_text: output_text 参数。
            error_message: error_message 参数。
        """
        await self.tool_log_repository.create_tool_log(
            ToolLogCreate(
                task_id=task.id,
                tool_name=tool_name,
                status=status,
                input_text=_safe_json(
                    {
                        "platform": task.platform,
                        "target": target_payload,
                        "task_id": task.id,
                        "task_status": task.status,
                    },
                    extra_sensitive_values=self.sensitive_values,
                ),
                output_text=output_text,
                error_message=(
                    None
                    if error_message is None
                    else _safe_summary(
                        error_message,
                        extra_sensitive_values=self.sensitive_values,
                    )
                ),
            )
        )
        await self.webhook_repository.record_delivery_attempt(
            task_id=task.id,
            status=status,
            delivery_status=(
                "succeeded"
                if status == "succeeded"
                else ("failed" if target_payload is None else "retry")
            ),
            error_summary=error_message,
            result_json=output_text,
        )


def _dispatch_tool_name(task: Task) -> str:
    """执行 分发 tool name 的内部辅助逻辑。

    Args:
        task: task 参数。
    """
    if task.platform == "langbot" and task.status == TaskStatus.WAITING_APPROVAL.value:
        return "langbot.approval_dispatch"
    if task.platform == "langbot":
        return "langbot.result_dispatch"
    raise ValueError(f"Unsupported dispatch platform: {task.platform}")


def _missing_target_message(platform: str) -> str:
    """执行 处理 missing target message 的内部辅助逻辑。

    Args:
        platform: platform 参数。
    """
    return {
        "langbot": "缺少 LangBot 推送目标",
    }.get(platform, "缺少推送目标")


def _resolve_dispatch_target(
    *,
    task: Task,
    dispatch_record: ProcessedMessage | None,
) -> dict[str, str] | None:
    """执行 解析 dispatch target 的内部辅助逻辑。

    Args:
        task: task 参数。
        dispatch_record: dispatch_record 参数。
    """
    if dispatch_record is None:
        return None

    if task.platform != "langbot" or not dispatch_record.response_target:
        return None

    try:
        payload = json.loads(dispatch_record.response_target)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    adapter = payload.get("adapter")
    conversation_id = payload.get("conversation_id")
    conversation_type = payload.get("conversation_type")
    if (
        not isinstance(adapter, str)
        or not adapter
        or not isinstance(conversation_id, str)
        or not conversation_id
        or not isinstance(conversation_type, str)
        or not conversation_type
    ):
        return None
    return {
        "adapter": adapter,
        "conversation_id": conversation_id,
        "conversation_type": conversation_type,
    }


async def _load_pending_task(
    session: AsyncSession,
    task_id: str,
    *,
    expected_task_type: str,
) -> Task:
    """执行 加载 pending task 的内部辅助逻辑。

    Args:
        session: session 参数。
        task_id: task_id 参数。
        expected_task_type: expected_task_type 参数。
    """
    task = await session.get(Task, task_id)
    if task is None:
        raise TaskNotFoundError(f"Task not found: {task_id}")
    if task.task_type != expected_task_type:
        raise InvalidCommandTaskError(
            f"Expected {expected_task_type} task, got {task.task_type}"
        )
    if task.status != TaskStatus.PENDING.value:
        raise InvalidTaskStatusTransitionError(
            f"Task is not pending: {task.id} ({task.status})"
        )
    return task


async def _mark_running(session: AsyncSession, task: Task) -> None:
    """执行 标记 running 的内部辅助逻辑。

    Args:
        session: session 参数。
        task: task 参数。
    """
    task.status = TaskStatus.RUNNING.value
    task.result_text = None
    task.error_message = None
    await session.flush()


async def _succeed_task(session: AsyncSession, task: Task, result_text: str) -> Task:
    """执行 处理 succeed task 的内部辅助逻辑。

    Args:
        session: session 参数。
        task: task 参数。
        result_text: result_text 参数。
    """
    task.status = TaskStatus.SUCCESS.value
    task.result_text = result_text
    task.error_message = None
    await session.commit()
    await session.refresh(task)
    return task


async def _fail_task(session: AsyncSession, task: Task, error_message: str) -> Task:
    """执行 处理 fail task 的内部辅助逻辑。

    Args:
        session: session 参数。
        task: task 参数。
        error_message: error_message 参数。
    """
    task.status = TaskStatus.FAILED.value
    task.result_text = None
    task.error_message = error_message
    await session.commit()
    await session.refresh(task)
    return task


def _command_rest(input_text: str, command: str) -> str:
    """执行 处理 command rest 的内部辅助逻辑。

    Args:
        input_text: input_text 参数。
        command: command 参数。
    """
    text = input_text.strip()
    if not text.startswith(command):
        raise InvalidCommandTaskError(f"Invalid command: {command}")
    return text.removeprefix(command).strip()


def _phase_label(status: str) -> str:
    """执行 处理 phase label 的内部辅助逻辑。

    Args:
        status: status 参数。
    """
    return {
        TaskStatus.PENDING.value: "等待执行",
        TaskStatus.RUNNING.value: "执行中",
        TaskStatus.SUCCESS.value: "已完成",
        TaskStatus.FAILED.value: "执行失败",
        TaskStatus.CANCELLED.value: "已取消",
        TaskStatus.WAITING_APPROVAL.value: "等待审批",
    }.get(status, "未知")


def _safe_summary(
    value: object,
    *,
    extra_sensitive_values: Iterable[str | None] = (),
    limit: int = 1000,
) -> str:
    """执行 处理 safe summary 的内部辅助逻辑。

    Args:
        value: value 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
        limit: limit 参数。
    """
    text = sanitize_text(value, extra_sensitive_values=extra_sensitive_values).strip()
    if "traceback" in text.lower():
        text = "内部错误已脱敏"
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _safe_json(
    payload: dict[str, Any],
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> str:
    """执行 处理 safe json 的内部辅助逻辑。

    Args:
        payload: payload 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
    """
    return _safe_summary(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ),
        extra_sensitive_values=extra_sensitive_values,
    )
