from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from time import perf_counter, time
from typing import Any, Protocol

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
MODEL_GATEWAY_RATE_LIMITED = "models_rate_limited"


@dataclass(frozen=True)
class ModelNode:
    """表示 处理 model node 的后端数据结构或服务对象。"""

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
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    input_token_cost: float | None = None
    output_token_cost: float | None = None

    @property
    def eligible(self) -> bool:
        """处理 eligible。"""
        return (
            self.enabled
            and self.pool in SUPPORTED_POOLS
            and bool(self.node_id and self.base_url and self.model and self.api_key)
            and self.capacity > 0
        )


@dataclass(frozen=True)
class NodeMetrics:
    """表示 处理 node metrics 的后端数据结构或服务对象。"""

    active_requests: int = 0
    success_count: int = 0
    failure_count: int = 0
    ewma_latency_ms: float = 0.0
    consecutive_failures: int = 0
    cooldown_until: float | None = None
    health_status: str = "healthy"
    request_timestamps: tuple[float, ...] = ()
    token_timestamps: tuple[tuple[float, int], ...] = ()

    @property
    def success_rate(self) -> float:
        """处理 success rate。"""
        total = self.success_count + self.failure_count
        return self.success_count / total if total else 1.0


class NodeAdapter(Protocol):
    """表示 处理 node adapter 的后端数据结构或服务对象。"""

    async def chat(self, request: GatewayRequest, node: ModelNode) -> GatewayResult:
        """处理 chat。

        Args:
            request: request 参数。
            node: node 参数。
        """
        ...

    def stream_chat(
        self, request: GatewayRequest, node: ModelNode
    ) -> AsyncIterator[str]:
        """处理 stream chat。

        Args:
            request: request 参数。
            node: node 参数。
        """
        ...


class WeightedLeastLoadBalancer:
    """表示 处理 weighted least load balancer 的后端数据结构或服务对象。"""

    def __init__(
        self,
        nodes: Sequence[ModelNode],
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 30.0,
        now: Callable[[], float] | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            nodes: nodes 参数。
            failure_threshold: failure_threshold 参数。
            cooldown_seconds: cooldown_seconds 参数。
            now: now 参数。
        """
        self.nodes = tuple(nodes)
        self._metrics = {node.node_id: NodeMetrics() for node in nodes}
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self.now = now or time

    def metrics(self, node_id: str) -> NodeMetrics:
        """处理 metrics。

        Args:
            node_id: node_id 参数。
        """
        return self._metrics[node_id]

    def set_metrics(self, node_id: str, metrics: NodeMetrics) -> None:
        """处理 set metrics。

        Args:
            node_id: node_id 参数。
            metrics: metrics 参数。
        """
        self._metrics[node_id] = metrics

    def rank(
        self,
        pool: str,
        *,
        excluded: frozenset[str] = frozenset(),
        request: GatewayRequest | None = None,
    ) -> tuple[ModelNode, ...]:
        """处理 rank。

        Args:
            pool: pool 参数。
            excluded: excluded 参数。
            request: request 参数。
        """
        eligible = (
            node
            for node in self.nodes
            if node.pool == pool
            and node.eligible
            and node.node_id not in excluded
            and self._is_node_available(node, request=request)
        )
        return tuple(
            sorted(eligible, key=lambda node: (-self.score(node), node.node_id))
        )

    def score(self, node: ModelNode) -> float:
        """处理 score。

        Args:
            node: node 参数。
        """
        metrics = self.metrics(node.node_id)
        capacity_score = (
            max(0.0, node.capacity - metrics.active_requests) / node.capacity
        )
        latency_penalty = min(
            1.0, metrics.ewma_latency_ms / max(1, node.latency_target_ms)
        )
        failure_rate = 1.0 - metrics.success_rate
        cost_penalty = 1.0 - min(1.0, max(0.0, node.cost_advantage))
        return (
            0.40 * capacity_score
            - 0.25 * latency_penalty
            - 0.20 * failure_rate
            - 0.15 * cost_penalty
        )

    def acquire(self, node: ModelNode) -> None:
        """处理 acquire。

        Args:
            node: node 参数。
        """
        current = self.metrics(node.node_id)
        self.set_metrics(
            node.node_id, replace(current, active_requests=current.active_requests + 1)
        )

    def release(
        self,
        node: ModelNode,
        *,
        succeeded: bool,
        latency_ms: int,
        usage: GatewayUsage | None = None,
    ) -> None:
        """处理 release。

        Args:
            node: node 参数。
            succeeded: succeeded 参数。
            latency_ms: latency_ms 参数。
            usage: usage 参数。
        """
        current = self.metrics(node.node_id)
        ewma = (
            float(latency_ms)
            if current.ewma_latency_ms <= 0
            else current.ewma_latency_ms * 0.8 + latency_ms * 0.2
        )
        now = self.now()
        request_timestamps = current.request_timestamps
        token_timestamps = current.token_timestamps
        health_status = current.health_status
        cooldown_until = current.cooldown_until
        consecutive_failures = current.consecutive_failures
        if succeeded:
            request_timestamps = (*self._recent_requests(current, now=now), now)
            token_count = (
                0 if usage is None else usage.input_tokens + usage.output_tokens
            )
            token_timestamps = (
                *self._recent_tokens(current, now=now),
                (now, token_count),
            )
            consecutive_failures = 0
            cooldown_until = None
            health_status = "healthy"
        else:
            consecutive_failures += 1
            if consecutive_failures >= self.failure_threshold:
                cooldown_until = now + self.cooldown_seconds
                health_status = "cooldown"
        self.set_metrics(
            node.node_id,
            replace(
                current,
                active_requests=max(0, current.active_requests - 1),
                success_count=current.success_count + int(succeeded),
                failure_count=current.failure_count + int(not succeeded),
                ewma_latency_ms=ewma,
                consecutive_failures=consecutive_failures,
                cooldown_until=cooldown_until,
                health_status=health_status,
                request_timestamps=request_timestamps,
                token_timestamps=token_timestamps,
            ),
        )

    def _is_node_available(
        self, node: ModelNode, *, request: GatewayRequest | None
    ) -> bool:
        """执行 处理 is node available 的内部辅助逻辑。

        Args:
            node: node 参数。
            request: request 参数。
        """
        metrics = self.metrics(node.node_id)
        now = self.now()
        if metrics.active_requests >= node.capacity:
            return False
        if metrics.cooldown_until is not None:
            if metrics.cooldown_until > now:
                return False
            self.set_metrics(
                node.node_id,
                replace(metrics, cooldown_until=None, health_status="healthy"),
            )
            metrics = self.metrics(node.node_id)
        if (
            node.rpm_limit is not None
            and len(self._recent_requests(metrics, now=now)) >= node.rpm_limit
        ):
            return False
        estimated_tokens = 0 if request is None else max(0, request.max_tokens)
        if node.tpm_limit is not None:
            used_tokens = sum(
                tokens for _, tokens in self._recent_tokens(metrics, now=now)
            )
            if used_tokens + estimated_tokens > node.tpm_limit:
                return False
        return True

    def _recent_requests(
        self, metrics: NodeMetrics, *, now: float
    ) -> tuple[float, ...]:
        """执行 处理 recent requests 的内部辅助逻辑。

        Args:
            metrics: metrics 参数。
            now: now 参数。
        """
        return tuple(item for item in metrics.request_timestamps if now - item < 60.0)

    def _recent_tokens(
        self, metrics: NodeMetrics, *, now: float
    ) -> tuple[tuple[float, int], ...]:
        """执行 处理 recent tokens 的内部辅助逻辑。

        Args:
            metrics: metrics 参数。
            now: now 参数。
        """
        return tuple(item for item in metrics.token_timestamps if now - item[0] < 60.0)


DiagnosticSink = Callable[[dict[str, object]], None]


class PooledModelGateway:
    """表示 处理 pooled model gateway 的后端数据结构或服务对象。"""

    def __init__(
        self,
        nodes: Sequence[ModelNode],
        *,
        adapters: Mapping[str, NodeAdapter],
        failure_threshold: int = 2,
        cooldown_seconds: float = 30.0,
        now: Callable[[], float] | None = None,
        diagnostic_sink: DiagnosticSink | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            nodes: nodes 参数。
            adapters: adapters 参数。
            failure_threshold: failure_threshold 参数。
            cooldown_seconds: cooldown_seconds 参数。
            now: now 参数。
            diagnostic_sink: diagnostic_sink 参数。
        """
        self.balancer = WeightedLeastLoadBalancer(
            nodes,
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
            now=now,
        )
        self.adapters = dict(adapters)
        self.diagnostic_sink = diagnostic_sink

    async def chat(self, request: GatewayRequest, pool: str) -> GatewayResult:
        """处理 chat。

        Args:
            request: request 参数。
            pool: pool 参数。
        """
        pool = _normalize_pool(pool)
        attempted: set[str] = set()
        last_error: Exception | None = None
        failed_from: tuple[str, str] | None = None
        while ranked := self.balancer.rank(
            pool, excluded=frozenset(attempted), request=request
        ):
            node = ranked[0]
            attempted.add(node.node_id)
            if failed_from is not None:
                self._diagnose_fallback(failed_from=failed_from, to_node=node)
                failed_from = None
            adapter = self.adapters[node.node_id]
            self.balancer.acquire(node)
            started = perf_counter()
            try:
                result = await adapter.chat(request, node)
            except Exception as exc:
                last_error = exc
                self.balancer.release(
                    node, succeeded=False, latency_ms=_elapsed(started)
                )
                failed_from = (node.node_id, _error_code(exc))
                continue
            self.balancer.release(
                node, succeeded=True, latency_ms=result.latency_ms, usage=result.usage
            )
            return self._with_diagnostics(result, node=node)
        if isinstance(last_error, ModelGatewayError):
            raise last_error
        raise ModelGatewayError(
            MODEL_GATEWAY_PROVIDER_ERROR,
            "No eligible model node completed the request",
            502,
        ) from last_error

    async def chat_stream(
        self,
        request: GatewayRequest,
        pool: str,
        on_delta,
    ) -> GatewayResult:
        """处理 chat stream。

        Args:
            request: request 参数。
            pool: pool 参数。
            on_delta: on_delta 参数。
        """
        pool = _normalize_pool(pool)
        attempted: set[str] = set()
        last_error: Exception | None = None
        failed_from: tuple[str, str] | None = None
        while ranked := self.balancer.rank(
            pool, excluded=frozenset(attempted), request=request
        ):
            node = ranked[0]
            attempted.add(node.node_id)
            if failed_from is not None:
                self._diagnose_fallback(failed_from=failed_from, to_node=node)
                failed_from = None
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
                failed_from = (node.node_id, _error_code(exc))
                continue
            latency_ms = _elapsed(started)
            usage = GatewayUsage(0, 0)
            self.balancer.release(
                node, succeeded=True, latency_ms=latency_ms, usage=usage
            )
            return self._with_diagnostics(
                GatewayResult(
                    provider=node.provider,
                    model=node.model,
                    content="".join(chunks),
                    usage=usage,
                    latency_ms=latency_ms,
                ),
                node=node,
            )
        if isinstance(last_error, ModelGatewayError):
            raise last_error
        raise ModelGatewayError(
            MODEL_GATEWAY_PROVIDER_ERROR,
            "No eligible model node completed the stream",
            502,
        ) from last_error

    async def stream_chat(
        self, request: GatewayRequest, pool: str
    ) -> AsyncIterator[str]:
        """处理 stream chat。

        Args:
            request: request 参数。
            pool: pool 参数。
        """
        pool = _normalize_pool(pool)
        attempted: set[str] = set()
        last_error: Exception | None = None
        while ranked := self.balancer.rank(
            pool, excluded=frozenset(attempted), request=request
        ):
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
                self.balancer.release(
                    node, succeeded=False, latency_ms=_elapsed(started)
                )
                continue
            self.balancer.release(
                node,
                succeeded=True,
                latency_ms=_elapsed(started),
                usage=GatewayUsage(0, 0),
            )
            return
        if isinstance(last_error, ModelGatewayError):
            raise last_error
        raise ModelGatewayError(
            MODEL_GATEWAY_PROVIDER_ERROR,
            "No eligible model node completed the stream",
            502,
        ) from last_error

    def _with_diagnostics(
        self, result: GatewayResult, *, node: ModelNode
    ) -> GatewayResult:
        """执行 处理 with diagnostics 的内部辅助逻辑。

        Args:
            result: result 参数。
            node: node 参数。
        """
        estimated_cost = _estimated_cost(node, result.usage)
        diagnostics: dict[str, Any] = {
            "node_id": node.node_id,
            "provider": node.provider,
            "model": node.model,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "estimated_cost": estimated_cost,
            "status": result.status,
        }
        return GatewayResult(
            provider=result.provider,
            model=result.model,
            content=result.content,
            usage=result.usage,
            latency_ms=result.latency_ms,
            status=result.status,
            estimated_cost=estimated_cost,
            diagnostics=diagnostics,
        )

    def _diagnose_fallback(
        self, *, failed_from: tuple[str, str], to_node: ModelNode
    ) -> None:
        """执行 处理 diagnose fallback 的内部辅助逻辑。

        Args:
            failed_from: failed_from 参数。
            to_node: to_node 参数。
        """
        if self.diagnostic_sink is None:
            return
        from_node, error_code = failed_from
        self.diagnostic_sink(
            {
                "event_type": "models.fallback",
                "from_node": from_node,
                "to_node": to_node.node_id,
                "error_code": error_code,
            }
        )


def _estimated_cost(node: ModelNode, usage: GatewayUsage) -> float | None:
    """执行 处理 estimated cost 的内部辅助逻辑。

    Args:
        node: node 参数。
        usage: usage 参数。
    """
    if node.input_token_cost is None and node.output_token_cost is None:
        return None
    return (node.input_token_cost or 0.0) * usage.input_tokens + (
        node.output_token_cost or 0.0
    ) * usage.output_tokens


def _error_code(error: Exception) -> str:
    """执行 处理 error code 的内部辅助逻辑。

    Args:
        error: error 参数。
    """
    if isinstance(error, ModelGatewayError):
        return error.code
    return MODEL_GATEWAY_PROVIDER_ERROR


def _elapsed(started: float) -> int:
    """执行 处理 elapsed 的内部辅助逻辑。

    Args:
        started: started 参数。
    """
    return max(0, round((perf_counter() - started) * 1000))


def _normalize_pool(value: str) -> str:
    """执行 规范化 pool 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    return {"light": "fast", "standard": "reasoning"}.get(value, value)
