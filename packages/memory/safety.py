from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal


Sensitivity = Literal["public", "personal", "sensitive", "forbidden"]

_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("authorization", re.compile(r"\b(?:authorization|proxy-authorization)\s*:", re.I)),
    ("cookie", re.compile(r"\b(?:cookie|set-cookie)\s*:", re.I)),
    ("bearer_token", re.compile(r"\bbearer\s+\S+", re.I)),
    (
        "credential_assignment",
        re.compile(
            r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret)\s*[:=]\s*\S+",
            re.I,
        ),
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    ),
    ("recovery_code", re.compile(r"\b(?:recovery|backup)\s+code\s*[:=]", re.I)),
    ("provider_key", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")),
)


@dataclass(frozen=True)
class MemorySafetyResult:
    sensitivity: Sensitivity
    reason_code: str | None = None


def normalize_memory_content(content: str) -> str:
    return " ".join(content.strip().split())


def memory_content_hash(content: str) -> str:
    normalized = normalize_memory_content(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def classify_memory_sensitivity(content: str) -> MemorySafetyResult:
    normalized = normalize_memory_content(content)
    for reason_code, pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(normalized):
            return MemorySafetyResult("forbidden", reason_code)
    return MemorySafetyResult("public")
