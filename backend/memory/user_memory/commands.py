from __future__ import annotations

from typing import Any

from sqlalchemy import select

from tasks.commands import (
    _command_rest,
    _fail_task,
    _load_pending_task,
    _mark_running,
    _safe_summary,
    _succeed_task,
)
from tasks.lifecycle import TaskNotFoundError, TaskServiceError
from domain.models import Memory, MemoryPolicy, MemoryRetrievalTrace, Task

from .errors import InvalidMemoryCommandError


class MemoryCommandMixin:
    """Execute /memory task commands for MemoryService."""

    async def execute_task(self: Any, task_id: str) -> Task:
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

    async def _execute_memory_command(self: Any, task: Task) -> str:
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
            from memory.candidate_pipeline import MemoryPolicyService

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
