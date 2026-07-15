from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import AccountConnection, ConnectionAuditLog

from .credentials import CredentialCipher, CredentialError
from .providers import ProviderError


DOMAIN_PATTERN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def validate_browser_credentials(values: dict[str, Any]) -> None:
    try:
        _domains(values.get("allowed_domains"))
        _state(values.get("storage_state", "{}"))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ProviderError("browser_session_invalid") from exc


@dataclass(frozen=True)
class BrowserSession:
    connection_id: str
    allowed_domains: tuple[str, ...]
    storage_state: dict[str, Any]


class AccountBackedBrowserSessions:
    def __init__(self, session: AsyncSession, *, cipher: CredentialCipher) -> None:
        self.session = session
        self.cipher = cipher

    async def load(self, *, user_id: str, connection_id: str) -> BrowserSession:
        connection = await self._connection(user_id, connection_id)
        try:
            values = self.cipher.decrypt(connection.credential_ciphertext)
            validate_browser_credentials(values)
            domains = _domains(values.get("allowed_domains"))
            state = _state(values.get("storage_state", "{}"))
        except (CredentialError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("browser_session_invalid") from exc
        return BrowserSession(connection.id, domains, state)

    async def save(
        self, *, user_id: str, connection_id: str, storage_state: dict[str, Any]
    ) -> None:
        connection = await self._connection(user_id, connection_id)
        try:
            values = self.cipher.decrypt(connection.credential_ciphertext)
            _state(storage_state)
            values["storage_state"] = storage_state
            connection.credential_ciphertext = self.cipher.encrypt(values)
        except (CredentialError, ValueError, TypeError) as exc:
            raise ProviderError("browser_session_invalid") from exc
        self.session.add(
            ConnectionAuditLog(
                connection_id=connection.id,
                user_id=user_id,
                action="save_state",
                status="succeeded",
            )
        )
        await self.session.commit()

    async def _connection(self, user_id: str, connection_id: str) -> AccountConnection:
        connection = await self.session.scalar(
            select(AccountConnection).where(
                AccountConnection.id == connection_id,
                AccountConnection.user_id == user_id,
                AccountConnection.provider == "browser",
                AccountConnection.status == "active",
            )
        )
        if connection is None:
            raise ProviderError("connection_unavailable")
        return connection


def _domains(value: object) -> tuple[str, ...]:
    raw = json.loads(value) if isinstance(value, str) and value.strip().startswith("[") else value
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        raise ValueError("browser_allowed_domains_invalid")
    domains = tuple(dict.fromkeys(str(item).strip().lower().rstrip(".") for item in raw))
    if not domains or len(domains) > 20 or any(not DOMAIN_PATTERN.fullmatch(item) for item in domains):
        raise ValueError("browser_allowed_domains_invalid")
    return domains


def _state(value: object) -> dict[str, Any]:
    raw = json.loads(value) if isinstance(value, str) else value
    if not isinstance(raw, dict):
        raise ValueError("browser_storage_state_invalid")
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode()) > 1024 * 1024:
        raise ValueError("browser_storage_state_invalid")
    raw.setdefault("cookies", [])
    raw.setdefault("origins", [])
    return raw
