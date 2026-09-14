from __future__ import annotations

from features.types import FeatureDefinition


FEATURE = FeatureDefinition(
    command="/status",
    task_type="status",
    profile_name="local.status",
    skill_names=(),
    requested_tools=(),
    default_steps=("查询任务状态",),
    max_steps=1,
    timeout_seconds=30.0,
    risk_level="low",
)
