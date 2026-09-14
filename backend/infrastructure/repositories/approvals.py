from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import (
    AgentRun,
    Approval,
    ApprovalStatus,
    ApprovalType,
    Task,
    Tenant,
)


class ApprovalRepository:
    """表示 处理 approval repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def create_pending(self, *, task_id: str, tool_name: str) -> Approval:
        """创建 pending。

        Args:
            task_id: task_id 参数。
            tool_name: tool_name 参数。
        """
        return await self.create_pending_request(
            task_id=task_id,
            approval_type=ApprovalType.TOOL.value,
            subject=tool_name,
            tool_name=tool_name,
            request_summary=f"工具调用：{tool_name}",
        )

    async def create_pending_request(
        self,
        *,
        task_id: str,
        approval_type: str,
        subject: str,
        tool_name: str,
        request_summary: str | None,
        request_fingerprint: str | None = None,
        capability_key: str | None = None,
        policy_decision: str | None = None,
        allowed_decisions_json: str = '["approve_once","reject"]',
        expires_at: datetime | None = None,
        run_id: str | None = None,
        authority_revision: int | None = None,
    ) -> Approval:
        """创建 pending request。

        Args:
            task_id: task_id 参数。
            approval_type: approval_type 参数。
            subject: subject 参数。
            tool_name: tool_name 参数。
            request_summary: request_summary 参数。
        """
        if run_id is None:
            run_id = await self.session.scalar(
                select(AgentRun.id)
                .where(AgentRun.task_id == task_id)
                .order_by(AgentRun.started_at.desc())
                .limit(1)
            )
        if authority_revision is None:
            tenant_id = await self.session.scalar(
                select(Task.tenant_id).where(Task.id == task_id)
            )
            if tenant_id is not None:
                authority_revision = await self.session.scalar(
                    select(Tenant.authority_revision).where(Tenant.id == tenant_id)
                )
        approval = Approval(
            task_id=task_id,
            run_id=run_id,
            tool_name=tool_name,
            approval_type=approval_type,
            subject=subject,
            request_summary=request_summary,
            request_fingerprint=request_fingerprint,
            capability_key=capability_key,
            policy_decision=policy_decision,
            authority_revision=authority_revision,
            allowed_decisions_json=allowed_decisions_json,
            expires_at=expires_at,
            status=ApprovalStatus.PENDING.value,
        )
        self.session.add(approval)
        await self.session.flush()
        return approval

    async def get_active_for_tool(
        self,
        *,
        task_id: str,
        tool_name: str,
    ) -> Approval | None:
        """获取 active for tool。

        Args:
            task_id: task_id 参数。
            tool_name: tool_name 参数。
        """
        return await self.session.scalar(
            select(Approval).where(
                Approval.task_id == task_id,
                Approval.tool_name == tool_name,
                Approval.approval_type == ApprovalType.TOOL.value,
                Approval.subject == tool_name,
                Approval.status.in_(
                    (
                        ApprovalStatus.PENDING.value,
                        ApprovalStatus.APPROVED.value,
                    )
                ),
            )
        )

    async def get_active_for_request(
        self,
        *,
        task_id: str,
        approval_type: str,
        subject: str,
    ) -> Approval | None:
        """获取 active for request。

        Args:
            task_id: task_id 参数。
            approval_type: approval_type 参数。
            subject: subject 参数。
        """
        return await self.session.scalar(
            select(Approval).where(
                Approval.task_id == task_id,
                Approval.approval_type == approval_type,
                Approval.subject == subject,
                Approval.status.in_(
                    (
                        ApprovalStatus.PENDING.value,
                        ApprovalStatus.APPROVED.value,
                    )
                ),
            )
        )

    async def get_by_task(
        self,
        *,
        approval_id: str,
        task_id: str,
    ) -> Approval | None:
        """获取 by task。

        Args:
            approval_id: approval_id 参数。
            task_id: task_id 参数。
        """
        return await self.session.scalar(
            select(Approval).where(
                Approval.id == approval_id,
                Approval.task_id == task_id,
            )
        )

    async def list_by_task(self, task_id: str) -> list[Approval]:
        """列出 by task。

        Args:
            task_id: task_id 参数。
        """
        result = await self.session.scalars(
            select(Approval)
            .where(Approval.task_id == task_id)
            .order_by(Approval.created_at.asc(), Approval.id.asc())
        )
        return list(result)
