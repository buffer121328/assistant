from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_database_engine(database_url: str) -> AsyncEngine:
    """创建 database engine。

    Args:
        database_url: database_url 参数。
    """
    return create_async_engine(database_url)


def create_database_sessionmaker(
    database_url: str,
) -> async_sessionmaker[AsyncSession]:
    """创建 database sessionmaker。

    Args:
        database_url: database_url 参数。
    """
    engine = create_database_engine(database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """获取 session。

    Args:
        request: request 参数。
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.db_sessionmaker
    async with sessionmaker() as session:
        yield session
