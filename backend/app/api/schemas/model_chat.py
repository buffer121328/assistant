from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MODEL_GATEWAY_VALIDATION_ERROR = "models_validation_error"
MODEL_GATEWAY_UNSUPPORTED_MODEL = "models_unsupported_model"
MODEL_GATEWAY_TIMEOUT = "models_timeout"
MODEL_GATEWAY_PROVIDER_ERROR = "models_provider_error"


class ModelGatewayMessage(BaseModel):
    """表示 处理 model gateway message 的后端数据结构或服务对象。"""

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class ModelChatRequest(BaseModel):
    """表示 处理 model chat request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    model_class: str | None = None
    messages: list[ModelGatewayMessage] = Field(min_length=1)
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4000, ge=1, le=32000)


class ModelGatewayUsage(BaseModel):
    """表示 处理 model gateway usage 的后端数据结构或服务对象。"""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ModelChatResponse(BaseModel):
    """表示 处理 model chat response 的后端数据结构或服务对象。"""

    provider: str
    model: str
    content: str
    usage: ModelGatewayUsage
    latency_ms: int = Field(ge=0)
    status: Literal["succeeded"]
