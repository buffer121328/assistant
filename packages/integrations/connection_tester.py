from __future__ import annotations

from collections.abc import Mapping

from .browser_sessions import validate_browser_credentials
from .providers import CalDavProvider, ProviderError, SmtpProvider


class DefaultConnectionTester:
    def __init__(
        self,
        *,
        smtp: SmtpProvider | None = None,
        caldav: CalDavProvider | None = None,
    ) -> None:
        self.smtp = smtp or SmtpProvider()
        self.caldav = caldav or CalDavProvider()

    async def test(self, provider: str, credentials: Mapping[str, str]) -> None:
        if provider == "smtp":
            await self.smtp.check(credentials)
            return
        if provider == "caldav":
            await self.caldav.check(credentials)
            return
        if provider == "browser":
            validate_browser_credentials(dict(credentials))
            return
        raise ProviderError("connection_provider_invalid")
