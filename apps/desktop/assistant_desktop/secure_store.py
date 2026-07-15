from __future__ import annotations

from typing import Protocol

import keyring
from keyring.errors import KeyringError

SERVICE_NAME = "personal-agent-assistant"


class SecureStoreError(RuntimeError):
    pass


class TokenStore(Protocol):
    def get(self, *, base_url: str, user_id: str) -> str: ...

    def set(self, *, base_url: str, user_id: str, token: str) -> None: ...


class KeyringTokenStore:
    def get(self, *, base_url: str, user_id: str) -> str:
        try:
            return keyring.get_password(SERVICE_NAME, _account(base_url, user_id)) or ""
        except KeyringError as exc:
            raise SecureStoreError("无法读取系统凭据库。") from exc

    def set(self, *, base_url: str, user_id: str, token: str) -> None:
        normalized = token.strip()
        if not normalized:
            raise SecureStoreError("本机 API Token 不能为空。")
        try:
            keyring.set_password(SERVICE_NAME, _account(base_url, user_id), normalized)
        except KeyringError as exc:
            raise SecureStoreError("无法写入系统凭据库。") from exc


def _account(base_url: str, user_id: str) -> str:
    return f"{base_url}|{user_id}"
