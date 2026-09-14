from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """表示 处理 app error 的后端数据结构或服务对象。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        """初始化对象实例。

        Args:
            code: code 参数。
            message: message 参数。
            status_code: status_code 参数。
        """
        self.code = code
        self.message = message
        self.status_code = status_code


def error_response(code: str, message: str, status_code: int) -> JSONResponse:
    """处理 error response。

    Args:
        code: code 参数。
        message: message 参数。
        status_code: status_code 参数。
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
            }
        },
    )


async def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """处理 app error handler。

    Args:
        _request: _request 参数。
        exc: exc 参数。
    """
    if not isinstance(exc, AppError):
        raise exc
    return error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
    )


async def http_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    """处理 http error handler。

    Args:
        _request: _request 参数。
        exc: exc 参数。
    """
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    if exc.status_code == 404:
        return error_response(
            code="not_found",
            message="Resource not found",
            status_code=404,
        )
    return error_response(
        code="http_error",
        message=str(exc.detail),
        status_code=exc.status_code,
    )


async def request_validation_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    """处理 request validation error handler。

    Args:
        _request: _request 参数。
        exc: exc 参数。
    """
    if not isinstance(exc, RequestValidationError):
        raise exc
    return error_response(
        code="validation_error",
        message="Invalid request",
        status_code=422,
    )
