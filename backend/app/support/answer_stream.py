from __future__ import annotations

import json
import re


class FinalAnswerDeltaDecoder:
    def __init__(self) -> None:
        self.buffer = ""
        self.emitted = 0

    def feed(self, chunk: str) -> str:
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
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return index
    return None
