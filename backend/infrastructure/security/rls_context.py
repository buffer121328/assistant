from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domain.policies.enterprise import SubjectContext


async def set_governance_context(
    session: AsyncSession,
    *,
    tenant_id: str,
    organization_ids: Iterable[str] = (),
) -> None:
    """Set transaction-local governance context for PostgreSQL RLS.

    Args:
        session: 当前数据库异步会话。
        tenant_id: 用于执行当前操作的 tenant id 参数。
        organization_ids: 用于执行当前操作的 organization ids 参数。
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    if not tenant_id or len(tenant_id) > 128:
        raise ValueError("tenant_id is required and bounded")
    orgs = tuple(item for item in organization_ids if item)
    await session.execute(
        text("select set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )
    await session.execute(
        text("select set_config('app.organization_ids', :organization_ids, true)"),
        {"organization_ids": ",".join(orgs)[:4_000]},
    )


async def set_subject_governance_context(
    session: AsyncSession, subject: SubjectContext
) -> None:
    """Bind an already server-resolved subject to the current transaction.

    Args:
        session: 当前数据库异步会话。
        subject: 用于执行当前操作的 subject 参数。
    """
    await set_governance_context(
        session,
        tenant_id=subject.tenant_id,
        organization_ids=subject.organization_ids,
    )
