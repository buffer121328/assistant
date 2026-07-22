from __future__ import annotations

import asyncio

from celery import Celery  # type: ignore[import-untyped]

from infrastructure.settings.config import Settings, load_settings
from infrastructure.persistence.database import create_database_sessionmaker
from workers.runtime import execute_task_by_id


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
            "task": "workers.run_v2_maintenance",
            "schedule": settings.scheduler_maintenance_interval_seconds,
        }
    },
)


@celery_app.task(name="workers.execute_task")
def execute_task(task_id: str) -> str:
    """执行 task。

    Args:
        task_id: task_id 参数。
    """
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


@celery_app.task(name="workers.run_v2_maintenance")
def run_v2_maintenance_task() -> dict[str, object]:
    """运行 v2 maintenance task。"""
    from workers.heartbeat import run_v2_maintenance

    runtime_settings = load_settings()
    sessionmaker = create_database_sessionmaker(runtime_settings.database_url)
    return asyncio.run(
        run_v2_maintenance(
            sessionmaker=sessionmaker,
            settings=runtime_settings,
        )
    )


def enqueue_task_execution(
    task_id: str, *, runtime_settings: Settings | None = None
) -> bool:
    """处理 enqueue task execution。

    Args:
        task_id: task_id 参数。
        runtime_settings: runtime_settings 参数。
    """
    runtime_settings = runtime_settings or load_settings()
    if _is_placeholder_redis_url(runtime_settings.redis_url):
        return False
    celery_app.conf.broker_url = runtime_settings.redis_url
    celery_app.conf.result_backend = runtime_settings.redis_url
    execute_task.delay(task_id)
    return True


def _is_placeholder_redis_url(redis_url: str) -> bool:
    """执行 处理 is placeholder redis url 的内部辅助逻辑。

    Args:
        redis_url: redis_url 参数。
    """
    return not redis_url.strip() or "placeholder" in redis_url
