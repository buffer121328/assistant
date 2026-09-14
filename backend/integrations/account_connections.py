from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from integrations import (
    CREDENTIAL_VERSION,
    CredentialCipher,
    CredentialError,
    ProviderError,
)

from domain.models import AccountConnection, ConnectionAuditLog, User

SUPPORTED_PROVIDERS = frozenset({"smtp", "caldav", "browser"})


class AccountConnectionError(RuntimeError):
    """表示 处理 account connection error 的后端数据结构或服务对象。"""

    def __init__(self, code: str, status_code: int = 400) -> None:
        """初始化对象实例。

        Args:
            code: code 参数。
            status_code: status_code 参数。
        """
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class ConnectionTester(Protocol):
    """表示 处理 connection tester 的后端数据结构或服务对象。"""

    async def test(self, provider: str, credentials: Mapping[str, str]) -> None:
        """测试。

        Args:
            provider: provider 参数。
            credentials: credentials 参数。
        """
        ...


class AccountConnectionRepository:
    """表示 处理 account connection repository 的后端数据结构或服务对象。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
        """
        self.session = session

    async def list_owned(self, user_id: str) -> list[AccountConnection]:
        """列出 owned。

        Args:
            user_id: user_id 参数。
        """
        rows = await self.session.scalars(
            select(AccountConnection)
            .where(AccountConnection.user_id == user_id)
            .order_by(AccountConnection.created_at.desc())
        )
        return list(rows)

    async def get_owned(self, connection_id: str, user_id: str) -> AccountConnection:
        """获取 owned。

        Args:
            connection_id: connection_id 参数。
            user_id: user_id 参数。
        """
        item = await self.session.scalar(
            select(AccountConnection).where(
                AccountConnection.id == connection_id,
                AccountConnection.user_id == user_id,
            )
        )
        if item is None:
            raise AccountConnectionError("connection_not_found", 404)
        return item


class AccountConnectionService:
    """表示 处理 account connection service 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        cipher: CredentialCipher,
        tester: ConnectionTester | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            cipher: cipher 参数。
            tester: tester 参数。
        """
        self.session = session
        self.repository = AccountConnectionRepository(session)
        self.cipher = cipher
        self.tester = tester

    async def create(
        self,
        *,
        user_id: str,
        provider: str,
        display_name: str,
        credentials: Mapping[str, str],
    ) -> AccountConnection:
        """创建。

        Args:
            user_id: user_id 参数。
            provider: provider 参数。
            display_name: display_name 参数。
            credentials: credentials 参数。
        """
        if provider not in SUPPORTED_PROVIDERS:
            raise AccountConnectionError("connection_provider_invalid")
        if not credentials or not all(
            key and value for key, value in credentials.items()
        ):
            raise AccountConnectionError("connection_credentials_invalid")
        if await self.session.get(User, user_id) is None:
            raise AccountConnectionError("user_not_found", 404)
        item = AccountConnection(
            user_id=user_id,
            provider=provider,
            display_name=display_name.strip(),
            credential_ciphertext=self.cipher.encrypt(dict(credentials)),
            credential_version=CREDENTIAL_VERSION,
            status="active",
        )
        self.session.add(item)
        await self.session.flush()
        self._audit(item, "create", "succeeded")
        await self.session.commit()
        return item

    async def list(self, user_id: str) -> list[AccountConnection]:
        """列出。

        Args:
            user_id: user_id 参数。
        """
        return await self.repository.list_owned(user_id)

    async def set_status(
        self, connection_id: str, user_id: str, status: str
    ) -> AccountConnection:
        """处理 set status。

        Args:
            connection_id: connection_id 参数。
            user_id: user_id 参数。
            status: status 参数。
        """
        item = await self.repository.get_owned(connection_id, user_id)
        item.status = status
        if status == "revoked":
            item.credential_ciphertext = self.cipher.encrypt({"revoked": "true"})
        action = "revoke" if status == "revoked" else "disable"
        self._audit(item, action, "succeeded")
        await self.session.commit()
        return item

    async def test(self, connection_id: str, user_id: str) -> AccountConnection:
        """测试。

        Args:
            connection_id: connection_id 参数。
            user_id: user_id 参数。
        """
        item = await self.repository.get_owned(connection_id, user_id)
        if item.status != "active":
            raise AccountConnectionError("connection_inactive", 409)
        if self.tester is None:
            raise AccountConnectionError("connection_tester_unavailable", 503)
        try:
            values = self.cipher.decrypt(item.credential_ciphertext)
            await self.tester.test(
                item.provider, {str(k): str(v) for k, v in values.items()}
            )
        except CredentialError as exc:
            raise AccountConnectionError(
                "connection_credentials_unavailable", 503
            ) from exc
        except ProviderError as exc:
            item.last_checked_at = datetime.now(UTC)
            item.last_error_code = exc.code
            self._audit(item, "test", "failed", exc.code)
            await self.session.commit()
            raise AccountConnectionError(exc.code, 502) from exc
        except Exception as exc:
            item.last_checked_at = datetime.now(UTC)
            item.last_error_code = "connection_test_failed"
            self._audit(item, "test", "failed", item.last_error_code)
            await self.session.commit()
            raise AccountConnectionError("connection_test_failed", 502) from exc
        item.last_checked_at = datetime.now(UTC)
        item.last_error_code = None
        self._audit(item, "test", "succeeded")
        await self.session.commit()
        return item

    def _audit(
        self,
        item: AccountConnection,
        action: str,
        status: str,
        error_code: str | None = None,
    ) -> None:
        """执行 处理 audit 的内部辅助逻辑。

        Args:
            item: item 参数。
            action: action 参数。
            status: status 参数。
            error_code: error_code 参数。
        """
        self.session.add(
            ConnectionAuditLog(
                connection_id=item.id,
                user_id=item.user_id,
                action=action,
                status=status,
                error_code=error_code,
            )
        )
