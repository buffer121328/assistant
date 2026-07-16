from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import pytest


def test_desktop_memory_client_uses_owned_memory_center_apis() -> None:
    from assistant_desktop.client import DesktopApiClient

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/api/memories/overview":
            return httpx.Response(200, json={"counts": {}, "pending_index_count": 0})
        if path == "/api/memories" and request.method == "POST":
            return httpx.Response(201, json={"memory": {"memory_id": "memory-1"}})
        if path == "/api/memories":
            return httpx.Response(200, json={"items": [], "limit": 20, "offset": 0})
        if path == "/api/memories/memory-1":
            return httpx.Response(200, json={"memory": {"memory_id": "memory-1"}})
        if path.endswith("/actions/pin"):
            return httpx.Response(
                200, json={"memory": {"memory_id": "memory-1", "is_pinned": True}}
            )
        if path == "/api/memory/policies" and request.method == "GET":
            return httpx.Response(200, json={"items": []})
        if path.startswith("/api/memory/policies/"):
            return httpx.Response(
                200, json={"policy": {"policy_key": "never_remember:fact"}}
            )
        if path == "/api/memory/consolidation-digests":
            return httpx.Response(200, json={"items": []})
        raise AssertionError(f"unexpected request: {request.method} {path}")

    client = DesktopApiClient(
        base_url="http://127.0.0.1:8000",
        user_id="owner-1",
        transport=httpx.MockTransport(handler),
    )
    client.get_memory_overview()
    client.create_memory(content="回答先给结论")
    client.list_memories(status="active", limit=20)
    client.get_memory_detail("memory-1")
    client.perform_memory_action("memory-1", "pin")
    client.list_memory_policies()
    client.update_memory_policy("never_remember:fact", enabled=True)
    client.list_memory_digests(limit=5)
    client.close()

    owned_gets = [
        request
        for request in requests
        if request.method == "GET" and request.url.path != "/api/memory/consolidation-digests"
    ]
    assert all(request.url.params["user_id"] == "owner-1" for request in owned_gets)
    assert requests[1].read().decode().count('"user_id":"owner-1"') == 1
    assert requests[4].read().decode().count('"user_id":"owner-1"') == 1
    assert requests[6].read().decode().count('"user_id":"owner-1"') == 1
    assert requests[-1].url.params["limit"] == "5"


def test_memory_center_is_non_blocking_accessible_and_refreshes_after_success(
    tmp_path: Path,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget, QWidget

    from assistant_desktop.client import DesktopApiError
    from assistant_desktop.memory_center_dialog import MemoryCenterDialog

    class CapturingPool:
        def __init__(self) -> None:
            self.workers: list[Any] = []

        def start(self, worker: Any) -> None:
            self.workers.append(worker)

        def run_all(self) -> None:
            while self.workers:
                self.workers.pop(0).run()

    class FakeClient:
        fail_actions = True

        def __init__(self, **_kwargs: object) -> None:
            pass

        def close(self) -> None:
            pass

        def get_memory_overview(self) -> dict[str, object]:
            return {"counts": {"active": 1, "candidate": 1}, "pending_index_count": 0}

        def list_memories(self, **values: object) -> dict[str, object]:
            status = values.get("status")
            if status == "conflict_pending":
                items: list[dict[str, object]] = []
            elif status == "candidate":
                items = [
                    {
                        "memory_id": "candidate-1",
                        "status": "candidate",
                        "memory_type": "fact",
                        "content": "候选内容",
                    }
                ]
            else:
                items = [
                    {
                        "memory_id": "memory-1",
                        "status": "active",
                        "memory_type": "preference",
                        "content": "服务端列表内容",
                    }
                ]
            return {"items": items, "limit": 50, "offset": 0}

        def get_memory_detail(self, memory_id: str) -> dict[str, object]:
            return {
                "memory": {
                    "memory_id": memory_id,
                    "status": "active",
                    "memory_type": "preference",
                    "content": "服务端新详情",
                    "source_kind": "explicit_service",
                    "reason_code": "explicit_user_request",
                    "scope_kind": "user/global",
                },
                "links": [],
                "feedback": [],
                "usage": [],
            }

        def perform_memory_action(
            self, _memory_id: str, _action: str, **_values: object
        ) -> dict[str, object]:
            if self.fail_actions:
                raise DesktopApiError("synthetic action failure")
            return {"memory": {"memory_id": "memory-1", "status": "active"}}

        def list_memory_policies(self) -> list[dict[str, object]]:
            return []

        def list_memory_digests(self) -> list[dict[str, object]]:
            return []

    application = QApplication.instance() or QApplication([])
    pool = CapturingPool()
    dialog = MemoryCenterDialog(
        base_url="http://127.0.0.1:8000",
        user_id="owner-1",
        thread_pool=pool,  # type: ignore[arg-type]
        client_factory=FakeClient,  # type: ignore[arg-type]
    )

    tabs = dialog.findChild(QTabWidget, "memory_center_tabs")
    assert tabs is not None and tabs.count() == 7
    tab_widgets = [tabs.widget(index) for index in range(tabs.count())]
    assert all(widget is not None for widget in tab_widgets)
    assert {widget.objectName() for widget in tab_widgets if widget is not None} == {
        "memory_overview_tab",
        "memory_list_tab",
        "memory_detail_tab",
        "memory_candidates_tab",
        "memory_conflicts_tab",
        "memory_retrieval_tab",
        "memory_settings_tab",
    }
    assert dialog.findChild(QWidget, "memory_list") is not None
    assert dialog.findChild(QWidget, "memory_action_correct") is not None
    assert dialog.findChild(QWidget, "inspect_memory_retrieval") is not None
    refresh = dialog.findChild(QPushButton, "refresh_memory_center")
    assert refresh is not None and refresh.accessibleName()

    dialog.refresh_all()
    assert len(pool.workers) == 6
    assert dialog.overview_label.text() == "尚未加载"
    pool.run_all()
    assert "Active 1" in dialog.overview_label.text()
    assert dialog.memory_list.count() == 1

    dialog._current_memory_id = "memory-1"  # noqa: SLF001 - user-selected state
    dialog.detail_text.setPlainText("服务端旧详情")
    dialog.perform_action("pin")
    assert dialog.detail_text.toPlainText() == "服务端旧详情"
    pool.run_all()
    assert dialog.detail_text.toPlainText() == "服务端旧详情"
    assert "synthetic action failure" in dialog.status_label.text()

    FakeClient.fail_actions = False
    dialog.perform_action("pin")
    assert dialog.detail_text.toPlainText() == "服务端旧详情"
    pool.workers.pop(0).run()
    assert dialog.detail_text.toPlainText() == "服务端旧详情"
    assert pool.workers
    pool.run_all()
    assert "服务端新详情" in dialog.detail_text.toPlainText()

    dialog.show()
    application.processEvents()
    dialog.close()
    application.processEvents()
