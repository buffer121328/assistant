from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .capabilities import CapabilitySnapshot
from .skills import SkillDefinition


@dataclass(frozen=True)
class TaskContext:
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


class ContextBuilder:
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
    ) -> TaskContext:
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
        )
