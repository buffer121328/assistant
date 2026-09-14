from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from model_gateway import (
    GatewayMessage,
    GatewayRequest,
    GatewayResult,
    GatewayUsage,
    ModelNode,
    NodeMetrics,
    PooledModelGateway,
    WeightedLeastLoadBalancer,
    OpenAICompatibleAdapter,
    route_model,
)


def request(task_type: str = "plan") -> GatewayRequest:
    return GatewayRequest(
        user_id="user-1",
        task_id="task-1",
        task_type=task_type,
        model_class=None,
        messages=(GatewayMessage(role="user", content="hello"),),
        temperature=0.0,
        max_tokens=100,
    )


def test_weighted_least_load_excludes_placeholders_and_prefers_capacity() -> None:
    nodes = (
        ModelNode(
            "fast-a",
            "fast",
            "deepseek",
            "https://a.invalid/v1",
            "flash-a",
            "key",
            4,
            0.7,
        ),
        ModelNode(
            "fast-b", "fast", "glm", "https://b.invalid/v1", "flash-b", "key", 4, 0.7
        ),
        ModelNode(
            "local-placeholder", "fast", "local", "", "", "", 1, 1.0, enabled=False
        ),
    )
    balancer = WeightedLeastLoadBalancer(nodes)
    balancer.set_metrics(
        "fast-a", NodeMetrics(active_requests=3, success_count=10, ewma_latency_ms=200)
    )
    balancer.set_metrics(
        "fast-b", NodeMetrics(active_requests=0, success_count=10, ewma_latency_ms=200)
    )

    ranked = balancer.rank("fast")

    assert [node.node_id for node in ranked] == ["fast-b", "fast-a"]
    assert "local-placeholder" not in {node.node_id for node in ranked}


def test_subagent_task_type_routes_to_standard_pool() -> None:
    """Bounded subagent calls use a supported governed model route."""
    assert route_model("agent", None) == "standard"


@pytest.mark.asyncio
async def test_openai_compatible_adapter_preserves_finish_reason() -> None:
    """HTTP-success truncation remains observable to structured response handling."""

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"action":"final"'},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 100},
            },
        )

    node = ModelNode(
        "reasoning-a",
        "reasoning",
        "deepseek",
        "https://deepseek.invalid/v1",
        "deepseek-test",
        "placeholder-key",
        1,
        0.5,
    )
    adapter = OpenAICompatibleAdapter(
        timeout_seconds=1,
        retry_attempts=1,
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.chat(request("learn"), node)

    assert result.diagnostics == {"finish_reason": "length"}


class FakeAdapter:
    def __init__(self, *, fail: bool = False, chunks: tuple[str, ...] = ()) -> None:
        self.fail = fail
        self.chunks = chunks
        self.calls = 0

    async def chat(self, request: GatewayRequest, node: ModelNode) -> GatewayResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider failed")
        return GatewayResult(node.provider, node.model, "ok", GatewayUsage(1, 1), 5)

    async def stream_chat(
        self, request: GatewayRequest, node: ModelNode
    ) -> AsyncIterator[str]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider failed")
        for chunk in self.chunks:
            yield chunk


@pytest.mark.asyncio
async def test_gateway_fails_over_only_inside_selected_pool() -> None:
    fast_a = ModelNode(
        "fast-a", "fast", "deepseek", "https://a.invalid/v1", "a", "key", 2, 0.5
    )
    fast_b = ModelNode(
        "fast-b", "fast", "glm", "https://b.invalid/v1", "b", "key", 2, 0.5
    )
    reasoning = ModelNode(
        "reasoning-a",
        "reasoning",
        "deepseek",
        "https://c.invalid/v1",
        "c",
        "key",
        2,
        0.5,
    )
    adapters = {
        "fast-a": FakeAdapter(fail=True),
        "fast-b": FakeAdapter(),
        "reasoning-a": FakeAdapter(),
    }
    gateway = PooledModelGateway((fast_a, fast_b, reasoning), adapters=adapters)

    result = await gateway.chat(request("router"), "fast")

    assert result.model == "b"
    assert adapters["fast-a"].calls == 1
    assert adapters["fast-b"].calls == 1
    assert adapters["reasoning-a"].calls == 0


@pytest.mark.asyncio
async def test_gateway_streams_chunks_and_releases_load() -> None:
    node = ModelNode(
        "fast-a", "fast", "deepseek", "https://a.invalid/v1", "a", "key", 1, 0.5
    )
    gateway = PooledModelGateway(
        (node,), adapters={"fast-a": FakeAdapter(chunks=("first", " second"))}
    )

    chunks = [chunk async for chunk in gateway.stream_chat(request("router"), "fast")]

    assert chunks == ["first", " second"]
    assert gateway.balancer.metrics("fast-a").active_requests == 0
    assert gateway.balancer.metrics("fast-a").success_count == 1


def test_final_answer_decoder_emits_only_answer_text() -> None:
    from model_gateway.streaming import FinalAnswerDeltaDecoder

    decoder = FinalAnswerDeltaDecoder()
    assert decoder.feed('{"action":"tool_call","tool_name":"search.web"}') == ""
    decoder = FinalAnswerDeltaDecoder()
    assert decoder.feed('{"action":"final","answer":"第一') == "第一"
    assert decoder.feed('段\\n第二段","plan":[]}') == "段\n第二段"
