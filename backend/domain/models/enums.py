from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    """表示 处理 task status 的后端数据结构或服务对象。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_APPROVAL = "waiting_approval"


class ApprovalStatus(str, Enum):
    """表示 处理 approval status 的后端数据结构或服务对象。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalType(str, Enum):
    """表示 处理 approval type 的后端数据结构或服务对象。"""

    TOOL = "tool"
    PLAN = "plan"
    REVIEW = "review"
    CHANGE = "change"
