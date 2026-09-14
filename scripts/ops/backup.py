from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path

from .db_common import DatabaseTarget, OpsError, capture, migration_version, table_counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a verified PostgreSQL backup")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        target = DatabaseTarget.from_environment()
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = output_dir / f"assistant-{stamp}.dump"
        temporary = backup.with_suffix(".dump.tmp")
        capture(
            target,
            [
                *target.command("pg_dump"),
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                f"--file={temporary}",
            ],
        )
        temporary.replace(backup)
        digest = sha256(backup.read_bytes()).hexdigest()
        manifest = {
            "format": "assistant-postgres-backup-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "backup_file": backup.name,
            "sha256": digest,
            "migration_version": migration_version(target),
            "table_counts": table_counts(target),
        }
        manifest_path = backup.with_suffix(".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, OpsError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "status": "passed",
                "backup": str(backup),
                "manifest": str(manifest_path),
                "sha256": digest,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
