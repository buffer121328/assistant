from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import ipaddress
from typing import Any
from urllib.parse import urlsplit, urlunsplit


class BrowserDestinationError(ValueError):
    pass


Resolver = Callable[[str], Awaitable[tuple[str, ...]]]


class PublicUrlPolicy:
    def __init__(self, *, resolver: Resolver | None = None) -> None:
        self._resolver = resolver or self._resolve

    async def validate(self, value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise BrowserDestinationError("Only public HTTP(S) URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise BrowserDestinationError("URL credentials are not allowed")
        if parsed.port is not None and parsed.port not in {80, 443}:
            raise BrowserDestinationError("Non-standard ports are not allowed")
        addresses = await self._resolver(parsed.hostname)
        if not addresses:
            raise BrowserDestinationError("Destination did not resolve")
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise BrowserDestinationError("Destination returned an invalid address") from exc
            if not ip.is_global:
                raise BrowserDestinationError("Private or reserved destinations are blocked")
        return urlunsplit(parsed)

    @staticmethod
    async def _resolve(host: str) -> tuple[str, ...]:
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(host, None, type=0)
        return tuple(dict.fromkeys(str(record[4][0]) for record in records))


@dataclass(frozen=True)
class BrowserReadResult:
    title: str
    text: str
    final_url: str


class PlaywrightBrowserReader:
    def __init__(
        self,
        *,
        policy: PublicUrlPolicy | None = None,
        timeout_seconds: float = 20.0,
        max_text_chars: int = 50_000,
    ) -> None:
        self.policy = policy or PublicUrlPolicy()
        self.timeout_seconds = max(1.0, min(timeout_seconds, 60.0))
        self.max_text_chars = max(1_000, min(max_text_chars, 100_000))

    async def read(self, url: str) -> BrowserReadResult:
        safe_url = await self.policy.validate(url)
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                )
                try:
                    async def guard_route(route: Any) -> None:
                        request = getattr(route, "request")
                        request_url = str(getattr(request, "url"))
                        try:
                            await self.policy.validate(request_url)
                        except BrowserDestinationError:
                            await getattr(route, "abort")("blockedbyclient")
                            return
                        await getattr(route, "continue_")()

                    await context.route("**/*", guard_route)
                    page = await context.new_page()
                    response = await page.goto(
                        safe_url,
                        wait_until="domcontentloaded",
                        timeout=int(self.timeout_seconds * 1_000),
                    )
                    if response is None:
                        raise RuntimeError("Browser navigation returned no response")
                    final_url = await self.policy.validate(page.url)
                    title = (await page.title())[:500]
                    text = (await page.locator("body").inner_text())[: self.max_text_chars]
                    return BrowserReadResult(title=title, text=text, final_url=final_url)
                finally:
                    await context.close()
            finally:
                await browser.close()
