from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.capabilities import CapabilityRegistry
from application.enterprise_resources import (
    ConnectorService,
    CredentialService,
    SkillCapabilityService,
)
from channels.base import IncomingMessage, OutgoingMessage
from domain.models import Base, CapabilityDefinition, Organization, Tenant, User
from domain.policies.enterprise import GovernanceValidationError, GovernedRole, SubjectContext
from tools import ToolCatalog
from tools.gateway import ToolGateway
from tools.core.registry import ToolInvocation
from tools.providers.connector import ReviewedConnectorToolSource


@pytest_asyncio.fixture
async def sessionmaker(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    """Provide isolated persistence for phases 03 through 05 acceptance contracts."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'vnext-03-05.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _foundation(session: AsyncSession) -> tuple[User, Organization]:
    """Create the minimal same-tenant identity records used by service tests."""
    tenant = Tenant(id="tenant-a", name="Tenant A", status="active")
    organization = Organization(id="org-a", tenant_id=tenant.id, name="Sales", type="department", status="active")
    user = User(id="publisher", tenant_id=tenant.id, display_name="Publisher")
    session.add_all((tenant, organization, user))
    await session.flush()
    return user, organization


def _subject(*roles: GovernedRole, user_id: str = "publisher") -> SubjectContext:
    """Build a trusted tenant subject for one acceptance scenario."""
    return SubjectContext("tenant-a", user_id, ("org-a",), roles)


@pytest.mark.asyncio
async def test_skill_versions_are_declarative_tested_immutable_and_stable(sessionmaker: async_sessionmaker) -> None:
    """A publisher can publish data-only versions while executable content and weaker roles fail."""
    async with sessionmaker() as session:
        await _foundation(session)
        service = SkillCapabilityService(session)
        publisher = _subject(GovernedRole.CAPABILITY_PUBLISHER)
        draft = await service.save_draft(subject=publisher, key="sales.customer-research", display_name="Customer research", content={"skill_md": "Research the customer using governed sources.", "tools": ["crm.customer.read"], "knowledge": ["sales.playbook"], "risk": "R1", "execution": {"mode": "server"}})
        await service.test_draft(subject=publisher, skill_id=draft.skill_id)
        published = await service.publish_skill(subject=publisher, skill_id=draft.skill_id, version="1.0.0")
        stable = await service.promote_stable(subject=publisher, skill_id=draft.skill_id, version="1.0.0")

        assert stable.id == published.id
        with pytest.raises(GovernanceValidationError, match="immutable"):
            await service.publish_skill(subject=publisher, skill_id=draft.skill_id, version="1.0.0")
        with pytest.raises(GovernanceValidationError, match="management role"):
            await service.save_draft(subject=_subject(GovernedRole.DEPARTMENT_ADMIN), key="sales.denied", display_name="Denied", content={"skill_md": "No publication authority", "execution": {"mode": "server"}})
        with pytest.raises(GovernanceValidationError, match="executable"):
            await service.save_draft(subject=publisher, key="sales.unsafe", display_name="Unsafe", content={"skill_md": "```python\nprint('unsafe')\n```", "execution": {"mode": "server"}})


@pytest.mark.asyncio
async def test_capability_publication_pins_stable_skill_and_projects_data_only(sessionmaker: async_sessionmaker) -> None:
    """Published Capability composition records fixed versions and loads only declarative data."""
    async with sessionmaker() as session:
        await _foundation(session)
        subject = _subject(GovernedRole.ENTERPRISE_ADMIN)
        service = SkillCapabilityService(session)
        draft = await service.save_draft(subject=subject, key="sales.customer-research", display_name="Research", content={"skill_md": "Use approved research sources.", "execution": {"mode": "server"}})
        await service.test_draft(subject=subject, skill_id=draft.skill_id)
        await service.publish_skill(subject=subject, skill_id=draft.skill_id, version="2.0.0")
        await service.promote_stable(subject=subject, skill_id=draft.skill_id, version="2.0.0")
        capability = CapabilityDefinition(tenant_id="tenant-a", key="sales.research", version="1.0.0", display_name="Sales research", summary="Research customers", status="active")
        session.add(capability)
        await session.flush()

        version = await service.publish_capability(subject=subject, capability_id=capability.id, version="1.0.0", skill_references=("sales.customer-research@stable",), tools=("crm.customer.read",))
        assert "sales.customer-research@2.0.0" in version.composition_json
        registry = CapabilityRegistry()
        assert await service.project_published_capabilities(tenant_id="tenant-a", registry=registry) == ("capability.sales.research",)
        resolved = cast(dict[str, object], registry.resolve("capability.sales.research"))
        assert resolved["skills"] == ["sales.customer-research@2.0.0"]
        distributed = await service.validate_distribution(
            subject=_subject(GovernedRole.DEPARTMENT_ADMIN),
            capability_version_id=version.id,
            organization_id="org-a",
        )
        assert distributed.id == version.id


class _ConnectorClient:
    """Deterministic external Connector fake with no network dependency."""

    async def test_connection(self) -> None:
        """Report a successful test connection."""

    async def list_tools(self) -> tuple[dict[str, object], ...]:
        """Return one raw provider tool for explicit review."""
        return ({"name": "search_customer", "description": "Search customers", "input_schema": {"type": "object", "properties": {}}, "version": "7"},)


@pytest.mark.asyncio
async def test_mcp_sync_is_disabled_until_internal_key_review(sessionmaker: async_sessionmaker) -> None:
    """Discovery never enables a raw MCP name and catalog projection uses the reviewed key."""
    async with sessionmaker() as session:
        _, organization = await _foundation(session)
        subject = _subject(GovernedRole.CONNECTOR_ADMIN)
        service = ConnectorService(session)
        connector = await service.create_connector(subject=subject, key="salesforce", connector_type="MCP", display_name="Salesforce")
        instance = await service.create_instance(subject=subject, connector_id=connector.id, organization_id=organization.id, auth_mode="bearer")
        await service.test_instance(subject=subject, instance_id=instance.id, client=_ConnectorClient())
        tools = await service.sync_tools(subject=subject, instance_id=instance.id, client=_ConnectorClient())
        assert tools[0].enabled is False
        await service.review_tool(subject=subject, tool_id=tools[0].id, internal_tool_key="crm.customer.search", risk_level="R2", enabled=True)

        snapshot = await ToolCatalog((ReviewedConnectorToolSource(source_id="salesforce", session=session, tenant_id="tenant-a"),)).refresh()
        assert snapshot.get("crm.customer.search") is not None
        assert snapshot.get("search_customer") is None

        foreign = Tenant(id="tenant-b", name="Tenant B", status="active")
        foreign_org = Organization(id="org-b", tenant_id="tenant-b", name="Other", type="department", status="active")
        session.add_all((foreign, foreign_org))
        await session.flush()
        with pytest.raises(GovernanceValidationError, match="cross tenants"):
            await service.create_instance(subject=subject, connector_id=connector.id, organization_id=foreign_org.id, auth_mode="bearer")


class _Cipher:
    """Reversible test cipher that makes plaintext persistence assertions explicit."""

    def encrypt(self, plaintext: str) -> str:
        """Encode a deterministic non-plaintext fixture value."""
        return "encrypted:" + plaintext[::-1]

    def decrypt(self, ciphertext: str) -> str:
        """Decode only values created by this test cipher."""
        if not ciphertext.startswith("encrypted:"):
            raise ValueError("invalid ciphertext")
        return ciphertext.removeprefix("encrypted:")[::-1]


@pytest.mark.asyncio
async def test_credentials_are_opaque_scoped_and_execution_only(sessionmaker: async_sessionmaker) -> None:
    """Credential APIs persist ciphertext and reject subjects outside the owner scope."""
    async with sessionmaker() as session:
        await _foundation(session)
        subject = _subject(GovernedRole.CONNECTOR_ADMIN)
        connector = await ConnectorService(session).create_connector(subject=subject, key="crm", connector_type="REST", display_name="CRM")
        service = CredentialService(session, _Cipher())
        record = await service.store(subject=subject, scope_type="USER", owner_id=subject.user_id, connector_id=connector.id, plaintext="top-secret")
        assert record.encrypted_payload != "top-secret"
        assert await service.resolve_for_execution(subject=subject, credential_id=record.id, connector_id=connector.id) == "top-secret"
        with pytest.raises(GovernanceValidationError, match="unavailable"):
            await service.resolve_for_execution(subject=_subject(GovernedRole.CONNECTOR_ADMIN, user_id="other"), credential_id=record.id, connector_id=connector.id)


@pytest.mark.asyncio
async def test_gateway_is_the_runtime_facade_and_channels_carry_no_authority() -> None:
    """Gateway delegates once while normalized channel values contain no roles or credentials."""
    calls: list[str] = []

    class Registry:
        """Capture one gateway delegation without database concerns."""

        async def execute(self, invocation: ToolInvocation, **_: object) -> dict[str, bool]:
            """Record the delegated internal tool identity."""
            calls.append(invocation.name)
            return {"ok": True}

    invocation = ToolInvocation("task", "publisher", "crm.customer.search")
    result = await ToolGateway(cast(Any, Registry())).execute(invocation, allowed_tools=("crm.customer.search",), approval_required_tools=())
    incoming = IncomingMessage("langbot", "default", "sender", "conversation", "group", "research", "message-1")
    outgoing = OutgoingMessage("result-1", incoming.conversation_id, "done")

    assert result == {"ok": True}
    assert calls == ["crm.customer.search"]
    assert not hasattr(incoming, "roles")
    assert not hasattr(incoming, "credential")
    assert outgoing.message_id == "result-1"


def test_phase_03_05_migration_is_additive_and_contains_no_plaintext_column() -> None:
    """The migration carries all governed records and persists ciphertext only."""
    migration = (Path(__file__).resolve().parents[2] / "backend/migrations/versions/202608100002_vnext_capability_connector_credential.py").read_text(encoding="utf-8")
    for table in ("skill_versions", "capability_versions", "connectors", "connector_instances", "connector_tools", "credentials"):
        assert f'"{table}"' in migration
    assert '"encrypted_payload"' in migration
    assert '"plaintext"' not in migration
