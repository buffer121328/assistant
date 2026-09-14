"""数据库持久化：异步引擎、会话工厂与请求级会话依赖。"""
from collections.abc import AsyncIterator
import json

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from infrastructure.security.rls_context import set_subject_governance_context


def create_database_engine(database_url: str) -> AsyncEngine:
    """创建异步数据库引擎。

    Args:
        database_url: 数据库连接串（如 postgresql+asyncpg://...）。
    """
    return create_async_engine(database_url)


def create_database_sessionmaker(
    database_url: str,
) -> async_sessionmaker[AsyncSession]:
    """创建异步会话工厂（提交后不过期对象）。

    Args:
        database_url: 数据库连接串。
    """
    engine = create_database_engine(database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：为请求提供绑定治理上下文的数据库会话。

    Args:
        request: 入站 HTTP 请求（用于读取用户标识与应用状态）。
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.db_sessionmaker
    async with sessionmaker() as session:
        # ① 解析请求用户：优先 query 参数，其次缓存过的请求体（POST/PUT/PATCH）
        user_id = request.query_params.get("user_id")
        cached_body = getattr(request, "_body", None)
        if user_id is None and cached_body is not None and request.method in {"POST", "PUT", "PATCH"}:
            try:
                payload = json.loads(cached_body.decode("utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("user_id"), str):
                    user_id = payload["user_id"]
            except (UnicodeDecodeError, json.JSONDecodeError):
                user_id = None
        if user_id:
            try:
                from application.enterprise_governance import resolve_local_subject

                # ② 把服务端解析的本地主体绑定到本事务的 RLS 上下文
                subject = await resolve_local_subject(session, user_id)
                await set_subject_governance_context(session, subject)
            except Exception:
                # 路由层授权仍负责报错；上下文设置不得让"缺用户"演变成数据泄漏。
                await session.rollback()
        yield session
