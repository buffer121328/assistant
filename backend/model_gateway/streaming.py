from __future__ import annotations

import json
import re


class FinalAnswerDeltaDecoder:
    """定义当前组件的职责和边界。"""

    def __init__(self) -> None:
        """初始化当前组件所需的依赖和状态。"""
        self.buffer = ""
        self.emitted = 0

    def feed(self, chunk: str) -> str:
        """Return newly decoded answer text from a streamed chunk.

        Args:
            chunk: 用于执行当前操作的 chunk 参数。
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
    """Return the first unescaped quote position, if present.

    Args:
        value: 待校验、归一化或转换的输入值。
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
