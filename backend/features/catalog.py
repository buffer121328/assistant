from __future__ import annotations

from features.daily.definition import FEATURE as DAILY_FEATURE
from features.learn.definition import FEATURE as LEARN_FEATURE
from features.office.definition import FEATURE as OFFICE_FEATURE
from features.plan.definition import FEATURE as PLAN_FEATURE
from features.types import FeatureDefinition


CORE_FEATURES: tuple[FeatureDefinition, ...] = (
    PLAN_FEATURE,
    LEARN_FEATURE,
    DAILY_FEATURE,
    OFFICE_FEATURE,
)
FEATURE_COMMANDS: dict[str, str] = {
    feature.command: feature.task_type for feature in CORE_FEATURES
}
_FEATURES_BY_TASK_TYPE: dict[str, FeatureDefinition] = {
    feature.task_type: feature for feature in CORE_FEATURES
}


def feature_for_command(command: str) -> FeatureDefinition | None:
    task_type = FEATURE_COMMANDS.get(command)
    if task_type is None:
        return None
    return _FEATURES_BY_TASK_TYPE[task_type]


def feature_for_task_type(task_type: str) -> FeatureDefinition | None:
    return _FEATURES_BY_TASK_TYPE.get(task_type)


def planning_task_types() -> frozenset[str]:
    return frozenset(_FEATURES_BY_TASK_TYPE)
