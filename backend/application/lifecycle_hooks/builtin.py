from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .registry import (
    HookDefinition,
    HookMatcher,
    LifecycleEvent,
    LifecycleHookRegistry,
)


async def _audit_observer(_event: LifecycleEvent) -> None:
    """Execution records provide the bounded audit fact for built-in observation.

    Args:
        _event: 用于执行当前操作的  event 参数。
    """


async def _metrics_observer(_event: LifecycleEvent) -> None:
    """Reserved safe metrics projection; no external telemetry is invoked here.

    Args:
        _event: 用于执行当前操作的  event 参数。
    """


async def _notification_observer(_event: LifecycleEvent) -> None:
    """Reserved notification projection; delivery remains a separately governed action.

    Args:
        _event: 用于执行当前操作的  event 参数。
    """


async def _memory_candidate_observer(_event: LifecycleEvent) -> None:
    """Reserved memory-candidate projection; the memory policy remains authoritative.

    Args:
        _event: 用于执行当前操作的  event 参数。
    """


async def _artifact_index_observer(_event: LifecycleEvent) -> None:
    """Reserved Artifact index projection; publication state is never mutated here.

    Args:
        _event: 用于执行当前操作的  event 参数。
    """


async def _high_risk_guard(_event: LifecycleEvent) -> Literal["allow"]:
    """Allow registry traversal while existing Policy and Approval remain final authority.

    Args:
        _event: 用于执行当前操作的  event 参数。
    """
    return "allow"


def build_builtin_lifecycle_registry(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    session: AsyncSession | None = None,
) -> LifecycleHookRegistry:
    """Compose only trusted, path-free built-in Hook definitions.

    Args:
        sessionmaker: 用于创建独立数据库会话的工厂。
        session: 当前数据库异步会话。
    """
    registry = LifecycleHookRegistry(sessionmaker=sessionmaker, session=session)
    registry.register(
        HookDefinition(
            name="audit.lifecycle",
            version="1",
            source_kind="builtin",
            source_id="core",
            matcher=HookMatcher(
                frozenset({"task.completed", "task.failed", "artifact.registered"})
            ),
            mode="observer",
            handler=_audit_observer,
            priority=100,
        )
    )
    registry.register(
        HookDefinition(
            name="metrics.lifecycle",
            version="1",
            source_kind="builtin",
            source_id="core",
            matcher=HookMatcher(
                frozenset({"task.completed", "task.failed", "run.completed"})
            ),
            mode="observer",
            handler=_metrics_observer,
            priority=50,
        )
    )
    registry.register(
        HookDefinition(
            name="notification.lifecycle",
            version="1",
            source_kind="builtin",
            source_id="core",
            matcher=HookMatcher(frozenset({"task.waiting_approval"})),
            mode="observer",
            handler=_notification_observer,
            priority=40,
        )
    )
    registry.register(
        HookDefinition(
            name="memory.candidate",
            version="1",
            source_kind="builtin",
            source_id="core",
            matcher=HookMatcher(frozenset({"task.completed"})),
            mode="observer",
            handler=_memory_candidate_observer,
            priority=30,
        )
    )
    registry.register(
        HookDefinition(
            name="artifact.index",
            version="1",
            source_kind="builtin",
            source_id="core",
            matcher=HookMatcher(frozenset({"artifact.registered"})),
            mode="observer",
            handler=_artifact_index_observer,
            priority=20,
        )
    )
    registry.register(
        HookDefinition(
            name="guard.high-risk-action",
            version="1",
            source_kind="builtin",
            source_id="core",
            matcher=HookMatcher(
                frozenset(
                    {
                        "before.tool",
                        "before.external.write",
                        "before.email.send",
                        "before.calendar.write",
                        "before.document.write",
                    }
                )
            ),
            mode="guard",
            handler=_high_risk_guard,
            priority=100,
        )
    )
    return registry


__all__ = ["build_builtin_lifecycle_registry"]
