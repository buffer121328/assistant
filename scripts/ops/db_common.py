from __future__ import annotations

from dataclasses import dataclass, field
import os
import subprocess
from urllib.parse import unquote, urlsplit


COUNTED_TABLES = (
    "users",
    "tasks",
    "account_connections",
    "knowledge_documents",
    "knowledge_chunks",
    "reminders",
    "notification_outbox",
    "delivery_attempts",
)


class OpsError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabaseTarget:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)

    @classmethod
    def from_environment(cls) -> DatabaseTarget:
        value = os.environ.get("DATABASE_URL", "").strip()
        parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.hostname
            or not parsed.path.strip("/")
            or not parsed.username
        ):
            raise OpsError("DATABASE_URL must reference a real PostgreSQL database")
        return cls(
            host=parsed.hostname,
            port=parsed.port or 5432,
            database=unquote(parsed.path.strip("/")),
            username=unquote(parsed.username),
            password=unquote(parsed.password or ""),
        )

    def command(self, executable: str) -> list[str]:
        return [
            executable,
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--username",
            self.username,
            "--dbname",
            self.database,
        ]

    def environment(self) -> dict[str, str]:
        values = dict(os.environ)
        values["PGPASSWORD"] = self.password
        return values


def capture(target: DatabaseTarget, command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            env=target.environment(),
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise OpsError(f"required executable is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise OpsError(f"database command failed: {command[0]}") from exc
    return result.stdout.strip()


def table_counts(target: DatabaseTarget) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in COUNTED_TABLES:
        output = capture(
            target,
            [*target.command("psql"), "--tuples-only", "--no-align", "--command", f'SELECT COUNT(*) FROM "{table}"'],
        )
        try:
            counts[table] = int(output)
        except ValueError as exc:
            raise OpsError(f"invalid count returned for table: {table}") from exc
    return counts


def migration_version(target: DatabaseTarget) -> str:
    version = capture(
        target,
        [
            *target.command("psql"),
            "--tuples-only",
            "--no-align",
            "--command",
            "SELECT version_num FROM alembic_version",
        ],
    )
    if not version or "\n" in version:
        raise OpsError("database migration version is invalid")
    return version
