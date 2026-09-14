from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
from typing import Literal, Protocol

import httpx

from infrastructure.settings.config import Settings

ScreenTarget = Literal["desktop", "frontend"]


class ScreenshotRunner(Protocol):
    """定义当前组件的接口契约。"""

    async def capture_png(self, *, target: str) -> bytes:
        """Return PNG bytes for the requested target.

        Args:
            target: 用于执行当前操作的 target 参数。
        """
        ...


class DesktopCaptureUnavailable(RuntimeError):
    """定义当前组件的职责和边界。"""

    pass


class LocalProcessScreenshotRunner:
    """定义当前组件的职责和边界。"""

    async def capture_png(self, *, target: str) -> bytes:
        """通过本机 screencapture 命令抓取指定目标并返回 PNG 字节。

        Args:
            target: 用于执行当前操作的 target 参数。
        """
        if target not in {"desktop", "frontend"}:
            raise DesktopCaptureUnavailable("unsupported_screen_target")
        with tempfile.TemporaryDirectory(prefix="assistant-screen-") as tempdir:
            output = Path(tempdir) / "screenshot.png"
            try:
                process = await asyncio.create_subprocess_exec(
                    "screencapture",
                    "-x",
                    str(output),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise DesktopCaptureUnavailable("desktop_capture_command_unavailable") from exc
            _, stderr = await process.communicate()
            if process.returncode != 0:
                message = (stderr or b"").decode("utf-8", errors="ignore").strip()
                raise DesktopCaptureUnavailable(message or "desktop_capture_failed")
            data = output.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise DesktopCaptureUnavailable("desktop_capture_invalid_png")
            return data


class ControllerHttpScreenshotRunner:
    """定义当前组件的职责和边界。"""

    def __init__(self, settings: Settings) -> None:
        """保存用于调用宿主机截图控制器的运行配置。

        Args:
            settings: 用于执行当前操作的 settings 参数。
        """
        self.settings = settings

    async def capture_png(self, *, target: str) -> bytes:
        """通过受配置约束的 HTTP 控制器获取指定目标的 PNG 图像。

        Args:
            target: 用于执行当前操作的 target 参数。
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self.settings.desktop_capture_controller_url,
                    json={"target": target, "format": "png"},
                )
        except httpx.TransportError as exc:
            raise DesktopCaptureUnavailable("desktop_controller_unavailable") from exc
        if response.status_code >= 400:
            raise DesktopCaptureUnavailable("desktop_controller_unavailable")
        content_type = response.headers.get("content-type", "")
        if "image/png" not in content_type and not response.content.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            raise DesktopCaptureUnavailable("desktop_controller_invalid_png")
        return response.content


def build_screenshot_runner(settings: Settings) -> ScreenshotRunner:
    """根据配置选择本机进程或 HTTP 控制器截图实现。

    Args:
        settings: 用于执行当前操作的 settings 参数。
    """
    if settings.desktop_capture_mode == "controller_http":
        return ControllerHttpScreenshotRunner(settings)
    return LocalProcessScreenshotRunner()
