from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import AccountConnection

from .credentials import CredentialCipher, CredentialError
from .providers import CalDavProvider, ProviderError, SmtpProvider


class AccountBackedProviders:
    """表示 处理 account backed providers 的后端数据结构或服务对象。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        cipher: CredentialCipher,
        smtp: SmtpProvider | None = None,
        caldav: CalDavProvider | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            cipher: cipher 参数。
            smtp: smtp 参数。
            caldav: caldav 参数。
        """
        self.session = session
        self.cipher = cipher
        self.smtp = smtp or SmtpProvider()
        self.caldav = caldav or CalDavProvider()

    async def send(
        self,
        *,
        user_id: str,
        connection_id: str,
        recipients: tuple[str, ...],
        subject: str,
        body: str,
    ) -> str:
        """处理 send。

        Args:
            user_id: user_id 参数。
            connection_id: connection_id 参数。
            recipients: recipients 参数。
            subject: subject 参数。
            body: body 参数。
        """
        credentials = await self._credentials(user_id, connection_id, "smtp")
        return await self.smtp.send(
            credentials,
            recipients=recipients,
            subject=subject,
            body=body,
        )

    async def create_event(
        self,
        *,
        user_id: str,
        connection_id: str,
        title: str,
        start: str,
        end: str,
        description: str,
        idempotency_key: str,
    ) -> str:
        """创建 event。

        Args:
            user_id: user_id 参数。
            connection_id: connection_id 参数。
            title: title 参数。
            start: start 参数。
            end: end 参数。
            description: description 参数。
            idempotency_key: idempotency_key 参数。
        """
        credentials = await self._credentials(user_id, connection_id, "caldav")
        return await self.caldav.create_event(
            credentials,
            title=title,
            start=start,
            end=end,
            description=description,
            idempotency_key=idempotency_key,
        )

    async def _credentials(
        self, user_id: str, connection_id: str, provider: str
    ) -> dict[str, str]:
        """执行 处理 credentials 的内部辅助逻辑。

        Args:
            user_id: user_id 参数。
            connection_id: connection_id 参数。
            provider: provider 参数。
        """
        connection = await self.session.scalar(
            select(AccountConnection).where(
                AccountConnection.id == connection_id,
                AccountConnection.user_id == user_id,
                AccountConnection.provider == provider,
                AccountConnection.status == "active",
            )
        )
        if connection is None:
            raise ProviderError("connection_unavailable")
        try:
            values = self.cipher.decrypt(connection.credential_ciphertext)
        except CredentialError as exc:
            raise ProviderError("connection_credentials_unavailable") from exc
        return {str(key): str(value) for key, value in values.items()}


async def active_connection_providers(
    session: AsyncSession, user_id: str
) -> frozenset[str]:
    """处理 active connection providers。

    Args:
        session: session 参数。
        user_id: user_id 参数。
    """
    providers = await session.scalars(
        select(AccountConnection.provider).where(
            AccountConnection.user_id == user_id,
            AccountConnection.status == "active",
        )
    )
    return frozenset(providers)
