from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tools import (
    NormalizedSearchSource,
    SearchProviderChain,
    SearchProviderChainError,
    SearchWebTool,
    TavilyConfig,
    TavilySearchRequest,
    build_search_provider_chain,
    parse_search_provider_order,
)
from domain.models import Base, Task, TaskStatus, ToolLog, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


SECRET = "secret-brave-token"
PRIVATE_URL = "https://private.example.invalid/search"


class FakeProvider:
    def __init__(
        self,
        name: str,
        *,
        sources: Sequence[NormalizedSearchSource] = (),
        error: Exception | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.name = name
        self.sources = list(sources)
        self.error = error
        self.events = events
        self.calls: list[TavilySearchRequest] = []

    async def search(self, request: TavilySearchRequest) -> list[NormalizedSearchSource]:
        self.calls.append(request)
        if self.events is not None:
            self.events.append(self.name)
        if self.error is not None:
            raise self.error
        return self.sources


class FakeTavilyClient:
    def __init__(self) -> None:
        self.calls: list[TavilySearchRequest] = []

    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
        self.calls.append(request)
        return {"results": []}


def source(
    title: str,
    url: str,
    *,
    provider: str = "tavily",
) -> NormalizedSearchSource:
    return NormalizedSearchSource(
        title=title,
        url=url,
        snippet=f"{title} snippet",
        provider_metadata={"provider": provider},
    )


def request() -> TavilySearchRequest:
    return TavilySearchRequest(
        task_id="task-1",
        user_id="user-1",
        query="python agent",
        max_results=2,
    )


@pytest.mark.asyncio
async def test_search_provider_chain_uses_tavily_first_when_it_returns_results() -> None:
    events: list[str] = []
    tavily = FakeProvider("tavily", sources=[source("Tavily", "https://example.com/a")], events=events)
    brave = FakeProvider("brave", sources=[source("Brave", "https://example.com/b")], events=events)
    chain = SearchProviderChain(
        [tavily, brave],
        fallback_on_empty=True,
        max_results=5,
        sensitive_values=[SECRET],
    )

    result = await chain.search(request())

    assert events == ["tavily"]
    assert result.selected_provider == "tavily"
    assert result.attempted_providers == ("tavily",)
    assert [item.title for item in result.sources] == ["Tavily"]
    assert result.failures == ()


@pytest.mark.asyncio
async def test_search_provider_chain_falls_back_to_brave_after_tavily_timeout() -> None:
    events: list[str] = []
    tavily = FakeProvider(
        "tavily",
        error=TimeoutError(f"timeout Bearer {SECRET} {PRIVATE_URL}"),
        events=events,
    )
    brave = FakeProvider(
        "brave",
        sources=[source("Brave", "https://example.com/brave", provider="brave")],
        events=events,
    )
    chain = SearchProviderChain(
        [tavily, brave],
        fallback_on_empty=True,
        max_results=5,
        sensitive_values=[SECRET, PRIVATE_URL],
    )

    result = await chain.search(request())

    assert events == ["tavily", "brave"]
    assert result.selected_provider == "brave"
    assert result.fallback_reason == "provider_failed"
    assert result.failures[0].provider == "tavily"
    assert result.failures[0].category == "timeout"
    failure_log = result.failures[0].to_log_dict()["message"]
    assert SECRET not in failure_log
    assert PRIVATE_URL not in failure_log
    assert "Bearer " not in failure_log


def test_build_search_provider_chain_uses_duckduckgo_only_when_enabled() -> None:
    disabled_config = TavilyConfig(
        base_url="",
        api_key="",
        timeout_seconds=1,
        max_results=3,
        provider_order=("duckduckgo",),
        duckduckgo_search_enabled=False,
    )
    disabled_chain = build_search_provider_chain(disabled_config)

    enabled_config = TavilyConfig(
        base_url="",
        api_key="",
        timeout_seconds=1,
        max_results=3,
        provider_order=("duckduckgo",),
        duckduckgo_search_enabled=True,
    )
    enabled_chain = build_search_provider_chain(enabled_config)

    assert [provider.name for provider in disabled_chain.providers] == []
    assert [provider.name for provider in enabled_chain.providers] == ["duckduckgo"]


@pytest.mark.asyncio
async def test_search_provider_chain_fails_closed_without_traceback_or_secrets() -> None:
    chain = SearchProviderChain(
        [
            FakeProvider("tavily", error=RuntimeError(f"Traceback token={SECRET}")),
            FakeProvider("brave", error=RuntimeError(f"authorization {SECRET}")),
        ],
        fallback_on_empty=True,
        max_results=5,
        sensitive_values=[SECRET],
    )

    with pytest.raises(SearchProviderChainError) as exc_info:
        await chain.search(request())

    assert exc_info.value.attempted_providers == ("tavily", "brave")
    messages = [failure.to_log_dict()["message"] for failure in exc_info.value.failures]
    assert all(SECRET not in message for message in messages)
    assert all("traceback" not in message.lower() for message in messages)


@pytest.mark.asyncio
async def test_empty_results_fallback_deduplicates_urls_and_enforces_max_results() -> None:
    tavily = FakeProvider("tavily", sources=[])
    brave = FakeProvider(
        "brave",
        sources=[
            source("One", "https://example.com/item?utm_source=a", provider="brave"),
            source("One duplicate", "https://example.com/item?utm_source=b", provider="brave"),
            source("Two", "https://example.com/two", provider="brave"),
        ],
    )
    chain = SearchProviderChain(
        [tavily, brave],
        fallback_on_empty=True,
        max_results=1,
        sensitive_values=[SECRET],
    )

    result = await chain.search(request())

    assert result.selected_provider == "brave"
    assert result.fallback_reason == "empty_results"
    assert result.failures[0].category == "empty_results"
    assert [item.url for item in result.sources] == ["https://example.com/item?utm_source=a"]


@pytest.mark.asyncio
async def test_search_web_tool_log_records_provider_chain_metadata(tmp_path: Path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/search-provider-chain.db",
        poolclass=NullPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        user = User(display_name="Search User")
        session.add(user)
        await session.flush()
        task = Task(
            user_id=user.id,
            platform="api",
            task_type="learn",
            input_text="/learn provider chain",
            status=TaskStatus.PENDING.value,
        )
        session.add(task)
        await session.flush()

        tool = SearchWebTool(
            session=session,
            config=TavilyConfig(
                base_url="https://tavily.invalid",
                api_key="fake-tavily-key",
                timeout_seconds=1,
                max_results=5,
            ),
            provider_chain=SearchProviderChain(
                [
                    FakeProvider("tavily", error=TimeoutError(f"timeout {SECRET}")),
                    FakeProvider("brave", sources=[source("Brave", "https://example.com/brave", provider="brave")]),
                ],
                fallback_on_empty=True,
                max_results=5,
                sensitive_values=[SECRET],
            ),
            sensitive_values=[SECRET],
        )

        result = await tool.search(task_id=task.id, user_id=user.id, query="provider chain")
        await session.commit()

    async with sessionmaker() as session:
        log = (await session.scalars(select(ToolLog))).one()

    await engine.dispose()

    assert result.sources[0].provider_metadata["provider"] == "brave"
    assert log.tool_name == "search.web"
    assert log.status == "succeeded"
    assert log.output_text is not None
    assert '"provider":"brave"' in log.output_text
    assert '"provider_chain":["tavily","brave"]' in log.output_text
    assert SECRET not in log.output_text


def test_parse_search_provider_order_ignores_unknown_and_duplicates() -> None:
    assert parse_search_provider_order("brave,tavily,unknown,brave") == (
        "brave",
        "tavily",
    )
