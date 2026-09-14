from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from application.enterprise_resources import CredentialService
from application.enterprise_runtime import GovernanceAuditInput, GovernanceAuditService
from domain.models import ConnectorInstance, ConnectorTool
from domain.policies.enterprise import GovernanceValidationError, SubjectContext


class ProductionProvider(Protocol):
    """定义当前组件的接口契约。"""

    async def call(
        self, *, tool_key: str, arguments: Mapping[str, object], credential: str
    ) -> Mapping[str, object]:
        """Execute one reviewed tool with an ephemeral governed credential.

        Args:
            tool_key: 已审核工具的稳定内部标识。
            arguments: 当前调用的结构化参数。
            credential: 仅用于本次调用的临时受治理凭据。
        """


class RestJsonProvider:
    """定义当前组件的职责和边界。"""

    def __init__(self, *, endpoint_template: str, timeout_seconds: float = 20.0) -> None:
        """初始化对象所需的运行时依赖和受限状态。

        Args:
            endpoint_template: 用于执行当前操作的 endpoint template 参数。
            timeout_seconds: 允许的最长等待时间（秒）。
        """
        if "{tool_key}" not in endpoint_template:
            raise ValueError("REST provider endpoint template requires {tool_key}")
        self.endpoint_template = endpoint_template
        self.timeout_seconds = timeout_seconds

    async def call(
        self, *, tool_key: str, arguments: Mapping[str, object], credential: str
    ) -> Mapping[str, object]:
        """执行 call 操作。

        Args:
            tool_key: 已审核工具的稳定内部标识。
            arguments: 当前调用的结构化参数。
            credential: 仅用于本次调用的临时受治理凭据。
        """
        url = self.endpoint_template.replace("{tool_key}", tool_key)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                url,
                json=dict(arguments),
                headers={"Authorization": f"Bearer {credential}"},
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise GovernanceValidationError("REST provider returned an invalid object")
        return payload


class McpHttpProvider:
    """定义当前组件的职责和边界。"""

    def __init__(self, *, endpoint: str, timeout_seconds: float = 20.0) -> None:
        """初始化对象所需的运行时依赖和受限状态。

        Args:
            endpoint: 用于执行当前操作的 endpoint 参数。
            timeout_seconds: 允许的最长等待时间（秒）。
        """
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    async def call(
        self, *, tool_key: str, arguments: Mapping[str, object], credential: str
    ) -> Mapping[str, object]:
        """执行 call 操作。

        Args:
            tool_key: 已审核工具的稳定内部标识。
            arguments: 当前调用的结构化参数。
            credential: 仅用于本次调用的临时受治理凭据。
        """
        request = {
            "jsonrpc": "2.0",
            "id": "governed-tool-call",
            "method": "tools/call",
            "params": {"name": tool_key, "arguments": dict(arguments)},
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.endpoint,
                json=request,
                headers={"Authorization": f"Bearer {credential}"},
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error") is not None:
            raise GovernanceValidationError("MCP provider returned an error")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise GovernanceValidationError("MCP provider returned an invalid result")
        return result


@dataclass(frozen=True)
class ProviderExecutionResult:
    """ProviderExecutionResult 的职责定义。"""
    tool_key: str  # tool_key 对应的数据字段。
    provider_key: str  # provider_key 对应的数据字段。
    result: Mapping[str, object]  # result 对应的数据字段。


class ProductionProviderGateway:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        session: AsyncSession,
        *,
        credential_service: CredentialService,
        providers: Mapping[str, ProductionProvider],
    ) -> None:
        """初始化对象所需的运行时依赖和受限状态。

        Args:
            session: 当前数据库异步会话。
            credential_service: 用于执行当前操作的 credential service 参数。
            providers: 用于执行当前操作的 providers 参数。
        """
        self.session = session
        self.credential_service = credential_service
        self.providers = providers

    async def execute(
        self,
        subject: SubjectContext,
        *,
        tool_id: str,
        arguments: Mapping[str, object],
        credential_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> ProviderExecutionResult:
        """执行 execute 操作。

        Args:
            subject: 用于执行当前操作的 subject 参数。
            tool_id: 用于执行当前操作的 tool id 参数。
            arguments: 当前调用的结构化参数。
            credential_id: 用于执行当前操作的 credential id 参数。
            task_id: 目标任务 ID。
            run_id: 用于执行当前操作的 run id 参数。
        """
        tool = await self.session.get(ConnectorTool, tool_id)
        if tool is None or tool.tenant_id != subject.tenant_id:
            raise GovernanceValidationError("Provider tool is unavailable")
        instance = await self.session.get(ConnectorInstance, tool.connector_instance_id)
        if (
            instance is None
            or instance.tenant_id != subject.tenant_id
            or instance.status != "available"
            or not tool.available
            or not tool.enabled
            or not tool.internal_tool_key
        ):
            await self._audit(subject, tool, task_id, run_id, "DENY", "provider_unavailable")
            raise GovernanceValidationError("Provider tool is not reviewed and healthy")
        provider = self.providers.get(instance.connector_id)
        if provider is None:
            await self._audit(subject, tool, task_id, run_id, "DENY", "provider_not_configured")
            raise GovernanceValidationError("Provider is not configured")
        if not credential_id:
            raise GovernanceValidationError("Governed provider credential is required")
        credential = await self.credential_service.resolve_for_execution(
            subject=subject, credential_id=credential_id, connector_id=instance.connector_id
        )
        try:
            result = await provider.call(
                tool_key=tool.internal_tool_key,
                arguments=dict(arguments),
                credential=credential,
            )
        except Exception as exc:
            await self._audit(subject, tool, task_id, run_id, "ALLOW", "provider_failed")
            raise GovernanceValidationError("Provider execution failed safely") from exc
        await self._audit(subject, tool, task_id, run_id, "ALLOW", "provider_succeeded")
        return ProviderExecutionResult(tool.internal_tool_key, instance.connector_id, result)

    async def _audit(
        self,
        subject: SubjectContext,
        tool: ConnectorTool,
        task_id: str | None,
        run_id: str | None,
        policy_decision: str,
        result_status: str,
    ) -> None:
        """执行 audit 的内部处理逻辑。

        Args:
            subject: 用于执行当前操作的 subject 参数。
            tool: 用于执行当前操作的 tool 参数。
            task_id: 目标任务 ID。
            run_id: 用于执行当前操作的 run id 参数。
            policy_decision: 用于执行当前操作的 policy decision 参数。
            result_status: 用于执行当前操作的 result status 参数。
        """
        await GovernanceAuditService(self.session).record(
            GovernanceAuditInput(
                tenant_id=subject.tenant_id,
                subject_id=subject.user_id,
                task_id=task_id,
                run_id=run_id,
                tool_key=tool.internal_tool_key,
                provider_key=tool.connector_instance_id,
                policy_decision=policy_decision,
                result_status=result_status,
                summary="Production provider gateway result",
            )
        )
