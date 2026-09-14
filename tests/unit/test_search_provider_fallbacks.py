from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any
import json

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


SECRET = "secret-serper-token"
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
    serper = FakeProvider("serper", sources=[source("Serper", "https://example.com/b")], events=events)
    chain = SearchProviderChain(
        [tavily, serper],
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
async def test_search_provider_chain_falls_back_to_serper_after_tavily_timeout() -> None:
    events: list[str] = []
    tavily = FakeProvider(
        "tavily",
        error=TimeoutError(f"timeout Bearer {SECRET} {PRIVATE_URL}"),
        events=events,
    )
    serper = FakeProvider(
        "serper",
        sources=[source("Serper", "https://example.com/serper", provider="serper")],
        events=events,
    )
    chain = SearchProviderChain(
        [tavily, serper],
        fallback_on_empty=True,
        max_results=5,
        sensitive_values=[SECRET, PRIVATE_URL],
    )

    result = await chain.search(request())

    assert events == ["tavily", "serper"]
    assert result.selected_provider == "serper"
    assert result.fallback_reason == "provider_failed"
    assert result.failures[0].provider == "tavily"
    assert result.failures[0].category == "timeout"
    failure_log = result.failures[0].to_log_dict()["message"]
    assert SECRET not in failure_log
    assert PRIVATE_URL not in failure_log
    assert "Bearer " not in failure_log


def test_build_search_provider_chain_uses_serper_and_exa_when_keys_configured() -> None:
    config = TavilyConfig(
        api_key="",
        timeout_seconds=1,
        max_results=3,
        provider_order=("serper", "exa"),
        serper_api_key="serper-key",
        exa_api_key="exa-key",
    )

    chain = build_search_provider_chain(config)

    assert [provider.name for provider in chain.providers] == ["serper", "exa"]


def test_build_search_provider_chain_skips_unconfigured_serper_and_exa() -> None:
    config = TavilyConfig(
        api_key="",
        timeout_seconds=1,
        max_results=3,
        provider_order=("serper", "exa"),
    )

    chain = build_search_provider_chain(config)

    assert [provider.name for provider in chain.providers] == []

@pytest.mark.asyncio
async def test_search_provider_chain_fails_closed_without_traceback_or_secrets() -> None:
    chain = SearchProviderChain(
        [
            FakeProvider("tavily", error=RuntimeError(f"Traceback token={SECRET}")),
            FakeProvider("serper", error=RuntimeError(f"authorization {SECRET}")),
        ],
        fallback_on_empty=True,
        max_results=5,
        sensitive_values=[SECRET],
    )

    with pytest.raises(SearchProviderChainError) as exc_info:
        await chain.search(request())

    assert exc_info.value.attempted_providers == ("tavily", "serper")
    messages = [failure.to_log_dict()["message"] for failure in exc_info.value.failures]
    assert all(SECRET not in message for message in messages)
    assert all("traceback" not in message.lower() for message in messages)


@pytest.mark.asyncio
async def test_empty_results_fallback_deduplicates_urls_and_enforces_max_results() -> None:
    tavily = FakeProvider("tavily", sources=[])
    serper = FakeProvider(
        "serper",
        sources=[
            source("One", "https://example.com/item?utm_source=a", provider="serper"),
            source("One duplicate", "https://example.com/item?utm_source=b", provider="serper"),
            source("Two", "https://example.com/two", provider="serper"),
        ],
    )
    chain = SearchProviderChain(
        [tavily, serper],
        fallback_on_empty=True,
        max_results=1,
        sensitive_values=[SECRET],
    )

    result = await chain.search(request())

    assert result.selected_provider == "serper"
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
                api_key="fake-tavily-key",
                timeout_seconds=1,
                max_results=5,
            ),
            provider_chain=SearchProviderChain(
                [
                    FakeProvider("tavily", error=TimeoutError(f"timeout {SECRET}")),
                    FakeProvider("serper", sources=[source("Serper", "https://example.com/serper", provider="serper")]),
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

    assert result.sources[0].provider_metadata["provider"] == "serper"
    assert log.tool_name == "search.web"
    assert log.status == "succeeded"
    assert log.output_text is not None
    assert '"provider":"serper"' in log.output_text
    assert '"provider_chain":["tavily","serper"]' in log.output_text
    assert SECRET not in log.output_text


def test_parse_search_provider_order_ignores_unknown_and_duplicates() -> None:
    assert parse_search_provider_order("serper,tavily,unknown,serper") == (
        "serper",
        "tavily",
    )


@pytest.mark.asyncio
async def test_serper_provider_posts_official_search_shape_and_normalizes() -> None:
    import httpx
    import respx

    from tools import SerperSearchProvider

    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://google.serper.dev/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "organic": [
                        {
                            "title": "Serper Result",
                            "link": "https://example.com/serper",
                            "snippet": "Serper snippet",
                            "position": 1,
                        }
                    ]
                },
            )
        )
        provider = SerperSearchProvider(
            api_key="serper-secret",
            base_url="https://google.serper.dev/search",
            timeout_seconds=1,
            sensitive_values=["serper-secret"],
        )

        sources = await provider.search(request())

    sent = route.calls.last.request
    assert sent.headers["X-API-KEY"] == "serper-secret"
    assert json.loads(sent.content) == {"q": "python agent", "num": 2}
    assert sources[0].provider_metadata["provider"] == "serper"
    assert sources[0].url == "https://example.com/serper"


@pytest.mark.asyncio
async def test_exa_provider_posts_official_search_shape_and_normalizes() -> None:
    import httpx
    import respx

    from tools import ExaSearchProvider

    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://api.exa.ai/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Exa Result",
                            "url": "https://example.com/exa",
                            "text": "Exa snippet",
                            "score": 0.9,
                        }
                    ]
                },
            )
        )
        provider = ExaSearchProvider(
            api_key="exa-secret",
            base_url="https://api.exa.ai/search",
            timeout_seconds=1,
            sensitive_values=["exa-secret"],
        )

        sources = await provider.search(request())

    sent = route.calls.last.request
    assert sent.headers["x-api-key"] == "exa-secret"
    assert json.loads(sent.content) == {"query": "python agent", "numResults": 2}
    assert sources[0].provider_metadata["provider"] == "exa"
    assert sources[0].url == "https://example.com/exa"
