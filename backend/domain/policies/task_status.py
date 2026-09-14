from __future__ import annotations

from domain.models import TaskStatus


VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCESS,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.WAITING_APPROVAL,
    },
    TaskStatus.WAITING_APPROVAL: {TaskStatus.PENDING, TaskStatus.CANCELLED},
}

TERMINAL_TASK_STATUSES = {
    TaskStatus.SUCCESS.value,
    TaskStatus.FAILED.value,
    TaskStatus.CANCELLED.value,
}
DISPATCHABLE_TASK_STATUSES = TERMINAL_TASK_STATUSES | {
    TaskStatus.WAITING_APPROVAL.value
}
