from __future__ import annotations

from collections.abc import AsyncIterator
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.main import create_app
from domain.models import Approval, Base, Task, TaskStatus, ToolLog, User
from domain.services import TaskService
from agent import HumanApprovalRequest
from agent.tool_management import (
    ToolApprovalRequiredError,
    ToolInvocation,
    ToolRegistry,
    ToolSpec,
    external_approval_binding,
)


@pytest_asyncio.fixture
async def sessionmaker(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/v2-desktop.db",
        poolclass=NullPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def create_user(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    name: str = "Desktop User",
) -> str:
    async with sessionmaker() as session:
        user = User(display_name=name)
        session.add(user)
        await session.commit()
        return user.id


async def create_task(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    status: TaskStatus = TaskStatus.PENDING,
) -> Task:
    async with sessionmaker() as session:
        task = Task(
            user_id=user_id,
            platform="desktop",
            task_type="plan",
            input_text="整理本周计划",
            status=status.value,
        )
        session.add(task)
        await session.commit()
        return task


def create_test_client(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> TestClient:
    app = create_app()
    app.state.db_sessionmaker = sessionmaker
    return TestClient(app)


@pytest.mark.asyncio
async def test_interactive_submit_enqueues_without_changing_create_only_api(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(sessionmaker)
    queued_task_ids: list[str] = []

    def fake_enqueue(task_id: str, *, runtime_settings: Any = None) -> bool:
        queued_task_ids.append(task_id)
        return True

    monkeypatch.setattr("app.api.router.enqueue_task_execution", fake_enqueue)
    payload = {
        "user_id": user_id,
        "platform": "desktop",
        "task_type": "plan",
        "input_text": "整理本周计划",
    }

    with create_test_client(sessionmaker) as client:
        submitted = client.post("/api/tasks/submit", json=payload)
        created_only = client.post("/api/tasks", json=payload)

    assert submitted.status_code == 201
    assert submitted.json()["queued"] is True
    assert submitted.json()["task"]["status"] == "pending"
    assert queued_task_ids == [submitted.json()["task"]["task_id"]]
    assert created_only.status_code == 201
    assert created_only.json()["task_id"] not in queued_task_ids


@pytest.mark.asyncio
async def test_interactive_submit_reports_unavailable_queue(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await create_user(sessionmaker)
    monkeypatch.setattr(
        "app.api.router.enqueue_task_execution",
        lambda task_id, *, runtime_settings=None: False,
    )

    with create_test_client(sessionmaker) as client:
        response = client.post(
            "/api/tasks/submit",
            json={
                "user_id": user_id,
                "platform": "desktop",
                "task_type": "learn",
                "input_text": "学习 LangGraph",
            },
        )

    assert response.status_code == 201
    assert response.json()["queued"] is False
    assert response.json()["task"]["status"] == "pending"


@pytest.mark.asyncio
async def test_approval_is_owned_audited_idempotent_and_resumes_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = await create_user(sessionmaker, name="Owner")
    other_id = await create_user(sessionmaker, name="Other")
    task = await create_task(
        sessionmaker,
        user_id=owner_id,
        status=TaskStatus.RUNNING,
    )

    async with sessionmaker() as session:
        await TaskService(session).save_waiting_approval(
            task.id,
            "任务需要审批后才能继续：email.send。",
            requested_tools=["email.send", "email.send"],
        )
        approvals = list(await session.scalars(select(Approval)))

    assert len(approvals) == 1
    assert approvals[0].tool_name == "email.send"
    assert approvals[0].status == "pending"
    queued_task_ids: list[str] = []

    def fake_enqueue(task_id: str, *, runtime_settings: Any = None) -> bool:
        queued_task_ids.append(task_id)
        return True

    monkeypatch.setattr("app.api.router.enqueue_task_execution", fake_enqueue)
    decision_url = f"/api/tasks/{task.id}/approvals/{approvals[0].id}/decision"

    with create_test_client(sessionmaker) as client:
        denied_list = client.get(
            f"/api/tasks/{task.id}/approvals",
            params={"user_id": other_id},
        )
        denied_decision = client.post(
            decision_url,
            json={"user_id": other_id, "decision": "approved"},
        )
        visible = client.get(
            f"/api/tasks/{task.id}/approvals",
            params={"user_id": owner_id},
        )
        approved = client.post(
            decision_url,
            json={"user_id": owner_id, "decision": "approved"},
        )
        repeated = client.post(
            decision_url,
            json={"user_id": owner_id, "decision": "approved"},
        )
        conflict = client.post(
            decision_url,
            json={"user_id": owner_id, "decision": "rejected"},
        )

    assert denied_list.status_code == 404
    assert denied_decision.status_code == 404
    assert visible.status_code == 200
    assert visible.json()["items"][0]["tool_name"] == "email.send"
    assert visible.json()["items"][0]["approval_type"] == "tool"
    assert visible.json()["items"][0]["subject"] == "email.send"
    assert "email.send" in visible.json()["items"][0]["request_summary"]
    assert approved.status_code == 200
    assert approved.json()["approval"]["status"] == "approved"
    assert approved.json()["approval"]["decided_by_user_id"] == owner_id
    assert approved.json()["approval"]["decided_at"]
    assert approved.json()["task"]["status"] == "pending"
    assert approved.json()["queued"] is True
    assert repeated.status_code == 200
    assert repeated.json()["queued"] is False
    assert conflict.status_code == 409
    assert queued_task_ids == [task.id]


@pytest.mark.asyncio
async def test_plan_approval_api_exposes_type_subject_summary_and_compatibility(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await create_user(sessionmaker)
    task = await create_task(
        sessionmaker,
        user_id=owner_id,
        status=TaskStatus.RUNNING,
    )
    async with sessionmaker() as session:
        await TaskService(session).save_waiting_approval(
            task.id,
            "计划需要审批。",
            approval_requests=(
                HumanApprovalRequest(
                    approval_type="plan",
                    subject="plan:0",
                    summary="核对来源；形成回答",
                ),
            ),
        )

    with create_test_client(sessionmaker) as client:
        response = client.get(
            f"/api/tasks/{task.id}/approvals",
            params={"user_id": owner_id},
        )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["approval_type"] == "plan"
    assert item["subject"] == "plan:0"
    assert item["request_summary"] == "核对来源；形成回答"
    assert item["tool_name"] == "agent.plan"


@pytest.mark.asyncio
async def test_rejected_approval_cancels_without_enqueue(
    sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = await create_user(sessionmaker)
    task = await create_task(
        sessionmaker,
        user_id=owner_id,
        status=TaskStatus.RUNNING,
    )
    async with sessionmaker() as session:
        await TaskService(session).save_waiting_approval(
            task.id,
            "任务需要审批后才能继续：email.send。",
            requested_tools=["email.send"],
        )
        approval = await session.scalar(select(Approval))
    assert approval is not None
    queued_task_ids: list[str] = []
    monkeypatch.setattr(
        "app.api.router.enqueue_task_execution",
        lambda task_id, *, runtime_settings=None: queued_task_ids.append(task_id),
    )

    with create_test_client(sessionmaker) as client:
        response = client.post(
            f"/api/tasks/{task.id}/approvals/{approval.id}/decision",
            json={"user_id": owner_id, "decision": "rejected"},
        )

    assert response.status_code == 200
    assert response.json()["approval"]["status"] == "rejected"
    assert response.json()["task"]["status"] == "cancelled"
    assert response.json()["queued"] is False
    assert queued_task_ids == []


@pytest.mark.asyncio
async def test_registry_executes_only_exact_approved_task_and_tool(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    user_id = await create_user(sessionmaker)
    approved_task = await create_task(sessionmaker, user_id=user_id)
    other_task = await create_task(sessionmaker, user_id=user_id)
    calls: list[str] = []

    async def handler(invocation: ToolInvocation) -> dict[str, bool]:
        calls.append(invocation.task_id)
        return {"sent": True}

    async with sessionmaker() as session:
        approval_subject = external_approval_binding("email.send", {}).subject
        session.add(
            Approval(
                task_id=approved_task.id,
                tool_name="email.send",
                subject=approval_subject,
                status="approved",
                decided_by_user_id=user_id,
            )
        )
        await session.flush()
        registry = ToolRegistry(session=session)
        registry.register(
            ToolSpec(
                name="email.send",
                description="Approval-gated email",
                risk_level="L3",
                handler=handler,
            )
        )

        result = await registry.execute(
            ToolInvocation(
                task_id=approved_task.id,
                user_id=user_id,
                name="email.send",
            ),
            allowed_tools=(),
            approval_required_tools=("email.send",),
        )
        with pytest.raises(ToolApprovalRequiredError):
            await registry.execute(
                ToolInvocation(
                    task_id=other_task.id,
                    user_id=user_id,
                    name="email.send",
                ),
                allowed_tools=(),
                approval_required_tools=("email.send",),
            )
        await session.commit()

    async with sessionmaker() as session:
        logs = list(
            await session.scalars(
                select(ToolLog).where(ToolLog.tool_name == "email.send")
            )
        )

    assert result == {"sent": True}
    assert calls == [approved_task.id]
    assert [(log.task_id, log.status) for log in logs] == [
        (approved_task.id, "succeeded"),
        (other_task.id, "waiting_approval"),
    ]


def test_desktop_client_contract_uses_interactive_and_owned_approval_apis() -> None:
    from assistant_desktop.client import DesktopApiClient

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/tasks/submit":
            return httpx.Response(
                201,
                json={
                    "task": {"task_id": "task-1", "status": "pending"},
                    "queued": False,
                },
            )
        if request.url.path == "/api/tasks/task-1/approvals":
            return httpx.Response(
                200,
                json={"items": [{"approval_id": "approval-1", "status": "pending"}]},
            )
        return httpx.Response(
            200,
            json={
                "approval": {"approval_id": "approval-1", "status": "approved"},
                "task": {"task_id": "task-1", "status": "pending"},
                "queued": True,
            },
        )

    client = DesktopApiClient(
        base_url="http://127.0.0.1:8000/",
        user_id="user-1",
        transport=httpx.MockTransport(handler),
    )
    submitted = client.submit_task(task_type="plan", input_text="整理本周计划")
    approvals = client.list_approvals("task-1")
    decided = client.decide_approval("task-1", "approval-1", "approved")

    assert submitted.queued is False
    assert submitted.task["status"] == "pending"
    assert approvals[0]["approval_id"] == "approval-1"
    assert decided.queued is True
    assert [request.url.path for request in requests] == [
        "/api/tasks/submit",
        "/api/tasks/task-1/approvals",
        "/api/tasks/task-1/approvals/approval-1/decision",
    ]
    assert requests[1].url.params["user_id"] == "user-1"


def test_pyside_window_is_compact_native_and_contains_required_controls(
    tmp_path: Path,
) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtCore")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QComboBox, QSystemTrayIcon, QWidget

    from assistant_desktop.window import TaskWindow

    application = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "desktop.ini"), QSettings.Format.IniFormat)
    window = TaskWindow(settings=settings)

    assert window.maximumWidth() <= 480
    assert window.minimumWidth() == window.maximumWidth()
    task_mode = window.findChild(QComboBox, "task_mode")
    assert task_mode is not None
    assert task_mode.currentData() == "agent"
    assert {task_mode.itemData(index) for index in range(task_mode.count())} == {
        "agent",
        "plan",
        "learn",
        "daily",
        "office",
        "memory",
        "status",
    }
    assert window.findChild(QWidget, "task_input") is not None
    assert window.findChild(QWidget, "recent_tasks") is not None
    assert window.findChild(QWidget, "approval_list") is not None
    assert window.findChild(QWidget, "confirm_memory_candidate") is not None
    assert window.findChild(QWidget, "reject_memory_candidate") is not None
    assert window.findChild(QWidget, "correct_memory_candidate") is not None
    assert window.findChild(QWidget, "manage_memory_center") is not None
    assert isinstance(window.tray_icon, QSystemTrayIcon)

    window._approvals_refreshed(  # noqa: SLF001 - verify user-visible approval label
        [
            {
                "approval_id": "approval-plan",
                "approval_type": "plan",
                "subject": "plan:0",
                "request_summary": "核对来源；形成回答",
                "status": "pending",
            }
        ]
    )
    assert window.approval_list.item(0).text() == ("[计划] plan:0 — 核对来源；形成回答")

    window._conversation_messages_refreshed(  # noqa: SLF001
        {
            "items": [{"role": "user", "content": "继续"}],
            "compacted": True,
            "summary_updated_at": "2026-07-16T08:00:00+00:00",
            "summary_version": "summary-v1",
        }
    )
    assert "已压缩历史" in window.status_label.text()
    assert "summary-v1" in window.status_label.text()

    window._memory_retrieval_refreshed(  # noqa: SLF001
        {
            "trace": {
                "injected_count": 3,
                "retrieval_mode": "keyword_fallback",
            },
            "items": [],
        }
    )
    assert "本次使用了 3 条记忆" in window.status_label.text()
    assert "keyword_fallback" in window.status_label.text()

    window.show()
    application.processEvents()
    window.close()
    application.processEvents()
    assert window.isHidden()
    window.shutdown()
