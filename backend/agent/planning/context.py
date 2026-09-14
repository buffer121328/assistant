from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.planning.capabilities import CapabilitySnapshot
from agent.skill_management import SkillDefinition


@dataclass(frozen=True)
class TaskContext:
    """表示 处理 task context 的后端数据结构或服务对象。"""

    task_id: str  # task_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    task_type: str  # task_type 对应的数据字段。
    input_text: str  # input_text 对应的数据字段。
    memory_summary: str  # memory_summary 对应的数据字段。
    conversation_id: str | None = None  # conversation_id 对应的数据字段。
    user_display_name: str = ""  # user_display_name 对应的数据字段。
    skill_names: tuple[str, ...] = ()  # skill_names 对应的数据字段。
    skill_instructions: tuple[str, ...] = ()  # skill_instructions 对应的数据字段。
    allowed_tools: tuple[str, ...] = ()  # allowed_tools 对应的数据字段。
    approval_required_tools: tuple[
        str, ...
    ] = ()  # approval_required_tools 对应的数据字段。
    capability_summary: tuple[str, ...] = ()  # capability_summary 对应的数据字段。
    capability_revision: int = 0  # capability_revision 对应的数据字段。
    tool_snapshot_revision: int = 0  # tool_snapshot_revision 对应的数据字段。
    tool_versions: tuple[tuple[str, str], ...] = ()  # tool_versions 对应的数据字段。
    tool_selection_reasons: tuple[
        tuple[str, str], ...
    ] = ()  # tool_selection_reasons 对应的数据字段。
    search_query: str | None = None  # search_query 对应的数据字段。
    sources: tuple[dict[str, Any], ...] = ()  # sources 对应的数据字段。
    conversation_history: tuple[
        tuple[str, str], ...
    ] = ()  # conversation_history 对应的数据字段。
    conversation_summary: str = ""  # conversation_summary 对应的数据字段。
    memory_blocks: tuple[str, ...] = ()  # memory_blocks 对应的数据字段。
    context_trace: tuple[dict[str, Any], ...] = ()  # context_trace 对应的数据字段。
    conversation_compacted: bool = False  # conversation_compacted 对应的数据字段。


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
            conversation_id=(
                str(task.conversation_id) if task.conversation_id is not None else None
            ),
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
