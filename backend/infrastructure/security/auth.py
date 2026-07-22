from __future__ import annotations

from hmac import compare_digest

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

PUBLIC_PATHS = frozenset({"/health", "/local/health", "/api/webhooks/langbot"})
PROTECTED_PREFIXES = ("/api/", "/internal/", "/local/")


class LocalApiAuthMiddleware(BaseHTTPMiddleware):
    """表示 处理 local api auth middleware 的后端数据结构或服务对象。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """分发。

        Args:
            request: request 参数。
            call_next: call_next 参数。
        """
        settings = request.app.state.settings
        if (
            request.url.path in PUBLIC_PATHS
            or not request.url.path.startswith(PROTECTED_PREFIXES)
            or not settings.local_api_auth_required
        ):
            return await call_next(request)

        expected = settings.local_api_token.get_secret_value()
        if not expected:
            return _error("local_api_auth_unconfigured", 503)

        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if (
            scheme.lower() != "bearer"
            or not supplied
            or not compare_digest(supplied, expected)
        ):
            return _error("local_api_auth_failed", 401)
        return await call_next(request)


def _error(code: str, status_code: int) -> JSONResponse:
    """执行 处理 error 的内部辅助逻辑。

    Args:
        code: code 参数。
        status_code: status_code 参数。
    """
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": "Local API authentication failed"}},
    )
