from __future__ import annotations

import asyncio
from dataclasses import dataclass
import importlib
import json
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class SemanticMemoryResult:
    memory_id: str
    content: str
    score: float | None = None


class SemanticMemory(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def add(
        self,
        *,
        user_id: str,
        run_id: str,
        memory_id: str,
        content: str,
    ) -> bool: ...

    async def delete(self, *, user_id: str, memory_id: str) -> bool: ...

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
    ) -> tuple[SemanticMemoryResult, ...]: ...


class NoopSemanticMemory:
    enabled = False

    async def add(
        self, *, user_id: str, run_id: str, memory_id: str, content: str
    ) -> bool:
        del user_id, run_id, memory_id, content
        return False

    async def delete(self, *, user_id: str, memory_id: str) -> bool:
        del user_id, memory_id
        return False

    async def search(
        self, *, user_id: str, query: str, limit: int
    ) -> tuple[SemanticMemoryResult, ...]:
        del user_id, query, limit
        return ()


class Mem0MemoryAdapter:
    def __init__(self, config_path: Path | None) -> None:
        self.config_path = config_path.expanduser().resolve() if config_path else None
        self._client: Any | None = None
        self._load_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.config_path is not None

    async def add(
        self, *, user_id: str, run_id: str, memory_id: str, content: str
    ) -> bool:
        try:
            client = await self._client_instance()
            await asyncio.to_thread(
                client.add,
                [{"role": "user", "content": content[:20_000]}],
                user_id=user_id,
                run_id=run_id,
                metadata={"sql_memory_id": memory_id},
            )
        except Exception:
            return False
        return True

    async def delete(self, *, user_id: str, memory_id: str) -> bool:
        try:
            client = await self._client_instance()
            payload = await asyncio.to_thread(client.get_all, user_id=user_id)
            for item in _result_items(payload):
                metadata = item.get("metadata")
                if (
                    isinstance(metadata, dict)
                    and metadata.get("sql_memory_id") == memory_id
                ):
                    mem0_id = item.get("id")
                    if isinstance(mem0_id, str):
                        await asyncio.to_thread(client.delete, mem0_id)
        except Exception:
            return False
        return True

    async def search(
        self, *, user_id: str, query: str, limit: int
    ) -> tuple[SemanticMemoryResult, ...]:
        client = await self._client_instance()
        bounded_limit = max(1, min(limit, 20))
        payload = await asyncio.to_thread(
            client.search,
            query[:4_000],
            user_id=user_id,
            limit=bounded_limit,
        )
        results: list[SemanticMemoryResult] = []
        for item in _result_items(payload)[:bounded_limit]:
            content = item.get("memory") or item.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            metadata = item.get("metadata")
            sql_memory_id = (
                metadata.get("sql_memory_id") if isinstance(metadata, dict) else None
            )
            raw_id = (
                sql_memory_id or item.get("memory_id") or item.get("id") or "unknown"
            )
            score = item.get("score")
            results.append(
                SemanticMemoryResult(
                    memory_id=str(raw_id)[:128],
                    content=content.strip()[:10_000],
                    score=(float(score) if isinstance(score, int | float) else None),
                )
            )
        return tuple(results)

    async def _client_instance(self) -> Any:
        if self.config_path is None:
            raise RuntimeError("Mem0 is not configured")
        if self._client is not None:
            return self._client
        async with self._load_lock:
            if self._client is not None:
                return self._client
            config = await asyncio.to_thread(_load_config, self.config_path)
            module = importlib.import_module("mem0")
            factory = getattr(module, "Memory")
            self._client = await asyncio.to_thread(factory.from_config, config)
            return self._client


def _load_config(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".json" or not path.is_file():
        raise ValueError("Mem0 config must be an existing JSON file")
    if path.stat().st_size > 64 * 1024:
        raise ValueError("Mem0 config is too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Mem0 config must be an object")
    return payload


def _result_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("results", value.get("memories", []))
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
