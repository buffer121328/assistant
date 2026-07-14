from __future__ import annotations

import asyncio

from celery import Celery  # type: ignore[import-untyped]

from .config import Settings, load_settings
from .database import create_database_sessionmaker
from .worker_runtime import execute_task_by_id


settings = load_settings()

celery_app = Celery(
    "assistant_api",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    accept_content=["json"],
    enable_utc=True,
    result_serializer="json",
    task_serializer="json",
    timezone="UTC",
    beat_schedule={
        "v2-maintenance": {
            "task": "assistant_api.run_v2_maintenance",
            "schedule": settings.scheduler_maintenance_interval_seconds,
        }
    },
)


@celery_app.task(name="assistant_api.execute_task")
def execute_task(task_id: str) -> str:
    runtime_settings = load_settings()
    sessionmaker = create_database_sessionmaker(runtime_settings.database_url)
    asyncio.run(
        execute_task_by_id(
            task_id,
            sessionmaker=sessionmaker,
            settings=runtime_settings,
        )
    )
    return task_id


@celery_app.task(name="assistant_api.run_v2_maintenance")
def run_v2_maintenance_task() -> dict[str, object]:
    from apps.scheduler.heartbeat import run_v2_maintenance

    runtime_settings = load_settings()
    sessionmaker = create_database_sessionmaker(runtime_settings.database_url)
    return asyncio.run(
        run_v2_maintenance(
            sessionmaker=sessionmaker,
            settings=runtime_settings,
        )
    )


def enqueue_task_execution(task_id: str, *, runtime_settings: Settings | None = None) -> bool:
    runtime_settings = runtime_settings or load_settings()
    if _is_placeholder_redis_url(runtime_settings.redis_url):
        return False
    celery_app.conf.broker_url = runtime_settings.redis_url
    celery_app.conf.result_backend = runtime_settings.redis_url
    execute_task.delay(task_id)
    return True


def _is_placeholder_redis_url(redis_url: str) -> bool:
    return not redis_url.strip() or "placeholder" in redis_url
