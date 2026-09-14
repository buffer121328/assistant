from __future__ import annotations

import json
from typing import Any

from model_gateway import ModelNode, OpenAICompatibleAdapter, PooledModelGateway

from infrastructure.settings.config import Settings


def build_pooled_models(settings: Settings) -> PooledModelGateway:
    """构建 pooled model gateway。

    Args:
        settings: settings 参数。
    """
    nodes = list(_legacy_nodes(settings))
    nodes.extend(_qwen_rag_nodes(settings))
    nodes.extend(_configured_nodes(settings.models_nodes_json))
    adapters = {
        node.node_id: OpenAICompatibleAdapter(
            timeout_seconds=settings.models_timeout_seconds,
            retry_attempts=settings.models_retry_attempts,
        )
        for node in nodes
    }
    return PooledModelGateway(tuple(nodes), adapters=adapters)


def _legacy_nodes(settings: Settings) -> tuple[ModelNode, ...]:
    """执行 处理 legacy nodes 的内部辅助逻辑。

    Args:
        settings: settings 参数。
    """
    return (
        ModelNode(
            node_id="deepseek-flash",
            pool="fast",
            provider="deepseek",
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_light_model,
            api_key=settings.deepseek_api_key,
            capacity=8,
            cost_advantage=0.7,
        ),
        ModelNode(
            node_id="deepseek-pro",
            pool="reasoning",
            provider="deepseek",
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_standard_model,
            api_key=settings.deepseek_api_key,
            capacity=8,
            cost_advantage=0.7,
        ),
        ModelNode(
            "local-small-placeholder",
            "fast",
            "local",
            "",
            "",
            "",
            1,
            1.0,
            enabled=False,
        ),
        ModelNode(
            "qwen-private-placeholder",
            "private",
            "qwen",
            "",
            "",
            "",
            1,
            0.5,
            enabled=False,
        ),
    )


def _qwen_rag_nodes(settings: Settings) -> tuple[ModelNode, ...]:
    """Build the optional Qwen node used by RAG/private model routes.

    Args:
        settings: 用于执行当前操作的 settings 参数。
    """
    return (
        ModelNode(
            node_id="qwen-rag",
            pool="private",
            provider="qwen",
            base_url=settings.qwen_base_url,
            model=settings.qwen_rag_model,
            api_key=settings.qwen_api_key,
            capacity=4,
            cost_advantage=0.8,
        ),
    )


def _configured_nodes(raw: str) -> tuple[ModelNode, ...]:
    """执行 处理 configured nodes 的内部辅助逻辑。

    Args:
        raw: raw 参数。
    """
    if not raw.strip():
        return ()
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("MODELS_NODES_JSON must be a JSON array")
    nodes: list[ModelNode] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Model gateway node must be an object")
        nodes.append(_node(item))
    return tuple(nodes)


def _node(item: dict[str, Any]) -> ModelNode:
    """执行 处理 node 的内部辅助逻辑。

    Args:
        item: item 参数。
    """
    return ModelNode(
        node_id=str(item.get("id", "")).strip(),
        pool=str(item.get("pool", "")).strip().lower(),
        provider=str(item.get("provider", "")).strip().lower(),
        base_url=str(item.get("base_url", "")).strip(),
        model=str(item.get("model", "")).strip(),
        api_key=str(item.get("api_key", "")).strip(),
        capacity=int(item.get("capacity", 1)),
        cost_advantage=float(item.get("cost_advantage", 0.5)),
        enabled=bool(item.get("enabled", False)),
        latency_target_ms=int(item.get("latency_target_ms", 2000)),
    )
