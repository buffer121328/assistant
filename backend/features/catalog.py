from __future__ import annotations

from features.daily.definition import FEATURE as DAILY_FEATURE
from features.learn.definition import FEATURE as LEARN_FEATURE
from features.memory_command.definition import FEATURE as MEMORY_FEATURE
from features.office.definition import FEATURE as OFFICE_FEATURE
from features.plan.definition import FEATURE as PLAN_FEATURE
from features.status_command.definition import FEATURE as STATUS_FEATURE
from features.screen_command.definition import FEATURE as SCREEN_FEATURE
from features.types import FeatureDefinition


AGENT_FEATURES: tuple[FeatureDefinition, ...] = (
    PLAN_FEATURE,
    LEARN_FEATURE,
    DAILY_FEATURE,
    OFFICE_FEATURE,
)
UTILITY_FEATURES: tuple[FeatureDefinition, ...] = (
    MEMORY_FEATURE,
    STATUS_FEATURE,
    SCREEN_FEATURE,
)
CORE_FEATURES: tuple[FeatureDefinition, ...] = AGENT_FEATURES + UTILITY_FEATURES
FEATURE_COMMANDS: dict[str, str] = {
    feature.command: feature.task_type for feature in CORE_FEATURES
}
_FEATURES_BY_TASK_TYPE: dict[str, FeatureDefinition] = {
    feature.task_type: feature for feature in CORE_FEATURES
}
_AGENT_FEATURES_BY_TASK_TYPE: dict[str, FeatureDefinition] = {
    feature.task_type: feature for feature in AGENT_FEATURES
}


def feature_for_command(command: str) -> FeatureDefinition | None:
    """处理 feature for command。

    Args:
        command: command 参数。
    """
    task_type = FEATURE_COMMANDS.get(command)
    if task_type is None:
        return None
    return _FEATURES_BY_TASK_TYPE[task_type]


def feature_for_task_type(task_type: str) -> FeatureDefinition | None:
    """处理 feature for task type。

    Args:
        task_type: task_type 参数。
    """
    return _AGENT_FEATURES_BY_TASK_TYPE.get(task_type)


def planning_task_types() -> frozenset[str]:
    """处理 planning task types。"""
    return frozenset(_AGENT_FEATURES_BY_TASK_TYPE)
