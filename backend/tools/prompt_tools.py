from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from agent.prompting import PromptStore
from domain.models import ToolLog
from models import sanitize_text

from .catalog import ToolDescriptor
from .registry import ToolInvocation, ToolRiskLevel, ToolSpec

PROMPT_TOOL_VERSION = "v10-prompt-tools-v1"


@dataclass
class PromptToolService:
    """表示 处理 prompt tool service 的后端数据结构或服务对象。"""

    session: AsyncSession
    store: PromptStore

    async def inspect(self, invocation: ToolInvocation) -> dict[str, object]:
        """处理 inspect。

        Args:
            invocation: invocation 参数。
        """
        result = self.store.inspect()
        modules = result.get("modules", [])
        module_count = len(modules) if isinstance(modules, list) else 0
        await self._audit(
            invocation,
            "prompt.inspect",
            "succeeded",
            {"module_count": module_count},
            None,
        )
        return result

    async def propose_change(self, invocation: ToolInvocation) -> dict[str, object]:
        """处理 propose change。

        Args:
            invocation: invocation 参数。
        """
        change = await self.store.propose_change(
            session=self.session,
            task_id=invocation.task_id,
            user_id=invocation.user_id,
            module_name=str(invocation.arguments.get("module") or ""),
            content=str(invocation.arguments.get("content") or ""),
            evidence=str(invocation.arguments.get("evidence") or ""),
        )
        await self._audit(
            invocation,
            "prompt.propose_change",
            "succeeded",
            {
                "change_id": change.id,
                "target_name": change.target_name,
                "status": change.status,
            },
            None,
        )
        return {
            "change_id": change.id,
            "status": change.status,
            "target_name": change.target_name,
        }

    async def list_versions(self, invocation: ToolInvocation) -> dict[str, object]:
        """列出 versions。

        Args:
            invocation: invocation 参数。
        """
        result = self.store.list_versions()
        versions = result.get("versions", [])
        version_count = len(versions) if isinstance(versions, list) else 0
        await self._audit(
            invocation,
            "prompt.list_versions",
            "succeeded",
            {"version_count": version_count},
            None,
        )
        return result

    async def rollback(self, invocation: ToolInvocation) -> dict[str, object]:
        """处理 rollback。

        Args:
            invocation: invocation 参数。
        """
        change = await self.store.rollback(
            session=self.session,
            change_id=str(invocation.arguments.get("change_id") or ""),
            user_id=invocation.user_id,
        )
        await self._audit(
            invocation,
            "prompt.rollback",
            "succeeded",
            {"change_id": change.id, "status": change.status},
            None,
        )
        return {"change_id": change.id, "status": change.status}

    async def _audit(
        self,
        invocation: ToolInvocation,
        tool_name: str,
        status: str,
        output: object | None,
        error: str | None,
    ) -> None:
        """执行 处理 audit 的内部辅助逻辑。

        Args:
            invocation: invocation 参数。
            tool_name: tool_name 参数。
            status: status 参数。
            output: output 参数。
            error: error 参数。
        """
        args = dict(invocation.arguments)
        if "content" in args:
            args["content"] = "[redacted-prompt-content]"
        self.session.add(
            ToolLog(
                task_id=invocation.task_id,
                tool_name=tool_name,
                status=status,
                input_text=sanitize_text(
                    {
                        "tool": tool_name,
                        "task_id": invocation.task_id,
                        "user_id": invocation.user_id,
                        "arguments": args,
                    }
                ),
                output_text=sanitize_text(output) if output is not None else None,
                error_message=sanitize_text(error) if error else None,
            )
        )
        await self.session.flush()


def build_prompt_tool_descriptors(
    *, enabled: bool = True
) -> tuple[ToolDescriptor, ...]:
    """构建 prompt tool descriptors。

    Args:
        enabled: enabled 参数。
    """
    return tuple(
        ToolDescriptor(
            name=name,
            description=description,
            input_schema=schema,
            source_id="builtin",
            source_kind="builtin",
            version=PROMPT_TOOL_VERSION,
            enabled=enabled,
            risk_level=cast(ToolRiskLevel, risk),
            requires_approval=risk != "L1",
            tags=("prompt", "governance", "v10"),
            parallel_safe=risk == "L1",
        )
        for name, description, risk, schema in _PROMPT_TOOL_DEFS
    )


def build_prompt_tool_specs(service: PromptToolService) -> tuple[ToolSpec, ...]:
    """构建 prompt tool specs。

    Args:
        service: service 参数。
    """
    handlers = {
        "prompt.inspect": service.inspect,
        "prompt.propose_change": service.propose_change,
        "prompt.list_versions": service.list_versions,
        "prompt.rollback": service.rollback,
    }
    return tuple(
        ToolSpec(
            name=name,
            description=description,
            risk_level=cast(ToolRiskLevel, risk),
            handler=handlers[name],
            handler_records_log=True,
            input_schema=schema,
            version=PROMPT_TOOL_VERSION,
            source_id="builtin",
            parallel_safe=risk == "L1",
        )
        for name, description, risk, schema in _PROMPT_TOOL_DEFS
    )


_PROMPT_TOOL_DEFS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    (
        "prompt.inspect",
        "Inspect prompt module fingerprints and safe summaries",
        "L1",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    (
        "prompt.propose_change",
        "Create governed prompt change proposal without writing production override",
        "L2",
        {
            "type": "object",
            "properties": {
                "module": {"type": "string"},
                "content": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["module", "content", "evidence"],
            "additionalProperties": False,
        },
    ),
    (
        "prompt.list_versions",
        "List bounded prompt override version metadata",
        "L1",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    (
        "prompt.rollback",
        "Rollback an applied governed prompt change",
        "L2",
        {
            "type": "object",
            "properties": {"change_id": {"type": "string"}},
            "required": ["change_id"],
            "additionalProperties": False,
        },
    ),
)
