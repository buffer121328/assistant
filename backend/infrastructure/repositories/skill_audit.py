from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import SkillAuditLog, User


class SkillAuditRepository:
    """表示 处理 skill audit repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def user_exists(self, user_id: str) -> bool:
        """处理 user exists。

        Args:
            user_id: user_id 参数。
        """
        return await self.session.get(User, user_id) is not None

    async def create_started(
        self,
        *,
        actor_user_id: str,
        skill_name: str | None,
        action: str,
    ) -> SkillAuditLog:
        """创建 started。

        Args:
            actor_user_id: actor_user_id 参数。
            skill_name: skill_name 参数。
            action: action 参数。
        """
        audit = SkillAuditLog(
            actor_user_id=actor_user_id,
            skill_name=skill_name,
            action=action,
            status="started",
        )
        self.session.add(audit)
        await self.session.flush()
        return audit

    async def finish(
        self,
        audit_id: str,
        *,
        status: str,
        skill_name: str | None,
        version: str | None,
        error_code: str | None,
    ) -> SkillAuditLog:
        """处理 finish。

        Args:
            audit_id: audit_id 参数。
            status: status 参数。
            skill_name: skill_name 参数。
            version: version 参数。
            error_code: error_code 参数。
        """
        audit = await self.session.get(SkillAuditLog, audit_id)
        if audit is None:
            raise RuntimeError("Skill audit record is unavailable")
        audit.status = status
        audit.skill_name = skill_name
        audit.version = version
        audit.error_code = error_code
        await self.session.flush()
        return audit
