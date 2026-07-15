from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from scripts.ops import backup, restore
from scripts.ops.soak import ProbeResult, run_soak
from scripts.ops.db_common import COUNTED_TABLES, DatabaseTarget, OpsError, migration_version


def test_database_target_never_places_password_in_command_or_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://assistant:private-password@127.0.0.1:5432/assistant",
    )
    target = DatabaseTarget.from_environment()
    command = target.command("pg_dump")
    assert "private-password" not in repr(target)
    assert "private-password" not in " ".join(command)
    assert target.environment()["PGPASSWORD"] == "private-password"


def test_backup_writes_hash_manifest_and_table_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = DatabaseTarget("localhost", 5432, "assistant", "assistant", "secret")
    monkeypatch.setattr(backup.DatabaseTarget, "from_environment", lambda: target)

    def fake_capture(target_value: DatabaseTarget, command: list[str]) -> str:
        del target_value
        file_arg = next((item for item in command if item.startswith("--file=")), None)
        if file_arg:
            Path(file_arg.removeprefix("--file=")).write_bytes(b"verified-backup")
        return ""

    counts = {table: index for index, table in enumerate(COUNTED_TABLES)}
    monkeypatch.setattr(backup, "capture", fake_capture)
    monkeypatch.setattr(backup, "migration_version", lambda target_value: "202607140005")
    monkeypatch.setattr(backup, "table_counts", lambda target_value: counts)
    monkeypatch.setattr(sys, "argv", ["backup", "--output-dir", str(tmp_path)])
    assert backup.main() == 0
    manifests = list(tmp_path.glob("*.manifest.json"))
    assert len(manifests) == 1
    value = json.loads(manifests[0].read_text(encoding="utf-8"))
    dump = tmp_path / value["backup_file"]
    assert value["format"] == "assistant-postgres-backup-v1"
    assert value["migration_version"] == "202607140005"
    assert value["table_counts"] == counts
    assert len(value["sha256"]) == 64
    assert dump.read_bytes() == b"verified-backup"


def test_restore_requires_empty_database_and_matching_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = DatabaseTarget("localhost", 5432, "assistant", "assistant", "secret")
    dump = tmp_path / "assistant.dump"
    dump.write_bytes(b"verified-backup")
    from hashlib import sha256

    counts = {table: 1 for table in COUNTED_TABLES}
    manifest = tmp_path / "assistant.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "assistant-postgres-backup-v1",
                "backup_file": dump.name,
                "sha256": sha256(dump.read_bytes()).hexdigest(),
                "migration_version": "202607140005",
                "table_counts": counts,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(restore.DatabaseTarget, "from_environment", lambda: target)
    commands: list[list[str]] = []

    def fake_capture(target_value: DatabaseTarget, command: list[str]) -> str:
        del target_value
        commands.append(command)
        return "0"

    monkeypatch.setattr(restore, "capture", fake_capture)
    monkeypatch.setattr(restore, "migration_version", lambda target_value: "202607140005")
    monkeypatch.setattr(restore, "table_counts", lambda target_value: counts)
    monkeypatch.setattr(
        sys, "argv", ["restore", "--manifest", str(manifest), "--confirm-empty"]
    )
    assert restore.main() == 0
    assert any(command[0] == "pg_restore" for command in commands)

    monkeypatch.setattr(sys, "argv", ["restore", "--manifest", str(manifest)])
    assert restore.main() == 1


def test_invalid_database_url_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://placeholder")
    with pytest.raises(OpsError):
        DatabaseTarget.from_environment()


def test_migration_version_requires_one_nonempty_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = DatabaseTarget("localhost", 5432, "assistant", "assistant", "secret")
    monkeypatch.setattr("scripts.ops.db_common.capture", lambda *args: "202607140005")
    assert migration_version(target) == "202607140005"
    monkeypatch.setattr("scripts.ops.db_common.capture", lambda *args: "")
    with pytest.raises(OpsError, match="migration version"):
        migration_version(target)


def test_soak_runner_is_bounded_and_reports_safe_failures() -> None:
    current = 0.0
    samples = iter(
        (
            ProbeResult(True, 10.0),
            ProbeResult(False, 25.0, "http_503"),
            ProbeResult(True, 12.0),
        )
    )

    def monotonic() -> float:
        return current

    def sleeper(seconds: float) -> None:
        nonlocal current
        current += seconds

    report = run_soak(
        duration_seconds=10,
        interval_seconds=5,
        probe=lambda: next(samples),
        monotonic=monotonic,
        sleeper=sleeper,
    )
    assert report.status == "failed"
    assert report.checks == 3
    assert report.failures == 1
    assert report.max_latency_ms == 25.0
    assert report.error_codes == {"http_503": 1}
    with pytest.raises(ValueError):
        run_soak(duration_seconds=1, interval_seconds=1, probe=lambda: ProbeResult(True, 1))


def test_ops_image_pins_postgres_client_to_server_major() -> None:
    dockerfile = Path("Dockerfile.ops").read_text(encoding="utf-8")
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM postgres:16-bookworm\n")
    assert 'ENTRYPOINT ["python3", "-m"]' in dockerfile
    assert "image: postgres:16" in compose
