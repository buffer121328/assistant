from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

LegacySuccessHook = Callable[[Any], Awaitable[None]]
LegacyEventSink = Callable[[str, dict[str, object]], Awaitable[None]]


def memory_candidate_as_success_hook(
    memory_candidate_hook: LegacySuccessHook | None,
) -> LegacySuccessHook | None:
    """Adapt the legacy memory callback to the retained success-hook contract.

    Args:
        memory_candidate_hook: 用于执行当前操作的 memory candidate hook 参数。
    """
    if memory_candidate_hook is None:
        return None

    async def success_hook(task: Any) -> None:
        """将已提交任务转交给遗留成功回调。

        Args:
            task: 需要处理的任务对象。
        """
        await memory_candidate_hook(task)

    return success_hook


async def invoke_legacy_success_hook(
    success_hook: LegacySuccessHook | None,
    task: Any,
) -> None:
    """Keep historical success callbacks best-effort after a committed Task.

    Args:
        success_hook: 用于执行当前操作的 success hook 参数。
        task: 需要处理的任务对象。
    """
    if success_hook is None:
        return
    try:
        await success_hook(task)
    except Exception:
        return


async def publish_legacy_event(
    event_sink: LegacyEventSink | None,
    event_type: str,
    payload: dict[str, object],
) -> None:
    """Preserve legacy event publication without making it lifecycle authority.

    Args:
        event_sink: 用于执行当前操作的 event sink 参数。
        event_type: 用于执行当前操作的 event type 参数。
        payload: 当前操作的结构化载荷。
    """
    if event_sink is None:
        return
    try:
        await event_sink(event_type, payload)
    except Exception:
        return


__all__ = [
    "LegacyEventSink",
    "LegacySuccessHook",
    "invoke_legacy_success_hook",
    "memory_candidate_as_success_hook",
    "publish_legacy_event",
]
