from __future__ import annotations

from features import FEATURE_COMMANDS

COMMAND_TO_TASK_TYPE = {
    **FEATURE_COMMANDS,
    "/memory": "memory",
    "/status": "status",
}


def parse_task_type(text: str) -> str | None:
    """解析 task type。

    Args:
        text: text 参数。
    """
    normalized = text.strip()
    if not normalized:
        return None
    command = normalized.split(maxsplit=1)[0]
    return COMMAND_TO_TASK_TYPE.get(command)
