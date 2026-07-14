from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy.engine import make_url


class AgentCheckpointConfigurationError(ValueError):
    pass


def build_checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=None)


def normalize_checkpoint_database_url(database_url: str) -> str:
    try:
        url = make_url(database_url)
    except Exception as exc:
        raise AgentCheckpointConfigurationError(
            "Agent checkpoint database URL is invalid"
        ) from exc
    if url.get_backend_name() != "postgresql":
        raise AgentCheckpointConfigurationError(
            "Agent checkpoint persistence requires PostgreSQL"
        )
    return url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )


@asynccontextmanager
async def open_agent_checkpointer(
    database_url: str,
    *,
    saver_factory: Callable[..., Any] | None = None,
    serializer: SerializerProtocol | None = None,
) -> AsyncIterator[Any]:
    connection_string = normalize_checkpoint_database_url(database_url)
    factory = saver_factory or AsyncPostgresSaver.from_conn_string
    serde = serializer or build_checkpoint_serializer()
    async with factory(connection_string, serde=serde) as saver:
        await saver.setup()
        yield saver
