from __future__ import annotations

from features.types import FeatureDefinition


FEATURE = FeatureDefinition(
    command="/daily",
    task_type="daily",
    profile_name="v2.daily_reporter",
    skill_names=("research", "daily-report"),
    requested_tools=(
        "search.web",
        "browser.read",
        "email.draft",
        "workspace.list",
        "workspace.read_file",
        "workspace.search_text",
        "workspace.find_files",
        "workspace.read_doc",
        "shell.readonly_exec",
    ),
    default_steps=(
        "读取任务上下文与记忆",
        "检索并整理当日线索",
        "输出日报摘要",
    ),
    max_steps=12,
    timeout_seconds=90.0,
    risk_level="medium",
    execution_mode="plan_execute_review",
    max_review_retries=1,
    max_replans=1,
    max_subagents=2,
    subagent_concurrency=2,
)
