from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any


EXACT_APPROVAL_TOOLS = frozenset(
    {"email.send", "calendar.sync_event", "browser.interact", "browser.save_state"}
)


@dataclass(frozen=True)
class ExternalApprovalBinding:
    """表示 处理 external approval binding 的后端数据结构或服务对象。"""

    subject: str
    summary: str
    fingerprint: str


def external_approval_binding(
    tool_name: str, arguments: dict[str, Any]
) -> ExternalApprovalBinding:
    """处理 external approval binding。

    Args:
        tool_name: tool_name 参数。
        arguments: arguments 参数。
    """
    normalized = json.dumps(
        {"arguments": arguments, "tool": tool_name},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    fingerprint = sha256(normalized.encode("utf-8")).hexdigest()
    return ExternalApprovalBinding(
        subject=f"{tool_name}:{fingerprint}",
        summary=_summary(tool_name, arguments, fingerprint),
        fingerprint=fingerprint,
    )


def external_audit_arguments(
    tool_name: str, arguments: dict[str, Any]
) -> dict[str, str]:
    """处理 external audit arguments。

    Args:
        tool_name: tool_name 参数。
        arguments: arguments 参数。
    """
    binding = external_approval_binding(tool_name, arguments)
    return {
        "action_summary": binding.summary,
        "argument_fingerprint": binding.fingerprint,
    }


def _summary(tool_name: str, arguments: dict[str, Any], fingerprint: str) -> str:
    """执行 处理 summary 的内部辅助逻辑。

    Args:
        tool_name: tool_name 参数。
        arguments: arguments 参数。
        fingerprint: fingerprint 参数。
    """
    connection = _bounded(arguments.get("connection_id"), 36)
    if tool_name == "email.send":
        recipients = arguments.get("to")
        target = (
            ", ".join(str(item) for item in recipients)
            if isinstance(recipients, list)
            else ""
        )
        subject = _bounded(arguments.get("subject"), 120)
        return (
            f"发送邮件；连接={connection}；收件人={_bounded(target, 240)}；"
            f"主题={subject}；内容指纹={fingerprint[:16]}"
        )
    if tool_name == "calendar.sync_event":
        return (
            f"同步日历；连接={connection}；标题={_bounded(arguments.get('title'), 120)}；"
            f"开始={_bounded(arguments.get('start'), 64)}；结束={_bounded(arguments.get('end'), 64)}；"
            f"内容指纹={fingerprint[:16]}"
        )
    return (
        f"浏览器外部动作；连接={connection}；"
        f"目标={_bounded(arguments.get('url'), 240)}；参数指纹={fingerprint[:16]}"
    )


def _bounded(value: object, limit: int) -> str:
    """执行 处理 bounded 的内部辅助逻辑。

    Args:
        value: value 参数。
        limit: limit 参数。
    """
    return str(value or "").replace("\n", " ").replace("\r", " ")[:limit]
