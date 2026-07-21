from __future__ import annotations

import importlib

from domain.models import Base


def test_legacy_schedule_table_is_removed_from_current_metadata() -> None:
    assert "scheduled_task_runs" not in Base.metadata.tables
    assert "agent_schedules" in Base.metadata.tables
    assert "agent_schedule_runs" in Base.metadata.tables


def test_v11_schedule_cleanup_migration_is_linear_and_reversible() -> None:
    migration = importlib.import_module(
        "backend.migrations.versions.202607210002_v11_schedule_legacy_removal"
    )

    assert migration.revision == "202607210002"
    assert migration.down_revision == "202607210001"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)
