from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json
import re
from typing import Any

MODEL_CLASS_LIGHT = "light"
MODEL_CLASS_STANDARD = "standard"
PROVIDER_DEEPSEEK = "deepseek"

MODEL_GATEWAY_VALIDATION_ERROR = "model_gateway_validation_error"
MODEL_GATEWAY_UNSUPPORTED_MODEL = "model_gateway_unsupported_model"
MODEL_GATEWAY_TIMEOUT = "model_gateway_timeout"
MODEL_GATEWAY_PROVIDER_ERROR = "model_gateway_provider_error"

LIGHT_TASK_TYPES = frozenset(
    {
        "router",
        "memory_extract",
        "status_summary",
        "card_render",
    }
)
STANDARD_TASK_TYPES = frozenset(
    {
        "plan",
        "learn",
        "daily",
        "office_text",
        "research_report",
    }
)
UNSUPPORTED_TASK_TYPES = frozenset({"coding_plan"})
UNSUPPORTED_MODEL_CLASSES = frozenset({"complex"})
SUPPORTED_MODEL_CLASSES = frozenset({MODEL_CLASS_LIGHT, MODEL_CLASS_STANDARD})

_MAX_LOG_TEXT_LENGTH = 1000
_SENSITIVE_PATTERNS = (
    re.compile(r"Bearer\s+(?:\[REDACTED\]|[A-Za-z0-9._~+/=-]+)", re.IGNORECASE),
    re.compile(r"(?i)\"?(authorization|cookie)\"?\s*[:=]\s*\"?[^,}\"']+"),
    re.compile(r"(?i)\"?(api[_-]?key|token|secret)\"?\s*[:=]\s*\"?[^,\s}\"']+"),
    re.compile(r"https?://private\.[^\s}\"')]+", re.IGNORECASE),
)


@dataclass(frozen=True)
class GatewayMessage:
    role: str
    content: str


@dataclass(frozen=True)
class GatewayRequest:
    user_id: str
    task_id: str
    task_type: str
    model_class: str | None
    messages: Sequence[GatewayMessage]
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class GatewayUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class GatewayResult:
    provider: str
    model: str
    content: str
    usage: GatewayUsage
    latency_ms: int
    status: str = "succeeded"


class ModelGatewayError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def route_model(task_type: str, model_class: str | None) -> str:
    normalized_task_type = task_type.strip()
    normalized_model_class = (
        model_class.strip().lower() if model_class is not None else None
    )

    if normalized_task_type in UNSUPPORTED_TASK_TYPES:
        raise ModelGatewayError(
            code=MODEL_GATEWAY_UNSUPPORTED_MODEL,
            message="Unsupported model route for this task type",
            status_code=400,
        )

    if normalized_model_class in UNSUPPORTED_MODEL_CLASSES:
        raise ModelGatewayError(
            code=MODEL_GATEWAY_UNSUPPORTED_MODEL,
            message="Unsupported model class",
            status_code=400,
        )

    if normalized_model_class is not None:
        if normalized_model_class in SUPPORTED_MODEL_CLASSES:
            return normalized_model_class
        raise ModelGatewayError(
            code=MODEL_GATEWAY_VALIDATION_ERROR,
            message="Invalid model class",
            status_code=400,
        )

    if normalized_task_type in LIGHT_TASK_TYPES:
        return MODEL_CLASS_LIGHT
    if normalized_task_type in STANDARD_TASK_TYPES:
        return MODEL_CLASS_STANDARD

    raise ModelGatewayError(
        code=MODEL_GATEWAY_VALIDATION_ERROR,
        message="Unknown task type",
        status_code=400,
    )


def sanitize_text(
    value: Any,
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> str:
    text = str(value)
    for sensitive_value in extra_sensitive_values:
        if sensitive_value:
            text = text.replace(sensitive_value, "[REDACTED]")
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def build_request_summary(
    request: GatewayRequest,
    *,
    resolved_model_class: str,
    extra_sensitive_values: Iterable[str | None] = (),
) -> str:
    payload = {
        "user_id": request.user_id,
        "task_id": request.task_id,
        "task_type": request.task_type,
        "model_class": resolved_model_class,
        "messages": [
            {"role": message.role, "content": _truncate(message.content)}
            for message in request.messages
        ],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }
    return sanitize_text(
        _json_dumps(payload),
        extra_sensitive_values=extra_sensitive_values,
    )


def build_response_summary(
    result: GatewayResult,
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> str:
    payload = {
        "provider": result.provider,
        "model": result.model,
        "content": _truncate(result.content),
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
        },
        "latency_ms": result.latency_ms,
        "status": result.status,
    }
    return sanitize_text(
        _json_dumps(payload),
        extra_sensitive_values=extra_sensitive_values,
    )


def build_error_summary(
    error: Exception,
    *,
    extra_sensitive_values: Iterable[str | None] = (),
) -> str:
    payload = {
        "error": _truncate(str(error)),
    }
    if isinstance(error, ModelGatewayError):
        payload["code"] = error.code
    return sanitize_text(
        _json_dumps(payload),
        extra_sensitive_values=extra_sensitive_values,
    )


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _truncate(value: str) -> str:
    if len(value) <= _MAX_LOG_TEXT_LENGTH:
        return value
    return f"{value[:_MAX_LOG_TEXT_LENGTH]}..."


POOL_ALIASES = {
    "light": "fast",
    "standard": "reasoning",
    "fast": "fast",
    "reasoning": "reasoning",
    "private": "private",
}


def route_pool(task_type: str, model_class: str | None) -> str:
    normalized = model_class.strip().lower() if model_class is not None else None
    if normalized is not None:
        try:
            return POOL_ALIASES[normalized]
        except KeyError as exc:
            raise ModelGatewayError(
                MODEL_GATEWAY_VALIDATION_ERROR, "Invalid model pool", 400
            ) from exc
    legacy = route_model(task_type, None)
    return POOL_ALIASES[legacy]
