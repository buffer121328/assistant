from __future__ import annotations

from tasks.lifecycle import TaskServiceError


class MemoryNotFoundError(TaskServiceError):
    """表示 处理 memory not found error 的后端数据结构或服务对象。"""

    code = "memory_not_found"
    status_code = 404


class InvalidMemoryCommandError(TaskServiceError):
    """表示 处理 invalid memory command error 的后端数据结构或服务对象。"""

    code = "invalid_memory_command"
    status_code = 400


class ForbiddenMemoryContentError(TaskServiceError):
    """表示 处理 forbidden memory content error 的后端数据结构或服务对象。"""

    code = "forbidden_memory_content"
    status_code = 400
