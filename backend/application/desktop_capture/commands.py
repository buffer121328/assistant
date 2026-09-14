from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ScreenTarget = Literal["desktop", "frontend"]
VALID_SCREEN_TARGETS: frozenset[str] = frozenset({"desktop", "frontend"})


@dataclass(frozen=True)
class ScreenCommand:
    """定义当前组件的职责和边界。"""

    target: ScreenTarget  # target 对应的数据字段。


def parse_screen_command(
    text: str,
    *,
    default_target: ScreenTarget = "frontend",
) -> ScreenCommand | None:
    """Parse explicit /screen commands without accepting natural language control.

    Args:
        text: 需要处理的文本内容。
        default_target: 用于执行当前操作的 default target 参数。
    """
    normalized = text.strip()
    if not normalized:
        return None
    parts = normalized.split()
    if parts[0] != "/screen":
        return None
    if len(parts) == 1:
        return ScreenCommand(target=default_target)
    if len(parts) > 2 or parts[1] not in VALID_SCREEN_TARGETS:
        raise ValueError("unsupported_screen_target")
    return ScreenCommand(target=parts[1])  # type: ignore[arg-type]
