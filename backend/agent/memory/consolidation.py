from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from time import monotonic
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import (
    Memory,
    MemoryConsolidationDecision,
    MemoryConsolidationDigest,
    MemoryConsolidationRun,
    MemoryIndexOutbox,
    MemoryLink,
    new_id,
)
from agent.memory.candidates import obvious_preference_conflict
from agent.memory.safety import memory_content_hash, normalize_memory_content


@dataclass(frozen=True)
class ReconciliationReport:
    """表示 处理 reconciliation report 的后端数据结构或服务对象。"""

    missing_memory_ids: tuple[str, ...] = ()
    orphan_index_ids: tuple[str, ...] = ()
    deleted_orphan_count: int = 0
    error_code: str | None = None


class SemanticIndexReconciler(Protocol):
    """表示 处理 semantic index reconciler 的后端数据结构或服务对象。"""

    async def reconcile(
        self, *, user_id: str, active_memory_ids: tuple[str, ...]
    ) -> ReconciliationReport:
        """处理 reconcile。

        Args:
            user_id: user_id 参数。
            active_memory_ids: active_memory_ids 参数。
        """
        ...


class MemoryConsolidationService:
    """表示 处理 memory consolidation service 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        reconciler: SemanticIndexReconciler | None = None,
        batch_limit: int = 200,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            reconciler: reconciler 参数。
            batch_limit: batch_limit 参数。
        """
        self.session = session
        self.reconciler = reconciler
        self.batch_limit = max(1, min(batch_limit, 1_000))

    async def run_daily(
        self, *, user_id: str, window_start: datetime, window_end: datetime
    ) -> MemoryConsolidationRun:
        """运行 daily。

        Args:
            user_id: user_id 参数。
            window_start: window_start 参数。
            window_end: window_end 参数。
        """
        existing = await self._existing(user_id, "daily", window_start, window_end)
        if existing is not None:
            return existing
        started = monotonic()
        run = MemoryConsolidationRun(
            user_id=user_id,
            run_type="daily",
            window_start=window_start,
            window_end=window_end,
            status="running",
        )
        self.session.add(run)
        await self.session.flush()
        memories = list(
            await self.session.scalars(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.created_at >= window_start,
                    Memory.created_at < window_end,
                    Memory.deleted_at.is_(None),
                )
                .order_by(Memory.created_at.asc(), Memory.id.asc())
                .limit(self.batch_limit)
            )
        )
        run.processed_count = len(memories)
        run.merged_count = await self._merge_duplicates(run, memories, window_end)
        run.conflict_count = await self._mark_conflicts(run, memories)
        await self._record_temporal_updates(run, memories)
        reconciliation = await self._reconcile(user_id)
        run.reconciliation_json = json.dumps(asdict(reconciliation), sort_keys=True)
        digest = MemoryConsolidationDigest(
            user_id=user_id,
            digest_type="daily",
            window_start=window_start,
            window_end=window_end,
            content_json=json.dumps(
                {
                    "processed_count": run.processed_count,
                    "merged_count": run.merged_count,
                    "conflict_count": run.conflict_count,
                    "memory_ids": [item.id for item in memories],
                },
                sort_keys=True,
            ),
        )
        self.session.add(digest)
        await self.session.flush()
        run.digest_id = digest.id
        run.status = (
            "completed"
            if reconciliation.error_code is None
            else "completed_with_warning"
        )
        run.duration_ms = (monotonic() - started) * 1000
        await self.session.flush()
        return run

    async def run_weekly(
        self, *, user_id: str, window_start: datetime, window_end: datetime
    ) -> MemoryConsolidationRun:
        """运行 weekly。

        Args:
            user_id: user_id 参数。
            window_start: window_start 参数。
            window_end: window_end 参数。
        """
        existing = await self._existing(user_id, "weekly", window_start, window_end)
        if existing is not None:
            return existing
        started = monotonic()
        run = MemoryConsolidationRun(
            user_id=user_id,
            run_type="weekly",
            window_start=window_start,
            window_end=window_end,
            status="running",
        )
        self.session.add(run)
        await self.session.flush()
        episodes = list(
            await self.session.scalars(
                select(Memory)
                .where(
                    Memory.user_id == user_id,
                    Memory.memory_type == "episode",
                    Memory.status == "active",
                    Memory.created_at >= window_start,
                    Memory.created_at < window_end,
                )
                .order_by(Memory.created_at.asc(), Memory.id.asc())
                .limit(self.batch_limit)
            )
        )
        run.processed_count = len(episodes)
        groups: dict[str, list[Memory]] = {}
        for episode in episodes:
            key = episode.reason_code or "successful_episode"
            groups.setdefault(key, []).append(episode)
        for reason_code, evidence in groups.items():
            distinct_tasks = {item.source_task_id or item.id for item in evidence}
            if len(distinct_tasks) < 2:
                continue
            source_message_id = f"weekly:{window_start.isoformat()}:{reason_code}"[:255]
            existing_procedure = await self.session.scalar(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.source_kind == "weekly_consolidation",
                    Memory.source_message_id == source_message_id,
                )
            )
            if existing_procedure is not None:
                continue
            content = f"重复成功流程：{evidence[0].content}"
            normalized = normalize_memory_content(content)
            procedure = Memory(
                id=new_id(),
                user_id=user_id,
                memory_type="procedure",
                content=normalized,
                normalized_content=normalized,
                content_hash=memory_content_hash(normalized),
                status="candidate",
                is_active=False,
                confirmed_by_user=False,
                source_kind="weekly_consolidation",
                source_trust="trusted_runtime",
                source_message_id=source_message_id,
                reason_code="weekly_procedure_candidate",
                sensitivity="public",
                scope_kind=evidence[0].scope_kind,
                scope_id=evidence[0].scope_id,
            )
            self.session.add(procedure)
            await self.session.flush()
            for episode in evidence:
                await self._link(
                    procedure.id,
                    episode.id,
                    "derived_from",
                    "rule",
                    episode.id,
                )
            run.derived_count += 1
        digest = MemoryConsolidationDigest(
            user_id=user_id,
            digest_type="weekly",
            window_start=window_start,
            window_end=window_end,
            content_json=json.dumps(
                {
                    "episode_count": len(episodes),
                    "derived_count": run.derived_count,
                },
                sort_keys=True,
            ),
        )
        self.session.add(digest)
        await self.session.flush()
        run.digest_id = digest.id
        run.status = "completed"
        run.duration_ms = (monotonic() - started) * 1000
        await self.session.flush()
        return run

    async def _existing(
        self, user_id: str, run_type: str, start: datetime, end: datetime
    ) -> MemoryConsolidationRun | None:
        """执行 处理 existing 的内部辅助逻辑。

        Args:
            user_id: user_id 参数。
            run_type: run_type 参数。
            start: start 参数。
            end: end 参数。
        """
        return await self.session.scalar(
            select(MemoryConsolidationRun).where(
                MemoryConsolidationRun.user_id == user_id,
                MemoryConsolidationRun.run_type == run_type,
                MemoryConsolidationRun.window_start == start,
                MemoryConsolidationRun.window_end == end,
            )
        )

    async def _merge_duplicates(
        self,
        run: MemoryConsolidationRun,
        memories: list[Memory],
        observed_at: datetime,
    ) -> int:
        """执行 处理 merge duplicates 的内部辅助逻辑。

        Args:
            run: run 参数。
            memories: memories 参数。
            observed_at: observed_at 参数。
        """
        groups: dict[str, list[Memory]] = {}
        for memory in memories:
            digest = memory.content_hash or memory_content_hash(memory.content)
            groups.setdefault(digest, []).append(memory)
        merged = 0
        for duplicates in groups.values():
            if len(duplicates) < 2:
                continue
            canonical = sorted(
                duplicates,
                key=lambda item: (
                    -int(item.confirmed_by_user),
                    -int(item.status == "active"),
                    item.created_at,
                    item.id,
                ),
            )[0]
            for duplicate in duplicates:
                if duplicate.id == canonical.id or duplicate.status == "superseded":
                    continue
                duplicate.status = "superseded"
                duplicate.is_active = False
                duplicate.valid_to = observed_at
                duplicate.supersedes_id = canonical.id
                await self._link(
                    duplicate.id,
                    canonical.id,
                    "supports",
                    "rule",
                    duplicate.id,
                )
                self.session.add(
                    MemoryConsolidationDecision(
                        run_id=run.id,
                        source_memory_id=duplicate.id,
                        target_memory_id=canonical.id,
                        action="merge_duplicate",
                        reason_code="exact_content_hash",
                    )
                )
                merged += 1
        return merged

    async def _mark_conflicts(
        self, run: MemoryConsolidationRun, memories: list[Memory]
    ) -> int:
        """执行 标记 conflicts 的内部辅助逻辑。

        Args:
            run: run 参数。
            memories: memories 参数。
        """
        conflicts = 0
        active = [item for item in memories if item.status == "active"]
        candidates = [item for item in memories if item.status == "candidate"]
        for candidate in candidates:
            if candidate.memory_type not in {"preference", "constraint"}:
                continue
            for current in active:
                if (
                    current.memory_type == candidate.memory_type
                    and current.scope_kind == candidate.scope_kind
                    and current.scope_id == candidate.scope_id
                    and obvious_preference_conflict(
                        current.normalized_content or current.content,
                        candidate.normalized_content or candidate.content,
                    )
                ):
                    candidate.status = "conflict_pending"
                    await self._link(
                        candidate.id,
                        current.id,
                        "contradicts",
                        "rule",
                        candidate.id,
                    )
                    self.session.add(
                        MemoryConsolidationDecision(
                            run_id=run.id,
                            source_memory_id=candidate.id,
                            target_memory_id=current.id,
                            action="mark_conflict",
                            reason_code="obvious_preference_conflict",
                        )
                    )
                    conflicts += 1
                    break
        return conflicts

    async def _record_temporal_updates(
        self, run: MemoryConsolidationRun, memories: list[Memory]
    ) -> None:
        """执行 记录 temporal updates 的内部辅助逻辑。

        Args:
            run: run 参数。
            memories: memories 参数。
        """
        for memory in memories:
            if memory.supersedes_id is None or memory.status != "active":
                continue
            old = await self.session.get(Memory, memory.supersedes_id)
            if old is None or old.user_id != memory.user_id:
                continue
            observed = memory.observed_at or memory.created_at
            old.status = "superseded"
            old.is_active = False
            old.valid_to = old.valid_to or observed
            memory.valid_from = memory.valid_from or observed
            await self._link(memory.id, old.id, "supersedes", "rule", memory.id)
            self.session.add(
                MemoryConsolidationDecision(
                    run_id=run.id,
                    source_memory_id=memory.id,
                    target_memory_id=old.id,
                    action="temporal_supersede",
                    reason_code="explicit_supersedes_id",
                )
            )

    async def _reconcile(self, user_id: str) -> ReconciliationReport:
        """执行 处理 reconcile 的内部辅助逻辑。

        Args:
            user_id: user_id 参数。
        """
        if self.reconciler is None:
            return ReconciliationReport()
        active_ids = tuple(
            await self.session.scalars(
                select(Memory.id).where(
                    Memory.user_id == user_id, Memory.status == "active"
                )
            )
        )
        try:
            report = await self.reconciler.reconcile(
                user_id=user_id, active_memory_ids=active_ids
            )
        except Exception:
            return ReconciliationReport(error_code="semantic_reconciliation_failed")
        for memory_id in report.missing_memory_ids:
            memory = await self.session.get(Memory, memory_id)
            if memory is None or memory.user_id != user_id:
                continue
            exists = await self.session.scalar(
                select(MemoryIndexOutbox).where(
                    MemoryIndexOutbox.memory_id == memory_id,
                    MemoryIndexOutbox.operation == "add",
                    MemoryIndexOutbox.status == "pending",
                )
            )
            if exists is None:
                self.session.add(
                    MemoryIndexOutbox(
                        memory_id=memory_id,
                        user_id=user_id,
                        operation="add",
                        status="pending",
                        last_error_code="reconciliation_missing_index",
                    )
                )
        return report

    async def _link(
        self,
        source_id: str,
        target_id: str,
        link_type: str,
        created_by: str,
        evidence_id: str,
    ) -> MemoryLink:
        """执行 处理 link 的内部辅助逻辑。

        Args:
            source_id: source_id 参数。
            target_id: target_id 参数。
            link_type: link_type 参数。
            created_by: created_by 参数。
            evidence_id: evidence_id 参数。
        """
        existing = await self.session.scalar(
            select(MemoryLink).where(
                MemoryLink.source_memory_id == source_id,
                MemoryLink.target_memory_id == target_id,
                MemoryLink.link_type == link_type,
            )
        )
        if existing is not None:
            return existing
        link = MemoryLink(
            source_memory_id=source_id,
            target_memory_id=target_id,
            link_type=link_type,
            confidence=1.0,
            created_by=created_by,
            source_evidence_id=evidence_id,
        )
        self.session.add(link)
        await self.session.flush()
        return link


async def run_memory_consolidation_maintenance(
    *,
    session: AsyncSession,
    now: datetime,
    user_limit: int = 50,
    batch_limit: int = 200,
) -> dict[str, object]:
    """运行 memory consolidation maintenance。

    Args:
        session: session 参数。
        now: now 参数。
        user_limit: user_limit 参数。
        batch_limit: batch_limit 参数。
    """
    from datetime import UTC, timedelta
    from domain.models import User

    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    day_end = current.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = day_end - timedelta(days=1)
    current_week_start = day_end - timedelta(days=day_end.weekday())
    week_end = current_week_start
    week_start = week_end - timedelta(days=7)
    user_ids = tuple(
        await session.scalars(
            select(User.id).order_by(User.id.asc()).limit(max(1, min(user_limit, 500)))
        )
    )
    daily_ids: list[str] = []
    weekly_ids: list[str] = []
    for user_id in user_ids:
        service = MemoryConsolidationService(session, batch_limit=batch_limit)
        daily = await service.run_daily(
            user_id=user_id, window_start=day_start, window_end=day_end
        )
        weekly = await service.run_weekly(
            user_id=user_id, window_start=week_start, window_end=week_end
        )
        daily_ids.append(daily.id)
        weekly_ids.append(weekly.id)
    return {
        "processed_user_count": len(user_ids),
        "daily_run_ids": tuple(daily_ids),
        "weekly_run_ids": tuple(weekly_ids),
    }
