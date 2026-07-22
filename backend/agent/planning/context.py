from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.planning.capabilities import CapabilitySnapshot
from agent.skill_management import SkillDefinition


@dataclass(frozen=True)
class TaskContext:
    """表示 处理 task context 的后端数据结构或服务对象。"""

    task_id: str
    user_id: str
    task_type: str
    input_text: str
    memory_summary: str
    user_display_name: str = ""
    skill_names: tuple[str, ...] = ()
    skill_instructions: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    approval_required_tools: tuple[str, ...] = ()
    capability_summary: tuple[str, ...] = ()
    capability_revision: int = 0
    tool_snapshot_revision: int = 0
    tool_versions: tuple[tuple[str, str], ...] = ()
    tool_selection_reasons: tuple[tuple[str, str], ...] = ()
    search_query: str | None = None
    sources: tuple[dict[str, Any], ...] = ()
    conversation_history: tuple[tuple[str, str], ...] = ()
    conversation_summary: str = ""
    memory_blocks: tuple[str, ...] = ()
    context_trace: tuple[dict[str, Any], ...] = ()
    conversation_compacted: bool = False


class ContextBuilder:
    """表示 处理 context builder 的后端数据结构或服务对象。"""

    def build(
        self,
        *,
        task: Any,
        user: Any,
        memory_summary: str,
        skills: tuple[SkillDefinition, ...],
        capabilities: CapabilitySnapshot,
        search_query: str | None = None,
        sources: tuple[dict[str, Any], ...] = (),
        conversation_history: tuple[tuple[str, str], ...] = (),
        conversation_summary: str = "",
        memory_blocks: tuple[str, ...] = (),
        context_trace: tuple[dict[str, Any], ...] = (),
        conversation_compacted: bool = False,
    ) -> TaskContext:
        """构建。

        Args:
            task: task 参数。
            user: user 参数。
            memory_summary: memory_summary 参数。
            skills: skills 参数。
            capabilities: capabilities 参数。
            search_query: search_query 参数。
            sources: sources 参数。
            conversation_history: conversation_history 参数。
            conversation_summary: conversation_summary 参数。
            memory_blocks: memory_blocks 参数。
            context_trace: context_trace 参数。
            conversation_compacted: conversation_compacted 参数。
        """
        return TaskContext(
            task_id=str(task.id),
            user_id=str(user.id),
            user_display_name=str(user.display_name),
            task_type=str(task.task_type),
            input_text=str(task.input_text),
            memory_summary=memory_summary,
            skill_names=tuple(skill.name for skill in skills),
            skill_instructions=tuple(skill.instructions for skill in skills),
            allowed_tools=capabilities.allowed_tools,
            approval_required_tools=capabilities.approval_required_tools,
            capability_summary=capabilities.summaries,
            capability_revision=capabilities.revision,
            tool_snapshot_revision=capabilities.revision,
            tool_versions=capabilities.tool_versions,
            tool_selection_reasons=capabilities.selection_reasons,
            search_query=search_query,
            sources=sources,
            conversation_history=conversation_history,
            conversation_summary=conversation_summary,
            memory_blocks=memory_blocks,
            context_trace=context_trace,
            conversation_compacted=conversation_compacted,
        )
