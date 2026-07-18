from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Protocol

from .core import (
    GatewayRequest,
    GatewayResult,
    GatewayUsage,
    ModelGatewayError,
    MODEL_GATEWAY_PROVIDER_ERROR,
)

POOL_FAST = "fast"
POOL_REASONING = "reasoning"
POOL_PRIVATE = "private"
SUPPORTED_POOLS = frozenset({POOL_FAST, POOL_REASONING, POOL_PRIVATE})


@dataclass(frozen=True)
class ModelNode:
    node_id: str
    pool: str
    provider: str
    base_url: str
    model: str
    api_key: str
    capacity: int
    cost_advantage: float
    enabled: bool = True
    latency_target_ms: int = 2000

    @property
    def eligible(self) -> bool:
        return (
            self.enabled
            and self.pool in SUPPORTED_POOLS
            and bool(self.node_id and self.base_url and self.model and self.api_key)
            and self.capacity > 0
        )


@dataclass(frozen=True)
class NodeMetrics:
    active_requests: int = 0
    success_count: int = 0
    failure_count: int = 0
    ewma_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total else 1.0


class NodeAdapter(Protocol):
    async def chat(self, request: GatewayRequest, node: ModelNode) -> GatewayResult: ...

    def stream_chat(
        self, request: GatewayRequest, node: ModelNode
    ) -> AsyncIterator[str]: ...


class WeightedLeastLoadBalancer:
    def __init__(self, nodes: Sequence[ModelNode]) -> None:
        self.nodes = tuple(nodes)
        self._metrics = {node.node_id: NodeMetrics() for node in nodes}

    def metrics(self, node_id: str) -> NodeMetrics:
        return self._metrics[node_id]

    def set_metrics(self, node_id: str, metrics: NodeMetrics) -> None:
        self._metrics[node_id] = metrics

    def rank(self, pool: str, *, excluded: frozenset[str] = frozenset()) -> tuple[ModelNode, ...]:
        eligible = (
            node for node in self.nodes
            if node.pool == pool and node.eligible and node.node_id not in excluded
            and self.metrics(node.node_id).active_requests < node.capacity
        )
        return tuple(sorted(eligible, key=lambda node: (-self.score(node), node.node_id)))

    def score(self, node: ModelNode) -> float:
        metrics = self.metrics(node.node_id)
        capacity_score = max(0.0, node.capacity - metrics.active_requests) / node.capacity
        latency_penalty = min(1.0, metrics.ewma_latency_ms / max(1, node.latency_target_ms))
        failure_rate = 1.0 - metrics.success_rate
        cost_penalty = 1.0 - min(1.0, max(0.0, node.cost_advantage))
        return 0.40 * capacity_score - 0.25 * latency_penalty - 0.20 * failure_rate - 0.15 * cost_penalty

    def acquire(self, node: ModelNode) -> None:
        current = self.metrics(node.node_id)
        self.set_metrics(node.node_id, replace(current, active_requests=current.active_requests + 1))

    def release(self, node: ModelNode, *, succeeded: bool, latency_ms: int) -> None:
        current = self.metrics(node.node_id)
        ewma = float(latency_ms) if current.ewma_latency_ms <= 0 else current.ewma_latency_ms * 0.8 + latency_ms * 0.2
        self.set_metrics(
            node.node_id,
            replace(
                current,
                active_requests=max(0, current.active_requests - 1),
                success_count=current.success_count + int(succeeded),
                failure_count=current.failure_count + int(not succeeded),
                ewma_latency_ms=ewma,
            ),
        )


class PooledModelGateway:
    def __init__(self, nodes: Sequence[ModelNode], *, adapters: Mapping[str, NodeAdapter]) -> None:
        self.balancer = WeightedLeastLoadBalancer(nodes)
        self.adapters = dict(adapters)

    async def chat(self, request: GatewayRequest, pool: str) -> GatewayResult:
        pool = _normalize_pool(pool)
        attempted: set[str] = set()
        last_error: Exception | None = None
        while ranked := self.balancer.rank(pool, excluded=frozenset(attempted)):
            node = ranked[0]
            attempted.add(node.node_id)
            adapter = self.adapters[node.node_id]
            self.balancer.acquire(node)
            started = perf_counter()
            try:
                result = await adapter.chat(request, node)
            except Exception as exc:
                last_error = exc
                self.balancer.release(node, succeeded=False, latency_ms=_elapsed(started))
                continue
            self.balancer.release(node, succeeded=True, latency_ms=result.latency_ms)
            return result
        if isinstance(last_error, ModelGatewayError):
            raise last_error
        raise ModelGatewayError(MODEL_GATEWAY_PROVIDER_ERROR, "No eligible model node completed the request", 502) from last_error

    async def chat_stream(
        self,
        request: GatewayRequest,
        pool: str,
        on_delta,
    ) -> GatewayResult:
        pool = _normalize_pool(pool)
        attempted: set[str] = set()
        last_error: Exception | None = None
        while ranked := self.balancer.rank(pool, excluded=frozenset(attempted)):
            node = ranked[0]
            attempted.add(node.node_id)
            adapter = self.adapters[node.node_id]
            self.balancer.acquire(node)
            started = perf_counter()
            chunks: list[str] = []
            try:
                async for chunk in adapter.stream_chat(request, node):
                    chunks.append(chunk)
                    await on_delta(chunk)
            except Exception as exc:
                last_error = exc
                self.balancer.release(
                    node, succeeded=False, latency_ms=_elapsed(started)
                )
                continue
            latency_ms = _elapsed(started)
            self.balancer.release(node, succeeded=True, latency_ms=latency_ms)
            return GatewayResult(
                provider=node.provider,
                model=node.model,
                content="".join(chunks),
                usage=GatewayUsage(0, 0),
                latency_ms=latency_ms,
            )
        if isinstance(last_error, ModelGatewayError):
            raise last_error
        raise ModelGatewayError(
            MODEL_GATEWAY_PROVIDER_ERROR,
            "No eligible model node completed the stream",
            502,
        ) from last_error

    async def stream_chat(self, request: GatewayRequest, pool: str) -> AsyncIterator[str]:
        pool = _normalize_pool(pool)
        attempted: set[str] = set()
        last_error: Exception | None = None
        while ranked := self.balancer.rank(pool, excluded=frozenset(attempted)):
            node = ranked[0]
            attempted.add(node.node_id)
            adapter = self.adapters[node.node_id]
            self.balancer.acquire(node)
            started = perf_counter()
            try:
                async for chunk in adapter.stream_chat(request, node):
                    yield chunk
            except Exception as exc:
                last_error = exc
                self.balancer.release(node, succeeded=False, latency_ms=_elapsed(started))
                continue
            self.balancer.release(node, succeeded=True, latency_ms=_elapsed(started))
            return
        if isinstance(last_error, ModelGatewayError):
            raise last_error
        raise ModelGatewayError(MODEL_GATEWAY_PROVIDER_ERROR, "No eligible model node completed the stream", 502) from last_error


def _elapsed(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _normalize_pool(value: str) -> str:
    return {"light": "fast", "standard": "reasoning"}.get(value, value)
