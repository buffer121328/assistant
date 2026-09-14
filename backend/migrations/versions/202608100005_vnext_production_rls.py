"""add PostgreSQL tenant and organization RLS policies."""

from collections.abc import Sequence

from alembic import op

revision: str = "202608100005"
down_revision: str | None = "202608100004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = (
    "organizations",
    "organization_memberships",
    "policy_rules",
    "tasks",
    "task_events",
    "memories",
    "knowledge_documents",
    "knowledge_chunks",
    "capability_definitions",
    "capability_versions",
    "capability_grants",
    "skill_definitions",
    "skill_drafts",
    "skill_versions",
    "connectors",
    "connector_instances",
    "connector_tools",
    "credentials",
    "governance_audits",
)


def upgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    op.execute(
        """
        DO $$
        BEGIN
          IF current_setting('server_version_num', true) IS NOT NULL THEN
            CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS text
            LANGUAGE sql STABLE AS $fn$ SELECT NULLIF(current_setting('app.tenant_id', true), '') $fn$;
            CREATE OR REPLACE FUNCTION app_current_organizations() RETURNS text[]
            LANGUAGE sql STABLE AS $fn$
              SELECT CASE WHEN NULLIF(current_setting('app.organization_ids', true), '') IS NULL
                THEN ARRAY[]::text[]
                ELSE string_to_array(current_setting('app.organization_ids', true), ',') END
            $fn$;
          END IF;
        END $$;
        """
    )
    for table in _TABLES:
        op.execute(
            f"""
            DO $$ BEGIN
              IF to_regclass('public.{table}') IS NOT NULL THEN
                ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
                ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
                DROP POLICY IF EXISTS app_tenant_isolation ON {table};
                CREATE POLICY app_tenant_isolation ON {table}
                  USING (tenant_id = app_current_tenant())
                  WITH CHECK (tenant_id = app_current_tenant());
              END IF;
            END $$;
            """
        )
    for table in ("memories", "knowledge_documents", "capability_grants", "connector_instances"):
        op.execute(
            f"""
            DO $$ BEGIN
              IF to_regclass('public.{table}') IS NOT NULL
                 AND EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = '{table}' AND column_name = 'organization_id') THEN
                DROP POLICY IF EXISTS app_organization_scope ON {table};
                CREATE POLICY app_organization_scope ON {table}
                  USING (organization_id IS NULL OR organization_id = ANY(app_current_organizations()))
                  WITH CHECK (organization_id IS NULL OR organization_id = ANY(app_current_organizations()));
              END IF;
            END $$;
            """
        )


def downgrade() -> None:
    """执行当前组件定义的业务处理逻辑。"""
    for table in reversed(_TABLES):
        op.execute(
            f"""
            DO $$ BEGIN
              IF to_regclass('public.{table}') IS NOT NULL THEN
                DROP POLICY IF EXISTS app_organization_scope ON {table};
                DROP POLICY IF EXISTS app_tenant_isolation ON {table};
                ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;
                ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;
              END IF;
            END $$;
            """
        )
    op.execute("DROP FUNCTION IF EXISTS app_current_organizations();")
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant();")
