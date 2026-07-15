from __future__ import annotations

import base64
from hashlib import sha256
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

CREDENTIAL_VERSION = "fernet-v1"
MIN_MASTER_KEY_CHARS = 32


class CredentialError(RuntimeError):
    """Safe credential boundary error without secret material."""


class CredentialCipher:
    def __init__(self, master_key: str) -> None:
        normalized = master_key.strip()
        if len(normalized) < MIN_MASTER_KEY_CHARS:
            raise CredentialError("credential_master_key_invalid")
        derived = base64.urlsafe_b64encode(sha256(normalized.encode()).digest())
        self._fernet = Fernet(derived)

    def encrypt(self, values: dict[str, Any]) -> str:
        try:
            payload = json.dumps(
                values,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError) as exc:
            raise CredentialError("credential_payload_invalid") from exc
        return self._fernet.encrypt(payload).decode()

    def decrypt(self, ciphertext: str) -> dict[str, Any]:
        try:
            payload = json.loads(self._fernet.decrypt(ciphertext.encode()))
        except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise CredentialError("credential_decryption_failed") from exc
        if not isinstance(payload, dict):
            raise CredentialError("credential_payload_invalid")
        return payload
