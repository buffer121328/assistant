from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .db_common import DatabaseTarget, OpsError, capture, migration_version, table_counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a verified backup into an empty database")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--confirm-empty", action="store_true")
    args = parser.parse_args()
    try:
        if not args.confirm_empty:
            raise OpsError("--confirm-empty is required")
        target = DatabaseTarget.from_environment()
        manifest_path = args.manifest.resolve(strict=True)
        manifest = _manifest(manifest_path)
        backup = manifest_path.parent / str(manifest["backup_file"])
        if sha256(backup.read_bytes()).hexdigest() != manifest["sha256"]:
            raise OpsError("backup checksum mismatch")
        table_total = capture(
            target,
            [
                *target.command("psql"),
                "--tuples-only",
                "--no-align",
                "--command",
                "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname='public'",
            ],
        )
        if table_total != "0":
            raise OpsError("target database public schema is not empty")
        capture(
            target,
            [
                *target.command("pg_restore"),
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                str(backup),
            ],
        )
        actual_counts = table_counts(target)
        if actual_counts != manifest["table_counts"]:
            raise OpsError("restored table counts do not match manifest")
        actual_migration = migration_version(target)
        if actual_migration != manifest["migration_version"]:
            raise OpsError("restored migration version does not match manifest")
    except (OSError, KeyError, json.JSONDecodeError, OpsError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "status": "passed",
                "manifest": str(manifest_path),
                "migration_version": actual_migration,
                "table_counts": actual_counts,
            }
        )
    )
    return 0


def _manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("format") != "assistant-postgres-backup-v1":
        raise OpsError("backup manifest format is invalid")
    if not isinstance(value.get("table_counts"), dict):
        raise OpsError("backup manifest table counts are invalid")
    if not isinstance(value.get("migration_version"), str) or not value["migration_version"]:
        raise OpsError("backup manifest migration version is invalid")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
