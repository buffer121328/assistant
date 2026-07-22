from __future__ import annotations

import json
import re


class FinalAnswerDeltaDecoder:
    """表示 处理 final answer delta decoder 的后端数据结构或服务对象。"""

    def __init__(self) -> None:
        """初始化对象实例。"""
        self.buffer = ""
        self.emitted = 0

    def feed(self, chunk: str) -> str:
        """处理 feed。

        Args:
            chunk: chunk 参数。
        """
        self.buffer += chunk
        if not re.search(r'"action"\s*:\s*"final"', self.buffer):
            return ""
        match = re.search(r'"answer"\s*:\s*"', self.buffer)
        if match is None:
            return ""
        encoded = self.buffer[match.end() :]
        end = _closing_quote(encoded)
        candidate = encoded if end is None else encoded[:end]
        try:
            decoded = json.loads('"' + candidate + '"')
        except json.JSONDecodeError:
            return ""
        delta = decoded[self.emitted :]
        self.emitted = len(decoded)
        return delta


def _closing_quote(value: str) -> int | None:
    """执行 处理 closing quote 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return index
    return None
