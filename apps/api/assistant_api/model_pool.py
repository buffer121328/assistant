from __future__ import annotations

import json
from typing import Any

from packages.model_gateway import ModelNode, OpenAICompatibleAdapter, PooledModelGateway

from .config import Settings


def build_pooled_model_gateway(settings: Settings) -> PooledModelGateway:
    nodes = list(_legacy_nodes(settings))
    nodes.extend(_configured_nodes(settings.model_gateway_nodes_json))
    adapters = {
        node.node_id: OpenAICompatibleAdapter(
            timeout_seconds=settings.model_gateway_timeout_seconds,
            retry_attempts=settings.model_gateway_retry_attempts,
        )
        for node in nodes
    }
    return PooledModelGateway(tuple(nodes), adapters=adapters)


def _legacy_nodes(settings: Settings) -> tuple[ModelNode, ...]:
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
            "local-small-placeholder", "fast", "local", "", "", "", 1, 1.0,
            enabled=False,
        ),
        ModelNode(
            "qwen-private-placeholder", "private", "qwen", "", "", "", 1, 0.5,
            enabled=False,
        ),
    )


def _configured_nodes(raw: str) -> tuple[ModelNode, ...]:
    if not raw.strip():
        return ()
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("MODEL_GATEWAY_NODES_JSON must be a JSON array")
    nodes: list[ModelNode] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Model gateway node must be an object")
        nodes.append(_node(item))
    return tuple(nodes)


def _node(item: dict[str, Any]) -> ModelNode:
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
