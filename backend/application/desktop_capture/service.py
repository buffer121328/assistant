from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from application.desktop_capture.commands import parse_screen_command
from application.artifact_lifecycle import ArtifactLifecycleService
from application.desktop_capture.runners import (
    DesktopCaptureUnavailable,
    ScreenshotRunner,
    build_screenshot_runner,
)
from application.task_execution.commands import (
    _fail_task,
    _load_pending_task,
    _mark_running,
    _safe_summary,
    _succeed_task,
)
from application.task_execution.events import TaskEventRepository
from domain.models import Task
from infrastructure.settings.config import Settings
from tools.builtin.artifacts import ArtifactStore
from tools.core.registry import (
    ToolExecutionError,
    ToolInvocation,
    ToolRegistry,
    ToolRegistryError,
    ToolSourceUnavailableError,
    ToolSpec,
)

SCREEN_TOOL_NAME = "desktop.screenshot"
SCREEN_ARTIFACT_FILENAME = "screenshot.png"


class ScreenTaskService:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        screenshot_runner: ScreenshotRunner | None = None,
    ) -> None:
        """初始化截图任务服务及其会话、配置、产物存储和截图执行器。

        Args:
            session: 当前数据库异步会话。
            settings: 用于执行当前操作的 settings 参数。
            screenshot_runner: 用于执行当前操作的 screenshot runner 参数。
        """
        self.session = session
        self.settings = settings
        self.screenshot_runner = screenshot_runner or build_screenshot_runner(settings)
        self.artifact_store = ArtifactStore(settings.artifacts_root)
        self.artifact_service = ArtifactLifecycleService(
            session,
            store=self.artifact_store,
            auto_commit=False,
        )

    async def execute_task(self, task_id: str) -> Task:
        """解析并执行截图任务，记录工具事件并更新任务最终状态。

        Args:
            task_id: 目标任务 ID。
        """
        task = await _load_pending_task(
            self.session,
            task_id,
            expected_task_type="screen",
        )
        command = parse_screen_command(
            task.input_text,
            default_target=self.settings.desktop_capture_default_target,
        )
        if command is None:
            return await _fail_task(self.session, task, "invalid_screen_command")

        events = TaskEventRepository(self.session)
        await events.append(
            task_id=task.id,
            user_id=task.user_id,
            event_type="screen.request.received",
            payload={"target": command.target, "platform": task.platform},
        )
        await _mark_running(self.session, task)
        await events.append(
            task_id=task.id,
            user_id=task.user_id,
            event_type="screen.capture.started",
            payload={"target": command.target, "tool": SCREEN_TOOL_NAME},
        )

        registry = self._build_registry(task)
        invocation = ToolInvocation(
            task_id=task.id,
            user_id=task.user_id,
            name=SCREEN_TOOL_NAME,
            arguments={
                "target": command.target,
                "format": "png",
                "idempotency_key": task.id,
            },
        )
        try:
            from tools.gateway import ToolGateway

            output = await ToolGateway(registry).execute(
                invocation,
                allowed_tools=(SCREEN_TOOL_NAME,),
                approval_required_tools=(),
            )
        except ToolSourceUnavailableError:
            await events.append(
                task_id=task.id,
                user_id=task.user_id,
                event_type="screen.capture.failed",
                payload={"reason": "desktop_capture_unavailable"},
            )
            return await _fail_task(self.session, task, "desktop_capture_unavailable")
        except ToolExecutionError as exc:
            safe_error = _safe_summary(exc)
            await events.append(
                task_id=task.id,
                user_id=task.user_id,
                event_type="screen.capture.failed",
                payload={"reason": safe_error},
            )
            return await _fail_task(self.session, task, safe_error)
        except ToolRegistryError as exc:
            safe_error = _safe_summary(exc)
            await events.append(
                task_id=task.id,
                user_id=task.user_id,
                event_type="screen.capture.failed",
                payload={"reason": safe_error},
            )
            return await _fail_task(self.session, task, safe_error)

        if not isinstance(output, dict):
            return await _fail_task(self.session, task, "desktop_capture_invalid_result")

        await events.append(
            task_id=task.id,
            user_id=task.user_id,
            event_type="screen.capture.succeeded",
            payload={
                "artifact_id": str(output.get("artifact_id", "")),
                "mime_type": str(output.get("mime_type", "")),
                "size_bytes": int(output.get("size_bytes", 0) or 0),
            },
        )
        result_text = self._build_success_summary(task=task, artifact=output)
        return await _succeed_task(self.session, task, result_text)

    def _build_registry(self, task: Task) -> ToolRegistry:
        """构造仅包含受治理截图工具的临时工具注册表。

        Args:
            task: 需要处理的任务对象。
        """
        registry = ToolRegistry(session=self.session)
        registry.register(
            ToolSpec(
                name=SCREEN_TOOL_NAME,
                description="Capture a local desktop/frontend screenshot as a task artifact.",
                risk_level="L2",
                handler=self._capture_handler,
                input_schema={
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "enum": ["desktop", "frontend"]},
                        "format": {"type": "string", "enum": ["png"], "default": "png"},
                        "idempotency_key": {"type": "string"},
                    },
                    "required": ["target"],
                    "additionalProperties": False,
                },
                version="v14.0",
                source_id="desktop_capture",
                source_available=self.settings.desktop_capture_enabled,
                idempotent=False,
                timeout_seconds=30.0,
            )
        )
        return registry

    async def _capture_handler(self, invocation: ToolInvocation) -> dict[str, Any]:
        """执行截图工具调用，校验 PNG 并将产物原子写入任务目录。

        Args:
            invocation: 用于执行当前操作的 invocation 参数。
        """
        target = str(invocation.arguments["target"])
        try:
            png = await self.screenshot_runner.capture_png(target=target)
        except DesktopCaptureUnavailable:
            raise
        except Exception as exc:
            raise DesktopCaptureUnavailable("desktop_capture_failed") from exc
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DesktopCaptureUnavailable("desktop_capture_invalid_png")
        artifact = await self.artifact_service.register_bytes(
            task_id=invocation.task_id,
            actor_user_id=invocation.user_id,
            filename=SCREEN_ARTIFACT_FILENAME,
            media_type="image/png",
            data=png,
            generation_method=SCREEN_TOOL_NAME,
            idempotency_key=f"screen:{invocation.task_id}",
        )
        reference = await self.artifact_service.managed_reference(
            artifact_id=artifact.id,
            task_id=invocation.task_id,
            actor_user_id=invocation.user_id,
        )
        return {
            "artifact_id": artifact.id,
            "filename": SCREEN_ARTIFACT_FILENAME,
            "mime_type": artifact.media_type,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
            "path": reference,
            "reference": reference,
            "url": self._artifact_url(invocation.task_id, reference),
            "target": target,
        }

    def _artifact_url(self, task_id: str, reference: str) -> str | None:
        """返回截图产物的对外 URL；当前版本默认不暴露公共链接。

        Args:
            task_id: 目标任务 ID。
            reference: 用于执行当前操作的 reference 参数。
        """
        del task_id, reference
        # V14 does not expose public artifact URLs by default; dispatch falls back to
        # task/artifact metadata when image upload is unavailable.
        return None

    def _build_success_summary(self, *, task: Task, artifact: dict[str, Any]) -> str:
        """生成不包含敏感路径的截图成功摘要。

        Args:
            task: 需要处理的任务对象。
            artifact: 用于执行当前操作的 artifact 参数。
        """
        del task
        return "\n".join(
            [
                "截图已生成。",
                f"目标: {artifact.get('target', 'unknown')}",
                f"截图文件: {artifact.get('filename', SCREEN_ARTIFACT_FILENAME)}",
                f"MIME: {artifact.get('mime_type', 'image/png')}",
                f"大小: {artifact.get('size_bytes', 0)} bytes",
            ]
        )


def screen_artifact_from_tool_log(log_output_text: str | None) -> dict[str, Any] | None:
    """从工具日志 JSON 中解析并校验截图产物元数据。

    Args:
        log_output_text: 用于执行当前操作的 log output text 参数。
    """
    if not log_output_text:
        return None
    try:
        payload = json.loads(log_output_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("mime_type") != "image/png" and payload.get("media_type") != "image/png":
        return None
    reference = payload.get("path") or payload.get("reference")
    if not isinstance(reference, str) or not reference:
        return None
    filename = payload.get("filename")
    if not isinstance(filename, str) or not filename:
        filename = Path(reference).name
    return {
        "artifact_id": str(payload.get("artifact_id", "")),
        "filename": filename,
        "mime_type": "image/png",
        "path": reference,
        "reference": reference,
        "url": payload.get("url") if isinstance(payload.get("url"), str) else None,
        "size_bytes": int(payload.get("size_bytes", 0) or 0),
    }
