from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import json
from typing import Any

from common.redaction import sanitize_text

MODEL_CLASS_LIGHT = "light"
MODEL_CLASS_STANDARD = "standard"
PROVIDER_DEEPSEEK = "deepseek"

MODEL_GATEWAY_VALIDATION_ERROR = "models_validation_error"
MODEL_GATEWAY_UNSUPPORTED_MODEL = "models_unsupported_model"
MODEL_GATEWAY_TIMEOUT = "models_timeout"
MODEL_GATEWAY_PROVIDER_ERROR = "models_provider_error"

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
@dataclass(frozen=True)
class GatewayMessage:
    """表示 处理 gateway message 的后端数据结构或服务对象。"""

    role: str
    content: str


@dataclass(frozen=True)
class GatewayRequest:
    """表示 处理 gateway request 的后端数据结构或服务对象。"""

    user_id: str
    task_id: str
    task_type: str
    model_class: str | None
    messages: Sequence[GatewayMessage]
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class GatewayUsage:
    """表示 处理 gateway usage 的后端数据结构或服务对象。"""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class GatewayResult:
    """表示 处理 gateway result 的后端数据结构或服务对象。"""

    provider: str
    model: str
    content: str
    usage: GatewayUsage
    latency_ms: int
    status: str = "succeeded"
    estimated_cost: float | None = None
    diagnostics: dict[str, Any] | None = None


class ModelGatewayError(Exception):
    """表示 处理 model gateway error 的后端数据结构或服务对象。"""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        """初始化对象实例。

        Args:
            code: code 参数。
            message: message 参数。
            status_code: status_code 参数。
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def route_model(task_type: str, model_class: str | None) -> str:
    """路由 model。

    Args:
        task_type: task_type 参数。
        model_class: model_class 参数。
    """
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


def build_request_summary(
    request: GatewayRequest,
    *,
    resolved_model_class: str,
    extra_sensitive_values: Iterable[str | None] = (),
) -> str:
    """构建 request summary。

    Args:
        request: request 参数。
        resolved_model_class: resolved_model_class 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
    """
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
    """构建 response summary。

    Args:
        result: result 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
    """
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
        "estimated_cost": result.estimated_cost,
        "diagnostics": result.diagnostics,
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
    """构建 error summary。

    Args:
        error: error 参数。
        extra_sensitive_values: extra_sensitive_values 参数。
    """
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
    """执行 处理 json dumps 的内部辅助逻辑。

    Args:
        payload: payload 参数。
    """
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _truncate(value: str) -> str:
    """执行 处理 truncate 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
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
    """路由 pool。

    Args:
        task_type: task_type 参数。
        model_class: model_class 参数。
    """
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
