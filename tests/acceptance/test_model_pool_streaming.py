from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from models import (
    GatewayMessage,
    GatewayRequest,
    GatewayResult,
    GatewayUsage,
    ModelNode,
    NodeMetrics,
    PooledModelGateway,
    WeightedLeastLoadBalancer,
    route_pool,
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


def test_pool_routes_keep_legacy_aliases_and_private_is_explicit() -> None:
    assert route_pool("router", None) == "fast"
    assert route_pool("plan", None) == "reasoning"
    assert route_pool("plan", "light") == "fast"
    assert route_pool("router", "standard") == "reasoning"
    assert route_pool("plan", "private") == "private"


def test_weighted_least_load_excludes_placeholders_and_prefers_capacity() -> None:
    nodes = (
        ModelNode("fast-a", "fast", "deepseek", "https://a.invalid/v1", "flash-a", "key", 4, 0.7),
        ModelNode("fast-b", "fast", "glm", "https://b.invalid/v1", "flash-b", "key", 4, 0.7),
        ModelNode("local-placeholder", "fast", "local", "", "", "", 1, 1.0, enabled=False),
    )
    balancer = WeightedLeastLoadBalancer(nodes)
    balancer.set_metrics("fast-a", NodeMetrics(active_requests=3, success_count=10, ewma_latency_ms=200))
    balancer.set_metrics("fast-b", NodeMetrics(active_requests=0, success_count=10, ewma_latency_ms=200))

    ranked = balancer.rank("fast")

    assert [node.node_id for node in ranked] == ["fast-b", "fast-a"]
    assert "local-placeholder" not in {node.node_id for node in ranked}


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
    fast_a = ModelNode("fast-a", "fast", "deepseek", "https://a.invalid/v1", "a", "key", 2, 0.5)
    fast_b = ModelNode("fast-b", "fast", "glm", "https://b.invalid/v1", "b", "key", 2, 0.5)
    reasoning = ModelNode("reasoning-a", "reasoning", "deepseek", "https://c.invalid/v1", "c", "key", 2, 0.5)
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
    node = ModelNode("fast-a", "fast", "deepseek", "https://a.invalid/v1", "a", "key", 1, 0.5)
    gateway = PooledModelGateway(
        (node,), adapters={"fast-a": FakeAdapter(chunks=("first", " second"))}
    )

    chunks = [chunk async for chunk in gateway.stream_chat(request("router"), "fast")]

    assert chunks == ["first", " second"]
    assert gateway.balancer.metrics("fast-a").active_requests == 0
    assert gateway.balancer.metrics("fast-a").success_count == 1


def test_desktop_event_renderer_shows_plan_and_appends_content(tmp_path) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from assistant_desktop.window import TaskWindow

    _app = QApplication.instance() or QApplication([])
    window = TaskWindow(
        settings=QSettings(str(tmp_path / "stream.ini"), QSettings.Format.IniFormat)
    )
    window._task_event_received(
        {"type": "plan", "payload": {"steps": ["检索资料", "生成回答"]}}
    )
    window.task_result.setPlainText("等待模型输出…")
    window._task_event_received(
        {"type": "content_delta", "payload": {"text": "第一段"}}
    )
    window._task_event_received(
        {"type": "content_delta", "payload": {"text": "第二段"}}
    )

    assert "1. 检索资料" in window.task_plan.text()
    assert window.task_result.toPlainText() == "第一段第二段"
    window.shutdown()


def test_final_answer_decoder_emits_only_answer_text() -> None:
    from models.streaming import FinalAnswerDeltaDecoder

    decoder = FinalAnswerDeltaDecoder()
    assert decoder.feed('{"action":"tool_call","tool_name":"search.web"}') == ""
    decoder = FinalAnswerDeltaDecoder()
    assert decoder.feed('{"action":"final","answer":"第一') == "第一"
    assert decoder.feed('段\\n第二段","plan":[]}') == "段\n第二段"
