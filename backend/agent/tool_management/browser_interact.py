from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from integrations import BrowserSession

from .browser import BrowserDestinationError, PublicUrlPolicy
from .catalog import ToolDescriptor
from .registry import ToolInvocation, ToolSpec


MAX_ACTIONS = 10
ALLOWED_ROLES = frozenset({"button", "link", "checkbox", "radio", "menuitem", "tab"})


class BrowserSessionStore(Protocol):
    async def load(self, *, user_id: str, connection_id: str) -> BrowserSession: ...
    async def save(
        self, *, user_id: str, connection_id: str, storage_state: dict[str, Any]
    ) -> None: ...


@dataclass(frozen=True)
class BrowserInteractionResult:
    title: str
    text: str
    final_url: str


class BrowserRuntime(Protocol):
    async def execute(
        self,
        *,
        session: BrowserSession,
        url: str,
        actions: tuple[dict[str, str], ...],
        policy: PublicUrlPolicy,
        timeout_seconds: float,
        max_text_chars: int,
    ) -> tuple[BrowserInteractionResult, dict[str, Any]]: ...


class BrowserInteractor:
    def __init__(
        self,
        *,
        sessions: BrowserSessionStore,
        runtime: BrowserRuntime | None = None,
        policy: PublicUrlPolicy | None = None,
        timeout_seconds: float = 20.0,
        max_text_chars: int = 20_000,
    ) -> None:
        self.sessions = sessions
        self.runtime = runtime or PlaywrightInteractionRuntime()
        self.policy = policy or PublicUrlPolicy()
        self.timeout_seconds = max(1.0, min(timeout_seconds, 60.0))
        self.max_text_chars = max(1_000, min(max_text_chars, 50_000))

    async def run(
        self,
        *,
        user_id: str,
        connection_id: str,
        url: str,
        actions: list[dict[str, Any]],
        save_state: bool,
    ) -> BrowserInteractionResult:
        session = await self.sessions.load(user_id=user_id, connection_id=connection_id)
        safe_url = await self.policy.validate(url)
        _validate_domain(safe_url, session.allowed_domains)
        normalized = _actions(actions)
        for action in normalized:
            if action["type"] == "navigate":
                action["url"] = await self.policy.validate(action["url"])
                _validate_domain(action["url"], session.allowed_domains)
        result, state = await self.runtime.execute(
            session=session,
            url=safe_url,
            actions=normalized,
            policy=self.policy,
            timeout_seconds=self.timeout_seconds,
            max_text_chars=self.max_text_chars,
        )
        final_url = await self.policy.validate(result.final_url)
        _validate_domain(final_url, session.allowed_domains)
        if save_state:
            await self.sessions.save(
                user_id=user_id,
                connection_id=connection_id,
                storage_state=state,
            )
        return BrowserInteractionResult(
            title=result.title[:500],
            text=result.text[: self.max_text_chars],
            final_url=final_url,
        )


class PlaywrightInteractionRuntime:
    async def execute(
        self,
        *,
        session: BrowserSession,
        url: str,
        actions: tuple[dict[str, str], ...],
        policy: PublicUrlPolicy,
        timeout_seconds: float,
        max_text_chars: int,
    ) -> tuple[BrowserInteractionResult, dict[str, Any]]:
        from playwright.async_api import StorageState, async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    storage_state=cast(StorageState, session.storage_state),
                    accept_downloads=False,
                    service_workers="block",
                )
                try:
                    async def guard_route(route: Any) -> None:
                        request_url = str(route.request.url)
                        try:
                            await policy.validate(request_url)
                            if route.request.is_navigation_request():
                                _validate_domain(request_url, session.allowed_domains)
                        except BrowserDestinationError:
                            await route.abort("blockedbyclient")
                            return
                        await route.continue_()

                    await context.route("**/*", guard_route)
                    page = await context.new_page()
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=int(timeout_seconds * 1_000),
                    )
                    for action in actions:
                        await _execute_action(page, action, timeout_seconds)
                    return (
                        BrowserInteractionResult(
                            title=await page.title(),
                            text=(await page.locator("body").inner_text())[:max_text_chars],
                            final_url=page.url,
                        ),
                        cast(dict[str, Any], await context.storage_state()),
                    )
                finally:
                    await context.close()
            finally:
                await browser.close()


def build_browser_tool_descriptors(*, enabled: bool) -> tuple[ToolDescriptor, ...]:
    return tuple(
        ToolDescriptor(
            name=name,
            description=description,
            input_schema=_schema(),
            source_id="builtin",
            source_kind="builtin",
            version="browser-interact-v1",
            enabled=enabled,
            risk_level="L3",
            requires_approval=True,
            tags=("daily", "office"),
        )
        for name, description in (
            ("browser.interact", "Run approved structured browser actions without saving state"),
            ("browser.save_state", "Run approved structured browser actions and save encrypted state"),
        )
    )


def build_browser_tool_specs(interactor: BrowserInteractor) -> tuple[ToolSpec, ...]:
    def spec(name: str, *, save_state: bool) -> ToolSpec:
        async def handler(invocation: ToolInvocation) -> dict[str, str]:
            args = invocation.arguments
            result = await interactor.run(
                user_id=invocation.user_id,
                connection_id=str(args["connection_id"]),
                url=str(args["url"]),
                actions=list(args.get("actions", [])),
                save_state=save_state,
            )
            return asdict(result)

        return ToolSpec(
            name=name,
            description="Approved structured browser interaction",
            risk_level="L3",
            handler=handler,
            input_schema=_schema(),
            version="browser-interact-v1",
        )

    return (
        spec("browser.interact", save_state=False),
        spec("browser.save_state", save_state=True),
    )


def _actions(values: list[dict[str, Any]]) -> tuple[dict[str, str], ...]:
    if len(values) > MAX_ACTIONS:
        raise BrowserDestinationError("Too many browser actions")
    normalized: list[dict[str, str]] = []
    allowed_keys = {
        "navigate": {"type", "url"},
        "click_role": {"type", "role", "name"},
        "click_text": {"type", "text"},
        "fill": {"type", "field", "value"},
        "submit": {"type", "name"},
    }
    for value in values:
        kind = value.get("type")
        if kind not in allowed_keys or set(value) != allowed_keys[kind]:
            raise BrowserDestinationError("Unsupported browser action")
        item = {key: str(raw) for key, raw in value.items()}
        if any(not raw or len(raw) > 2_000 for key, raw in item.items() if key != "type"):
            raise BrowserDestinationError("Browser action value is invalid")
        if kind == "click_role" and item["role"] not in ALLOWED_ROLES:
            raise BrowserDestinationError("Browser role is not allowed")
        normalized.append(item)
    return tuple(normalized)


def _validate_domain(url: str, allowed_domains: tuple[str, ...]) -> None:
    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    if hostname not in allowed_domains:
        raise BrowserDestinationError("Destination domain is outside the connection allowlist")


async def _execute_action(page: Any, action: dict[str, str], timeout_seconds: float) -> None:
    timeout = int(timeout_seconds * 1_000)
    kind = action["type"]
    if kind == "navigate":
        await page.goto(action["url"], wait_until="domcontentloaded", timeout=timeout)
    elif kind == "click_role":
        await page.get_by_role(action["role"], name=action["name"], exact=True).click(timeout=timeout)
    elif kind == "click_text":
        await page.get_by_text(action["text"], exact=True).click(timeout=timeout)
    elif kind == "fill":
        await page.get_by_label(action["field"], exact=True).fill(action["value"], timeout=timeout)
    else:
        await page.get_by_role("button", name=action["name"], exact=True).click(timeout=timeout)


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "connection_id": {"type": "string", "minLength": 1, "maxLength": 36},
            "url": {"type": "string", "minLength": 1, "maxLength": 2048},
            "actions": {"type": "array", "maxItems": MAX_ACTIONS, "items": {"type": "object"}},
        },
        "required": ["connection_id", "url", "actions"],
        "additionalProperties": False,
    }
