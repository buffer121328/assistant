from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import AccountConnection

from .credentials import CredentialCipher, CredentialError
from .providers import CalDavProvider, ProviderError, SmtpProvider


class AccountBackedProviders:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cipher: CredentialCipher,
        smtp: SmtpProvider | None = None,
        caldav: CalDavProvider | None = None,
    ) -> None:
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
    providers = await session.scalars(
        select(AccountConnection.provider).where(
            AccountConnection.user_id == user_id,
            AccountConnection.status == "active",
        )
    )
    return frozenset(providers)
