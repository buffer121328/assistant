from __future__ import annotations

import os

import asyncpg  # type: ignore[import-untyped]
import pytest
from redis.asyncio import Redis


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SERVICE_INTEGRATION") != "1",
    reason="set RUN_SERVICE_INTEGRATION=1 with isolated PostgreSQL and Redis",
)


@pytest.mark.asyncio
async def test_postgres_migration_head_is_queryable() -> None:
    url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(url)
    try:
        version = await connection.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await connection.close()
    assert isinstance(version, str)
    assert version


@pytest.mark.asyncio
async def test_redis_ping() -> None:
    client = Redis.from_url(os.environ["REDIS_URL"])
    try:
        assert await client.ping() is True
    finally:
        await client.aclose()
