from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import scrypt, sha256
from hmac import compare_digest
import re
from secrets import token_bytes, token_urlsafe

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from infrastructure.settings.config import Settings, load_settings

from application.enterprise_governance import EnterpriseGovernanceService
from domain.models import (
    GovernanceAudit,
    LocalCredential,
    LocalSession,
    OrganizationMembership,
    Tenant,
    User,
)
from domain.models.base import utc_now
from domain.policies.enterprise import (
    GovernedRole,
    LOCAL_ORGANIZATION_ID,
    LOCAL_TENANT_ID,
)

_LOGIN_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_PASSWORD_MIN_LENGTH = 12
_PASSWORD_MAX_LENGTH = 128
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SESSION_TTL = timedelta(hours=12)


class LocalAuthError(Exception):
    """定义当前组件可安全处理的错误类型。"""

    def __init__(self, code: str, status_code: int) -> None:
        """初始化对象所需的运行时依赖和受限状态。

        Args:
            code: 可安全返回给调用方的错误码。
            status_code: 可安全返回给调用方的 HTTP 状态码。
        """
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class AuthenticatedSession:
    """定义当前组件的职责和边界。"""

    token: str  # token 对应的数据字段。
    session_id: str  # session_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    tenant_id: str  # tenant_id 对应的数据字段。
    expires_at: datetime  # expires_at 对应的数据字段。


class LocalAuthenticationService:
    """定义当前组件的职责和边界。"""

    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        """初始化对象所需的运行时依赖和受限状态。

        Args:
            session: 当前数据库异步会话。
        """
        self._session = session
        self._settings = settings or load_settings()

    async def recovery_status(self) -> dict[str, object]:
        """Return non-sensitive availability information for emergency recovery."""
        configured = bool(
            self._settings.admin_recovery_enabled
            and self._settings.admin_recovery_question.strip()
            and self._settings.admin_recovery_answer_verifier.get_secret_value().strip()
        )
        available = False
        if configured:
            available = bool(await self._session.scalar(
                select(func.count(LocalCredential.id))
                .join(OrganizationMembership, OrganizationMembership.user_id == LocalCredential.user_id)
                .where(
                    LocalCredential.tenant_id == LOCAL_TENANT_ID,
                    LocalCredential.status == "active",
                    LocalCredential.recovery_consumed_at.is_(None),
                    OrganizationMembership.organization_id == LOCAL_ORGANIZATION_ID,
                    OrganizationMembership.role == GovernedRole.ENTERPRISE_ADMIN.value,
                    OrganizationMembership.status == "active",
                )
            ))
        return {"enabled": available, "question": self._settings.admin_recovery_question.strip() if available else None}

    async def recover_password(self, *, login_name: str, answer: str, new_password: str) -> AuthenticatedSession:
        """Replace one local administrator password using a configured one-time verifier."""
        if not (await self.recovery_status())["enabled"]:
            raise LocalAuthError("recovery_unavailable", 404)
        login = _normalized_login_name(login_name)
        _validated_password(new_password)
        now = utc_now()
        credential = await self._session.scalar(
            select(LocalCredential).join(
                OrganizationMembership,
                OrganizationMembership.user_id == LocalCredential.user_id,
            ).where(
                LocalCredential.tenant_id == LOCAL_TENANT_ID,
                LocalCredential.login_name == login,
                LocalCredential.status == "active",
                OrganizationMembership.organization_id == LOCAL_ORGANIZATION_ID,
                OrganizationMembership.role == GovernedRole.ENTERPRISE_ADMIN.value,
                OrganizationMembership.status == "active",
            ).with_for_update()
        )
        verifier = self._settings.admin_recovery_answer_verifier.get_secret_value()
        locked_until = _as_utc(credential.recovery_locked_until) if credential and credential.recovery_locked_until else None
        valid = credential is not None and credential.recovery_consumed_at is None and (locked_until is None or locked_until <= now) and _verify_recovery_answer(answer, verifier)
        if not valid:
            if credential is not None and credential.recovery_consumed_at is None:
                if locked_until is not None and locked_until > now:
                    pass
                else:
                    credential.recovery_failed_attempt_count += 1
                    if credential.recovery_failed_attempt_count >= max(1, self._settings.admin_recovery_max_attempts):
                        credential.recovery_locked_until = now + timedelta(minutes=max(1, self._settings.admin_recovery_cooldown_minutes))
                    await self._record_audit(user_id=credential.user_id, result_status="recovery_failed", resource_id=credential.user_id)
                    await self._session.flush()
            raise LocalAuthError("recovery_failed", 401)
        user = await self._session.get(User, credential.user_id)
        if user is None:
            raise LocalAuthError("recovery_failed", 401)
        credential.password_verifier = _password_verifier(new_password)
        credential.password_version = "scrypt.v1"
        credential.recovery_consumed_at = now
        credential.recovery_locked_until = None
        credential.recovery_failed_attempt_count = 0
        await self._session.execute(
            LocalSession.__table__.update().where(
                LocalSession.user_id == user.id,
                LocalSession.revoked_at.is_(None),
            ).values(revoked_at=now)
        )
        await self._record_audit(user_id=user.id, result_status="password_recovered", resource_id=user.id)
        await self._session.flush()
        return await self._issue_session(user)

    async def initialization_required(self) -> bool:
        """执行当前组件定义的业务处理逻辑。"""
        count = await self._session.scalar(
            select(func.count(LocalCredential.id)).where(
                LocalCredential.tenant_id == LOCAL_TENANT_ID,
                LocalCredential.status == "active",
            )
        )
        return not bool(count)

    async def initialize_administrator(
        self,
        *,
        display_name: str,
        login_name: str,
        password: str,
    ) -> AuthenticatedSession:
        """Create the only first local administrator, then issue a new session.

        Args:
            display_name: 用于执行当前操作的 display name 参数。
            login_name: 用于执行当前操作的 login name 参数。
            password: 用于执行当前操作的 password 参数。
        """
        display = _validated_display_name(display_name)
        login = _validated_login_name(login_name)
        _validated_password(password)
        foundation = EnterpriseGovernanceService(self._session)
        await foundation.ensure_local_foundation()
        tenant = await self._session.scalar(
            select(Tenant)
            .where(Tenant.id == LOCAL_TENANT_ID)
            .with_for_update()
        )
        if tenant is None:
            raise LocalAuthError("initialization_unavailable", 409)
        if not await self.initialization_required():
            raise LocalAuthError("initialization_unavailable", 409)
        user = User(tenant_id=tenant.id, display_name=display)
        self._session.add(user)
        await self._session.flush()
        self._session.add(
            LocalCredential(
                tenant_id=tenant.id,
                user_id=user.id,
                login_name=login,
                password_verifier=_password_verifier(password),
                password_version="scrypt.v1",
                status="active",
            )
        )
        self._session.add(
            OrganizationMembership(
                tenant_id=tenant.id,
                organization_id=LOCAL_ORGANIZATION_ID,
                user_id=user.id,
                role=GovernedRole.ENTERPRISE_ADMIN.value,
                status="active",
            )
        )
        await self._record_audit(
            user_id=user.id,
            result_status="initialized",
            resource_id=user.id,
        )
        await self._session.flush()
        return await self._issue_session(user)

    async def provision_member(
        self,
        *,
        display_name: str,
        login_name: str,
        password: str,
        organization_id: str,
        role: GovernedRole,
    ) -> User:
        """Create one local employee account and its initial governed membership.

        This method accepts no actor claim; the enterprise-admin facade authorizes
        callers before invoking it. Password material is immediately replaced by
        the versioned verifier and is never returned or written to audit data.

        Args:
            display_name: 用于执行当前操作的 display name 参数。
            login_name: 用于执行当前操作的 login name 参数。
            password: 用于执行当前操作的 password 参数。
            organization_id: 用于执行当前操作的 organization id 参数。
            role: 用于执行当前操作的 role 参数。
        """
        display = _validated_display_name(display_name)
        login = _validated_login_name(login_name)
        _validated_password(password)
        foundation = EnterpriseGovernanceService(self._session)
        tenant, _ = await foundation.ensure_local_foundation()
        existing = await self._session.scalar(
            select(LocalCredential).where(
                LocalCredential.tenant_id == tenant.id,
                LocalCredential.login_name == login,
            )
        )
        if existing is not None:
            raise LocalAuthError("local_account_exists", 409)
        user = User(tenant_id=tenant.id, display_name=display)
        self._session.add(user)
        await self._session.flush()
        self._session.add(
            LocalCredential(
                tenant_id=tenant.id,
                user_id=user.id,
                login_name=login,
                password_verifier=_password_verifier(password),
                password_version="scrypt.v1",
                status="active",
            )
        )
        await foundation.add_membership(
            tenant_id=tenant.id,
            organization_id=organization_id,
            user_id=user.id,
            role=role,
        )
        await self._record_audit(
            user_id=user.id,
            result_status="provisioned",
            resource_id=user.id,
        )
        await self._session.flush()
        return user

    async def authenticate(self, *, login_name: str, password: str) -> AuthenticatedSession:
        """Verify local credentials while returning one failure shape for all invalid input.

        Args:
            login_name: 用于执行当前操作的 login name 参数。
            password: 用于执行当前操作的 password 参数。
        """
        login = _normalized_login_name(login_name)
        credential = await self._session.scalar(
            select(LocalCredential).where(
                LocalCredential.tenant_id == LOCAL_TENANT_ID,
                LocalCredential.login_name == login,
                LocalCredential.status == "active",
            )
        )
        if credential is None or not _verify_password(password, credential.password_verifier):
            if credential is not None:
                credential.failed_attempt_count = min(credential.failed_attempt_count + 1, 99)
                credential.last_failed_at = utc_now()
                await self._record_audit(
                    user_id=credential.user_id,
                    result_status="failed",
                    resource_id=credential.user_id,
                )
            else:
                await self._record_audit(
                    user_id="anonymous",
                    result_status="failed",
                    resource_id=None,
                )
            await self._session.flush()
            raise LocalAuthError("invalid_credentials", 401)
        user = await self._session.get(User, credential.user_id)
        if user is None or user.tenant_id != LOCAL_TENANT_ID:
            raise LocalAuthError("invalid_credentials", 401)
        credential.failed_attempt_count = 0
        credential.last_failed_at = None
        await self._record_audit(
            user_id=user.id,
            result_status="succeeded",
            resource_id=user.id,
        )
        await self._session.flush()
        return await self._issue_session(user)

    async def session_for_token(self, token: str) -> AuthenticatedSession:
        """Validate the opaque session token and return only server-resolved identifiers.

        Args:
            token: 用于执行当前操作的 token 参数。
        """
        digest = _token_digest(token)
        record = await self._session.scalar(
            select(LocalSession).where(LocalSession.token_digest == digest)
        )
        now = datetime.now(UTC)
        if (
            record is None
            or record.revoked_at is not None
            or _as_utc(record.expires_at) <= now
        ):
            raise LocalAuthError("session_invalid", 401)
        user = await self._session.get(User, record.user_id)
        if user is None or user.tenant_id != record.tenant_id:
            raise LocalAuthError("session_invalid", 401)
        record.last_seen_at = now
        await self._session.flush()
        return AuthenticatedSession(
            token="",
            session_id=record.id,
            user_id=user.id,
            tenant_id=user.tenant_id,
            expires_at=record.expires_at,
        )

    async def logout(self, token: str) -> None:
        """Revoke a current opaque session without indicating whether it previously existed.

        Args:
            token: 用于执行当前操作的 token 参数。
        """
        digest = _token_digest(token)
        record = await self._session.scalar(
            select(LocalSession).where(LocalSession.token_digest == digest)
        )
        if record is None or record.revoked_at is not None:
            return
        record.revoked_at = utc_now()
        await self._record_audit(
            user_id=record.user_id,
            result_status="revoked",
            resource_id=record.id,
        )
        await self._session.flush()

    async def _issue_session(self, user: User) -> AuthenticatedSession:
        """执行 issue session 的内部处理逻辑。

        Args:
            user: 用于执行当前操作的 user 参数。
        """
        token = token_urlsafe(48)
        expires_at = datetime.now(UTC) + _SESSION_TTL
        record = LocalSession(
            tenant_id=user.tenant_id,
            user_id=user.id,
            token_digest=_token_digest(token),
            expires_at=expires_at,
            last_seen_at=utc_now(),
        )
        self._session.add(record)
        await self._session.flush()
        return AuthenticatedSession(
            token=token,
            session_id=record.id,
            user_id=user.id,
            tenant_id=user.tenant_id,
            expires_at=expires_at,
        )

    async def _record_audit(
        self,
        *,
        user_id: str,
        result_status: str,
        resource_id: str | None,
    ) -> None:
        """Write a bounded audit fact without password, login, or session secret material.

        Args:
            user_id: 目标用户 ID。
            result_status: 用于执行当前操作的 result status 参数。
            resource_id: 用于执行当前操作的 resource id 参数。
        """
        self._session.add(
            GovernanceAudit(
                tenant_id=LOCAL_TENANT_ID,
                subject_id=user_id,
                organization_id=LOCAL_ORGANIZATION_ID if user_id != "anonymous" else None,
                conversation_id=None,
                task_id=None,
                run_id=None,
                agent_id=None,
                capability_key=None,
                tool_key="local_auth",
                provider_key=None,
                resource_type="local_session",
                resource_id=resource_id,
                risk="R1",
                policy_decision="ALLOW" if result_status != "failed" else "DENY",
                approval_id=None,
                arguments_hash=None,
                result_status=result_status,
                model_name=None,
                cost_usd=None,
                summary="local_auth_event",
                created_at=utc_now(),
            )
        )


def _normalized_login_name(value: str) -> str:
    """执行 normalized login name 的内部处理逻辑。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    return value.strip().lower()


def _validated_login_name(value: str) -> str:
    """执行 validated login name 的内部处理逻辑。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    normalized = _normalized_login_name(value)
    if not _LOGIN_NAME.fullmatch(normalized):
        raise LocalAuthError("invalid_login_input", 422)
    return normalized


def _validated_display_name(value: str) -> str:
    """执行 validated display name 的内部处理逻辑。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    display = value.strip()
    if not 1 <= len(display) <= 120:
        raise LocalAuthError("invalid_display_name", 422)
    return display


def _validated_password(value: str) -> None:
    """执行 validated password 的内部处理逻辑。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    if not _PASSWORD_MIN_LENGTH <= len(value) <= _PASSWORD_MAX_LENGTH:
        raise LocalAuthError("invalid_password_input", 422)


def _password_verifier(password: str) -> str:
    """执行 password verifier 的内部处理逻辑。

    Args:
        password: 用于执行当前操作的 password 参数。
    """
    _validated_password(password)
    salt = token_bytes(16)
    digest = scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=64 * 1024 * 1024,
    )
    return "$".join(
        (
            "scrypt.v1",
            str(_SCRYPT_N),
            str(_SCRYPT_R),
            str(_SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def _verify_password(password: str, verifier: str) -> bool:
    """执行 verify password 的内部处理逻辑。

    Args:
        password: 用于执行当前操作的 password 参数。
        verifier: 用于执行当前操作的 verifier 参数。
    """
    if not _PASSWORD_MIN_LENGTH <= len(password) <= _PASSWORD_MAX_LENGTH:
        return False
    try:
        version, raw_n, raw_r, raw_p, encoded_salt, encoded_digest = verifier.split("$", 5)
        if version != "scrypt.v1":
            return False
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        if (n, r, p) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P):
            return False
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        actual = scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=64 * 1024 * 1024,
        )
    except (ValueError, TypeError):
        return False
    return compare_digest(actual, expected)


def _verify_recovery_answer(answer: str, verifier: str) -> bool:
    """Verify the configured recovery answer using the same scrypt envelope."""
    if not answer or len(answer) > 256:
        return False
    return _verify_scrypt_value(answer.strip().lower(), verifier)


def _verify_scrypt_value(value: str, verifier: str) -> bool:
    try:
        version, raw_n, raw_r, raw_p, encoded_salt, encoded_digest = verifier.split("$", 5)
        if version != "scrypt.v1":
            return False
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        if (n, r, p) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P):
            return False
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        actual = scrypt(value.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected), maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return compare_digest(actual, expected)


def _as_utc(value: datetime) -> datetime:
    """执行 as utc 的内部处理逻辑。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _token_digest(token: str) -> str:
    """执行 token digest 的内部处理逻辑。

    Args:
        token: 用于执行当前操作的 token 参数。
    """
    return sha256(token.encode("utf-8")).hexdigest()
