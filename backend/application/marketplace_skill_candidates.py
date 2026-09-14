from __future__ import annotations

from hashlib import sha256
import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.enterprise_runtime import require_admin_role
from domain.models import SkillAuditLog, SkillDefinition, SkillDraft
from domain.policies.enterprise import (
    GovernedRole,
    GovernanceValidationError,
    SubjectContext,
)


_SAFE_CANDIDATE_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_MAX_DISPLAY_NAME = 120
_MAX_SUMMARY = 500
_CANDIDATE_STATUS = "candidate_disabled"


class MarketplaceSkillCandidateService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession) -> None:
        """Bind candidate persistence and bounded lifecycle audit to one session.

        Args:
            session: 当前数据库异步会话。
        """
        self.session = session

    async def record(
        self,
        subject: SubjectContext,
        *,
        key: str,
        display_name: str,
        summary: str,
    ) -> dict[str, object]:
        """Store one validated, disabled candidate for an authorized publisher.

        Enterprise administrators and capability publishers may record metadata.
        The operation deliberately accepts no package, URL, credentials, or code
        and emits a bounded Skill audit row rather than enabling a Skill.

        Args:
            subject: 用于执行当前操作的 subject 参数。
            key: 用于执行当前操作的 key 参数。
            display_name: 用于执行当前操作的 display name 参数。
            summary: 用于执行当前操作的 summary 参数。
        """
        require_admin_role(
            subject,
            GovernedRole.ENTERPRISE_ADMIN,
            GovernedRole.CAPABILITY_PUBLISHER,
        )
        normalized_key = self._key(key)
        normalized_display_name = self._display_name(display_name)
        normalized_summary = self._summary(summary)
        payload = {
            "acquired": False,
            "catalog_source": "marketplace",
            "enabled": False,
            "summary": normalized_summary,
        }
        serialized_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        existing = await self.session.scalar(
            select(SkillDefinition).where(
                SkillDefinition.tenant_id == subject.tenant_id,
                SkillDefinition.key == normalized_key,
            )
        )
        if existing is not None:
            return await self._existing_candidate(
                existing,
                display_name=normalized_display_name,
                content=serialized_payload,
            )

        skill = SkillDefinition(
            tenant_id=subject.tenant_id,
            key=normalized_key,
            display_name=normalized_display_name,
            stable_version=None,
            status=_CANDIDATE_STATUS,
        )
        self.session.add(skill)
        await self.session.flush()
        self.session.add(
            SkillDraft(
                tenant_id=subject.tenant_id,
                skill_id=skill.id,
                content_json=serialized_payload,
                content_digest=sha256(serialized_payload.encode("utf-8")).hexdigest(),
                test_status="candidate_unreviewed",
                tested_digest=None,
                updated_by=subject.user_id,
            )
        )
        self.session.add(
            SkillAuditLog(
                actor_user_id=subject.user_id,
                skill_name=skill.key,
                action="candidate_record",
                status="disabled",
                version=None,
                error_code=None,
            )
        )
        await self.session.flush()
        return self._response(skill)

    async def _existing_candidate(
        self,
        skill: SkillDefinition,
        *,
        display_name: str,
        content: str,
    ) -> dict[str, object]:
        """Allow an exact retry but reject attempts to overwrite published Skills.

        Args:
            skill: 用于执行当前操作的 skill 参数。
            display_name: 用于执行当前操作的 display name 参数。
            content: 用于执行当前操作的 content 参数。
        """
        draft = await self.session.scalar(
            select(SkillDraft).where(SkillDraft.skill_id == skill.id)
        )
        if (
            skill.status != _CANDIDATE_STATUS
            or skill.stable_version is not None
            or draft is None
            or skill.display_name != display_name
            or draft.content_json != content
        ):
            raise GovernanceValidationError("Marketplace Skill candidate conflicts")
        return self._response(skill)

    @staticmethod
    def _key(value: str) -> str:
        """Validate a stable candidate key without accepting path-like input.

        Args:
            value: 待校验、归一化或转换的输入值。
        """
        normalized = value.strip()
        if not _SAFE_CANDIDATE_KEY.fullmatch(normalized) or len(normalized) > 128:
            raise GovernanceValidationError("Marketplace Skill candidate key is invalid")
        return normalized

    @staticmethod
    def _display_name(value: str) -> str:
        """Validate bounded display text that is safe to return to admin clients.

        Args:
            value: 待校验、归一化或转换的输入值。
        """
        normalized = value.strip()
        if not normalized or len(normalized) > _MAX_DISPLAY_NAME:
            raise GovernanceValidationError("Marketplace Skill candidate name is invalid")
        return normalized

    @staticmethod
    def _summary(value: str) -> str:
        """Validate bounded candidate metadata without accepting implementation content.

        Args:
            value: 待校验、归一化或转换的输入值。
        """
        normalized = value.strip()
        if not normalized or len(normalized) > _MAX_SUMMARY:
            raise GovernanceValidationError("Marketplace Skill candidate summary is invalid")
        return normalized

    @staticmethod
    def _response(skill: SkillDefinition) -> dict[str, object]:
        """Return the explicit non-executable state without draft implementation data.

        Args:
            skill: 用于执行当前操作的 skill 参数。
        """
        return {
            "id": skill.id,
            "key": skill.key,
            "display_name": skill.display_name,
            "status": skill.status,
            "stable_version": skill.stable_version,
            "distributed": False,
            "executable": False,
        }
