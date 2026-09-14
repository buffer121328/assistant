from __future__ import annotations


def _truncate(value: str, limit: int = 1000) -> str:
    """执行 处理 truncate 的内部辅助逻辑。

    Args:
        value: value 参数。
        limit: limit 参数。
    """
    if len(value) <= limit:
        return value
    return f"{value[:limit]}..."
