from __future__ import annotations

from features.types import FeatureDefinition


FEATURE = FeatureDefinition(
    command="/memory",
    task_type="memory",
    profile_name="local.memory",
    skill_names=(),
    requested_tools=(),
    default_steps=("执行用户记忆命令",),
    max_steps=1,
    timeout_seconds=30.0,
    risk_level="low",
)
