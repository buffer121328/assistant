from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tasks.events import TaskEventRepository
from tasks.lifecycle import TaskService
from domain.models import AgentSchedule, AgentScheduleRun, ToolLog, utc_now

from .constants import MAX_HISTORY, MIN_EVERY_SECONDS
from .payloads import _optional_str, _safe_payload
from .time_utils import _as_utc, _next_cron_time, _timezone


@dataclass
class AgentScheduleService:
    """表示 处理 agent schedule service 的后端数据结构或服务对象。"""

    session: AsyncSession

    async def create(
        self,
        *,
        user_id: str,
        mode: str,
        payload: dict[str, object],
        conversation_id: str | None = None,
        run_at: datetime | None = None,
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        timezone: str = "UTC",
        catch_up_policy: str = "skip",
    ) -> AgentSchedule:
        """创建。

        Args:
            user_id: user_id 参数。
            mode: mode 参数。
            payload: payload 参数。
            conversation_id: conversation_id 参数。
            run_at: run_at 参数。
            every_seconds: every_seconds 参数。
            cron_expr: cron_expr 参数。
            timezone: timezone 参数。
            catch_up_policy: catch_up_policy 参数。
        """
        tz = _timezone(timezone)
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"at", "every", "cron"}:
            raise ValueError("schedule mode must be at, every, or cron")
        if catch_up_policy not in {"skip", "catch_up"}:
            raise ValueError("catch_up_policy must be skip or catch_up")
        safe_payload = _safe_payload(payload)
        now = utc_now()
        if normalized_mode == "at":
            if run_at is None:
                raise ValueError("run_at is required for at schedules")
            next_run = _as_utc(run_at)
        elif normalized_mode == "every":
            if every_seconds is None or every_seconds < MIN_EVERY_SECONDS:
                raise ValueError("every_seconds is below the minimum interval")
            safe_payload["every_seconds"] = every_seconds
            next_run = now + timedelta(seconds=every_seconds)
        else:
            if not cron_expr:
                raise ValueError("cron_expr is required for cron schedules")
            safe_payload["cron_expr"] = cron_expr
            next_run = _next_cron_time(cron_expr, now, tz)

        schedule = AgentSchedule(
            user_id=user_id,
            conversation_id=conversation_id,
            mode=normalized_mode,
            timezone=timezone,
            enabled=True,
            payload_json=json.dumps(safe_payload, ensure_ascii=False, default=str),
            catch_up_policy=catch_up_policy,
            next_run_at=next_run,
        )
        self.session.add(schedule)
        await self.session.flush()
        await self._tool_log(
            task_id=None,
            name="schedule.create",
            status="succeeded",
            output={
                "schedule_id": schedule.id,
                "mode": schedule.mode,
                "next_run_at": schedule.next_run_at,
            },
        )
        await self.session.commit()
        await self.session.refresh(schedule)
        return schedule

    async def list_schedules(self, *, user_id: str) -> list[dict[str, object]]:
        """列出 schedules。

        Args:
            user_id: user_id 参数。
        """
        result = await self.session.scalars(
            select(AgentSchedule)
            .where(AgentSchedule.user_id == user_id, AgentSchedule.deleted_at.is_(None))
            .order_by(AgentSchedule.created_at.desc())
        )
        return [self._schedule_dict(item) for item in result]

    async def toggle(
        self, *, user_id: str, schedule_id: str, enabled: bool
    ) -> dict[str, object]:
        """处理 toggle。

        Args:
            user_id: user_id 参数。
            schedule_id: schedule_id 参数。
            enabled: enabled 参数。
        """
        schedule = await self._owned_schedule(user_id=user_id, schedule_id=schedule_id)
        schedule.enabled = enabled
        await self._tool_log(
            task_id=None,
            name="schedule.toggle",
            status="succeeded",
            output={"schedule_id": schedule.id, "enabled": enabled},
        )
        await self.session.commit()
        return self._schedule_dict(schedule)

    async def delete(self, *, user_id: str, schedule_id: str) -> dict[str, object]:
        """删除。

        Args:
            user_id: user_id 参数。
            schedule_id: schedule_id 参数。
        """
        schedule = await self._owned_schedule(user_id=user_id, schedule_id=schedule_id)
        schedule.enabled = False
        schedule.deleted_at = utc_now()
        await self._tool_log(
            task_id=None,
            name="schedule.delete",
            status="succeeded",
            output={"schedule_id": schedule.id},
        )
        await self.session.commit()
        return {"schedule_id": schedule.id, "deleted": True}

    async def run_now(self, *, user_id: str, schedule_id: str) -> dict[str, object]:
        """运行 now。

        Args:
            user_id: user_id 参数。
            schedule_id: schedule_id 参数。
        """
        schedule = await self._owned_schedule(user_id=user_id, schedule_id=schedule_id)
        run = await self._materialize(
            schedule, scheduled_for=utc_now(), evaluated_at=utc_now(), force=True
        )
        await self.session.commit()
        return {
            "schedule_id": schedule.id,
            "run_id": run.id,
            "task_id": run.task_id,
            "status": run.status,
        }

    async def history(
        self, *, user_id: str, schedule_id: str, limit: int = MAX_HISTORY
    ) -> list[dict[str, object]]:
        """处理 history。

        Args:
            user_id: user_id 参数。
            schedule_id: schedule_id 参数。
            limit: limit 参数。
        """
        schedule = await self._owned_schedule(user_id=user_id, schedule_id=schedule_id)
        result = await self.session.scalars(
            select(AgentScheduleRun)
            .where(AgentScheduleRun.schedule_id == schedule.id)
            .order_by(AgentScheduleRun.created_at.desc())
            .limit(min(limit, MAX_HISTORY))
        )
        return [self._run_dict(item) for item in result]

    async def materialize_due(
        self, *, now: datetime | None = None
    ) -> list[AgentScheduleRun]:
        """处理 materialize due。

        Args:
            now: now 参数。
        """
        current = _as_utc(now or utc_now())
        result = await self.session.scalars(
            select(AgentSchedule).where(
                AgentSchedule.enabled.is_(True),
                AgentSchedule.deleted_at.is_(None),
                AgentSchedule.next_run_at.is_not(None),
                AgentSchedule.next_run_at <= current,
            )
        )
        runs: list[AgentScheduleRun] = []
        for schedule in result:
            assert schedule.next_run_at is not None
            runs.append(
                await self._materialize(
                    schedule, scheduled_for=schedule.next_run_at, evaluated_at=current
                )
            )
        await self.session.commit()
        return runs

    async def _materialize(
        self,
        schedule: AgentSchedule,
        *,
        scheduled_for: datetime,
        evaluated_at: datetime | None = None,
        force: bool = False,
    ) -> AgentScheduleRun:
        """执行 处理 materialize 的内部辅助逻辑。

        Args:
            schedule: schedule 参数。
            scheduled_for: scheduled_for 参数。
            evaluated_at: evaluated_at 参数。
            force: force 参数。
        """
        scheduled_for = _as_utc(scheduled_for)
        existing = await self.session.scalar(
            select(AgentScheduleRun).where(
                AgentScheduleRun.schedule_id == schedule.id,
                AgentScheduleRun.scheduled_for == scheduled_for,
            )
        )
        if existing is not None and not force:
            return existing
        payload = json.loads(schedule.payload_json)
        task = await TaskService(self.session).create_task(
            user_id=schedule.user_id,
            platform="agent_schedule",
            task_type=str(payload.get("task_type") or "agent"),
            input_text=str(payload.get("input_text") or ""),
            workflow_key=_optional_str(payload.get("workflow_key")),
            model_class=_optional_str(payload.get("model_class")),
            conversation_id=schedule.conversation_id,
            commit=False,
        )
        run = AgentScheduleRun(
            schedule_id=schedule.id,
            user_id=schedule.user_id,
            scheduled_for=scheduled_for,
            task_id=task.id,
            status="materialized",
        )
        self.session.add(run)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(AgentScheduleRun).where(
                    AgentScheduleRun.schedule_id == schedule.id,
                    AgentScheduleRun.scheduled_for == scheduled_for,
                )
            )
            if existing is None:
                raise
            return existing
        await TaskEventRepository(self.session).append(
            task_id=task.id,
            user_id=schedule.user_id,
            event_type="queued",
            payload={
                "source": "schedule",
                "schedule_id": schedule.id,
                "scheduled_for": scheduled_for.isoformat(),
            },
        )
        schedule.last_run_at = scheduled_for
        schedule.next_run_at = self._next_after_run(
            schedule, scheduled_for, evaluated_at=evaluated_at or scheduled_for
        )
        await self._tool_log(
            task_id=task.id,
            name="schedule.materialize",
            status="succeeded",
            output={"schedule_id": schedule.id, "run_id": run.id},
        )
        return run

    def _next_after_run(
        self,
        schedule: AgentSchedule,
        scheduled_for: datetime,
        *,
        evaluated_at: datetime,
    ) -> datetime | None:
        """执行 处理 next after run 的内部辅助逻辑。

        Args:
            schedule: schedule 参数。
            scheduled_for: scheduled_for 参数。
            evaluated_at: evaluated_at 参数。
        """
        payload = json.loads(schedule.payload_json)
        if schedule.mode == "at":
            schedule.enabled = False
            return None
        if schedule.mode == "every":
            every_seconds = int(payload.get("every_seconds") or MIN_EVERY_SECONDS)
            if schedule.catch_up_policy == "catch_up":
                return scheduled_for + timedelta(seconds=every_seconds)
            now = _as_utc(evaluated_at)
            next_run = scheduled_for + timedelta(seconds=every_seconds)
            while next_run <= now:
                next_run += timedelta(seconds=every_seconds)
            return next_run
        if schedule.mode == "cron":
            return _next_cron_time(
                str(payload.get("cron_expr") or "* * * * *"),
                scheduled_for + timedelta(minutes=1),
                _timezone(schedule.timezone),
            )
        return None

    async def _owned_schedule(self, *, user_id: str, schedule_id: str) -> AgentSchedule:
        """执行 处理 owned schedule 的内部辅助逻辑。

        Args:
            user_id: user_id 参数。
            schedule_id: schedule_id 参数。
        """
        schedule = await self.session.scalar(
            select(AgentSchedule).where(
                AgentSchedule.id == schedule_id,
                AgentSchedule.user_id == user_id,
                AgentSchedule.deleted_at.is_(None),
            )
        )
        if schedule is None:
            raise ValueError("schedule not found")
        return schedule

    async def _tool_log(
        self, *, task_id: str | None, name: str, status: str, output: dict[str, object]
    ) -> None:
        """执行 处理 tool log 的内部辅助逻辑。

        Args:
            task_id: task_id 参数。
            name: name 参数。
            status: status 参数。
            output: output 参数。
        """
        self.session.add(
            ToolLog(
                task_id=task_id,
                tool_name=name,
                status=status,
                output_text=json.dumps(output, ensure_ascii=False, default=str),
            )
        )
        await self.session.flush()

    @staticmethod
    def _schedule_dict(item: AgentSchedule) -> dict[str, object]:
        """执行 处理 schedule dict 的内部辅助逻辑。

        Args:
            item: item 参数。
        """
        return {
            "schedule_id": item.id,
            "mode": item.mode,
            "enabled": item.enabled,
            "next_run_at": item.next_run_at.isoformat() if item.next_run_at else None,
            "last_run_at": item.last_run_at.isoformat() if item.last_run_at else None,
            "timezone": item.timezone,
            "catch_up_policy": item.catch_up_policy,
        }

    @staticmethod
    def _run_dict(item: AgentScheduleRun) -> dict[str, object]:
        """执行 运行 dict 的内部辅助逻辑。

        Args:
            item: item 参数。
        """
        return {
            "run_id": item.id,
            "schedule_id": item.schedule_id,
            "task_id": item.task_id,
            "scheduled_for": item.scheduled_for.isoformat(),
            "status": item.status,
        }
