from __future__ import annotations

from features.types import FeatureDefinition


FEATURE = FeatureDefinition(
    command="/plan",
    task_type="plan",
    profile_name="v2.planner",
    skill_names=("structured-planning",),
    requested_tools=("calendar.create_event",),
    default_steps=(
        "明确目标与约束",
        "拆解阶段步骤",
        "给出下一步行动",
    ),
    max_steps=3,
    timeout_seconds=60.0,
    risk_level="low",
)
