from __future__ import annotations


COMMAND_TO_TASK_TYPE = {
    "/plan": "plan",
    "/learn": "learn",
    "/daily": "daily",
    "/office": "office",
    "/memory": "memory",
    "/status": "status",
}


def parse_task_type(text: str) -> str | None:
    normalized = text.strip()
    if not normalized:
        return None
    command = normalized.split(maxsplit=1)[0]
    return COMMAND_TO_TASK_TYPE.get(command)
