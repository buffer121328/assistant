from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
import pytest_asyncio
import httpx
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from application.enterprise_runtime import (
    EnterpriseAdminWriteService,
    governed_semantic_search,
    memory_retrieval_filter,
)
from application.enterprise_resources import CredentialService
from application.production_providers import (
    McpHttpProvider,
    ProductionProviderGateway,
    RestJsonProvider,
)
from domain.models import (
    Base,
    Organization,
    OrganizationMembership,
    Tenant,
    User,
    Connector,
    ConnectorInstance,
    ConnectorTool,
    Credential,
    GovernanceAudit,
)
from domain.policies.enterprise import (
    GovernedAgentProfile,
    GovernedRole,
    GovernanceValidationError,
    SubjectContext,
)
from infrastructure.security.rls_context import set_governance_context


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'production-gates.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _foundation(session: AsyncSession) -> SubjectContext:
    tenant = Tenant(id="gate-tenant", name="Gate tenant")
    organization = Organization(id="gate-finance", tenant_id=tenant.id, name="Finance")
    admin = User(id="gate-admin", tenant_id=tenant.id, display_name="Admin")
    employee = User(id="gate-employee", tenant_id=tenant.id, display_name="Employee")
    session.add_all((tenant, organization, admin, employee))
    session.add_all(
        (
            OrganizationMembership(
                tenant_id=tenant.id,
                organization_id=organization.id,
                user_id=admin.id,
                role=GovernedRole.ENTERPRISE_ADMIN.value,
            ),
            OrganizationMembership(
                tenant_id=tenant.id,
                organization_id=organization.id,
                user_id=employee.id,
                role=GovernedRole.MEMBER.value,
            ),
        )
    )
    await session.flush()
    return SubjectContext(
        tenant_id=tenant.id,
        user_id=admin.id,
        organization_ids=(organization.id,),
        roles=(GovernedRole.ENTERPRISE_ADMIN,),
        authority_revision=tenant.authority_revision,
    )


@pytest.mark.asyncio
async def test_admin_writes_are_role_scoped_and_tenant_bound(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        subject = await _foundation(session)
        created = await EnterpriseAdminWriteService(session).create_organization(
            subject, name="Sales"
        )
        assert created["name"] == "Sales"
        with pytest.raises(GovernanceValidationError):
            await EnterpriseAdminWriteService(session).create_organization(
                SubjectContext(
                    tenant_id=subject.tenant_id,
                    user_id="gate-employee",
                    organization_ids=("gate-finance",),
                    roles=(GovernedRole.MEMBER,),
                ),
                name="Forbidden",
            )


@pytest.mark.asyncio
async def test_shared_retrieval_uses_server_filter_and_fails_closed(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    class Provider:
        supports_governed_filters = True
        seen: Mapping[str, object] | None = None

        async def search(
            self, *, query: str, filters: Mapping[str, object], limit: int
        ) -> tuple[Mapping[str, object], ...]:
            self.seen = filters
            return ({"id": "shared"},)

    async with sessionmaker() as session:
        subject = await _foundation(session)
        profile = GovernedAgentProfile(
            subject=subject,
            capabilities=("finance.expense-review@1.0.0",),
            tools=("expense.read",),
            knowledge_scopes=("organization.finance",),
            memory_access=(("organization", "read"),),
        )
        provider = Provider()
        results = await governed_semantic_search(
            provider,
            query="finance",
            access_filter=memory_retrieval_filter(profile),
            limit=5,
            audit_session=session,
            provider_key="shared-memory",
        )
        assert results == ({"id": "shared"},)
        assert provider.seen is not None
        assert provider.seen["tenant_id"] == subject.tenant_id
        audit = await session.scalar(
            select(GovernanceAudit).where(
                GovernanceAudit.provider_key == "shared-memory"
            )
        )
        assert audit is not None
        assert audit.result_status == "retrieval_succeeded"

        class Unsupported:
            supports_governed_filters = False

            async def search(self, **_: object):
                raise AssertionError("unsupported provider must not be called")

        assert (
            await governed_semantic_search(
                Unsupported(),
                query="finance",
                access_filter=memory_retrieval_filter(profile),
                limit=5,
            )
            == ()
        )


@pytest.mark.asyncio
async def test_sqlite_rls_context_keeps_compatibility(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with sessionmaker() as session:
        await set_governance_context(session, tenant_id="gate-tenant", organization_ids=("gate-finance",))
        assert session.bind is not None
        assert session.bind.dialect.name == "sqlite"


@pytest.mark.asyncio
async def test_production_gateway_denies_unreviewed_and_calls_reviewed_tool(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    class Cipher:
        def encrypt(self, plaintext: str) -> str:
            return plaintext[::-1]

        def decrypt(self, ciphertext: str) -> str:
            return ciphertext[::-1]

    class Provider:
        calls = 0

        async def call(self, *, tool_key: str, arguments, credential: str):
            self.calls += 1
            assert credential == "secret"
            return {"ok": True, "tool": tool_key, "arguments": dict(arguments)}

    async with sessionmaker() as session:
        subject = await _foundation(session)
        connector = Connector(
            id="gate-connector",
            tenant_id=subject.tenant_id,
            key="finance",
            type="MCP",
            display_name="Finance MCP",
            created_by=subject.user_id,
        )
        instance = ConnectorInstance(
            id="gate-instance",
            tenant_id=subject.tenant_id,
            connector_id=connector.id,
            organization_id="gate-finance",
            auth_mode="governed",
            status="available",
        )
        tool = ConnectorTool(
            id="gate-tool",
            tenant_id=subject.tenant_id,
            connector_instance_id=instance.id,
            external_name="expense.read",
            internal_tool_key="expense.read",
            risk_level="R1",
            enabled=False,
            available=True,
        )
        session.add_all((connector, instance, tool))
        await session.flush()

        credential = Credential(
            id="gate-credential",
            tenant_id=subject.tenant_id,
            scope_type="ORGANIZATION",
            owner_id="gate-finance",
            connector_id=connector.id,
            encrypted_payload="terces",
            status="active",
        )
        session.add(credential)
        await session.flush()
        provider = Provider()
        gateway = ProductionProviderGateway(
            session,
            credential_service=CredentialService(session, Cipher()),
            providers={connector.id: provider},
        )
        with pytest.raises(GovernanceValidationError):
            await gateway.execute(subject, tool_id=tool.id, arguments={}, credential_id=credential.id)
        assert provider.calls == 0
        tool.enabled = True
        await gateway.execute(subject, tool_id=tool.id, arguments={"expense_id": "e1"}, credential_id=credential.id)
        assert provider.calls == 1


def test_electron_employee_and_admin_surfaces_are_separate() -> None:
    root = Path(__file__).parents[2] / "frontend" / "desktop" / "src" / "renderer"
    employee_api = (root / "api.ts").read_text(encoding="utf-8")
    employee_app = (root / "App.tsx").read_text(encoding="utf-8")
    admin_api = (root / "admin-api.ts").read_text(encoding="utf-8")
    assert "/api/admin/" not in employee_api
    assert "/api/admin/" not in employee_app
    assert "/api/admin/" in admin_api
    assert (root / "admin.html").exists()


@pytest.mark.asyncio
@respx.mock
async def test_production_rest_and_mcp_adapters_use_governed_credentials() -> None:
    rest_route = respx.post("https://provider.invalid/tools/expense.read").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    rest = await RestJsonProvider(
        endpoint_template="https://provider.invalid/tools/{tool_key}"
    ).call(tool_key="expense.read", arguments={"id": "e1"}, credential="secret")
    assert rest == {"ok": True}
    assert rest_route.calls[0].request.headers["authorization"] == "Bearer secret"

    mcp_route = respx.post("https://mcp.invalid/rpc").mock(
        return_value=httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": "governed-tool-call",
                "result": {"content": []},
            },
        )
    )
    mcp = await McpHttpProvider(endpoint="https://mcp.invalid/rpc").call(
        tool_key="expense.read", arguments={"id": "e1"}, credential="secret"
    )
    assert mcp == {"content": []}
    body = mcp_route.calls[0].request.content.decode("utf-8")
    assert '"method":"tools/call"' in body
