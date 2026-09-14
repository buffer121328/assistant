from __future__ import annotations

from features.types import FeatureDefinition


FEATURE = FeatureDefinition(
    command="/screen",
    task_type="screen",
    profile_name="local.screen",
    skill_names=(),
    requested_tools=("desktop.screenshot",),
    default_steps=("生成受控截图 artifact", "通过 LangBot 回传图片或降级文本"),
    max_steps=1,
    timeout_seconds=30.0,
    risk_level="medium",
)
