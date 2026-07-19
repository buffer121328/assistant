from __future__ import annotations

from features.types import FeatureDefinition


FEATURE = FeatureDefinition(
    command="/office",
    task_type="office",
    profile_name="v2.office_writer",
    skill_names=("office-writing",),
    requested_tools=(
        "email.draft",
        "calendar.create_event",
        "office.create_docx",
        "office.create_xlsx",
        "office.create_pptx",
        "workspace.list",
        "workspace.read_file",
        "workspace.search_text",
        "workspace.find_files",
        "workspace.read_doc",
        "shell.readonly_exec",
    ),
    default_steps=(
        "读取任务上下文与记忆",
        "整理输入材料",
        "输出结构化文本",
    ),
    max_steps=12,
    timeout_seconds=90.0,
    risk_level="low",
    execution_mode="plan_execute_review",
    max_review_retries=1,
    max_replans=1,
    max_subagents=2,
    subagent_concurrency=2,
)
