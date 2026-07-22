import json
import logging
import sys
from collections.abc import MutableMapping
from typing import Any


SENSITIVE_LOG_FIELDS = {
    "api_key",
    "token",
    "cookie",
    "password",
    "secret",
    "dsn",
}


class JsonFormatter(logging.Formatter):
    """表示 处理 json formatter 的后端数据结构或服务对象。"""

    def format(self, record: logging.LogRecord) -> str:
        """处理 format。

        Args:
            record: record 参数。
        """
        payload: dict[str, Any] = {
            "level": record.levelname.lower(),
            "message": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class RedactingLogger(logging.LoggerAdapter[logging.Logger]):
    """表示 处理 redacting logger 的后端数据结构或服务对象。"""

    def process(
        self,
        msg: object,
        kwargs: MutableMapping[str, Any],
    ) -> tuple[object, MutableMapping[str, Any]]:
        """处理 process。

        Args:
            msg: msg 参数。
            kwargs: kwargs 参数。
        """
        kwargs.pop("extra", None)
        for key in tuple(kwargs):
            if key.lower() in SENSITIVE_LOG_FIELDS:
                kwargs.pop(key)
        return msg, kwargs


def configure_logging(level: str) -> RedactingLogger:
    """处理 configure logging。

    Args:
        level: level 参数。
    """
    logger = logging.getLogger("assistant_api")
    logger.handlers.clear()
    logger.setLevel(level.upper())
    logger.propagate = False

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    return RedactingLogger(logger, {})
