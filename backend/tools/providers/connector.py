from __future__ import annotations

import json
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import ConnectorInstance, ConnectorTool
from tools.core.catalog import ToolDescriptor, ToolSourceKind
from tools.core.registry import ToolRiskLevel


class ReviewedConnectorToolSource:
    """定义当前组件的职责和边界。"""

    source_kind: ToolSourceKind = "mcp"

    def __init__(self, *, source_id: str, session: AsyncSession, tenant_id: str) -> None:
        """Bind catalog discovery to one tenant and database session.

        Args:
            source_id: 用于执行当前操作的 source id 参数。
            session: 当前数据库异步会话。
            tenant_id: 用于执行当前操作的 tenant id 参数。
        """
        self.source_id = source_id
        self.session = session
        self.tenant_id = tenant_id

    async def discover(self) -> tuple[ToolDescriptor, ...]:
        """执行当前组件定义的业务处理逻辑。"""
        rows = tuple(
            await self.session.scalars(
                select(ConnectorTool)
                .join(ConnectorInstance, ConnectorInstance.id == ConnectorTool.connector_instance_id)
                .where(
                    ConnectorTool.tenant_id == self.tenant_id,
                    ConnectorTool.enabled.is_(True),
                    ConnectorTool.available.is_(True),
                    ConnectorTool.internal_tool_key.is_not(None),
                    ConnectorInstance.status == "available",
                )
            )
        )
        return tuple(
            ToolDescriptor(
                name=str(row.internal_tool_key),
                description=row.description or "Governed Connector tool",
                input_schema=json.loads(row.input_schema_json),
                source_id=self.source_id,
                source_kind="mcp",
                version=row.provider_version,
                enabled=True,
                risk_level=_legacy_risk(row.risk_level),
                requires_approval=row.risk_level in {"R3", "R4"},
            )
            for row in rows
        )


def _legacy_risk(value: str | None) -> ToolRiskLevel:
    """Map governed risk to the catalog's migration-compatible legacy spelling.

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    normalized = value if value in {"R0", "R1", "R2", "R3", "R4"} else "R2"
    return cast(ToolRiskLevel, f"L{normalized[1]}")
