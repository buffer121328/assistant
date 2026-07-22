from __future__ import annotations

from collections.abc import Mapping

from .browser_sessions import validate_browser_credentials
from .providers import CalDavProvider, ProviderError, SmtpProvider


class DefaultConnectionTester:
    """表示 处理 default connection tester 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        smtp: SmtpProvider | None = None,
        caldav: CalDavProvider | None = None,
    ) -> None:
        """初始化对象实例。

        Args:
            smtp: smtp 参数。
            caldav: caldav 参数。
        """
        self.smtp = smtp or SmtpProvider()
        self.caldav = caldav or CalDavProvider()

    async def test(self, provider: str, credentials: Mapping[str, str]) -> None:
        """测试。

        Args:
            provider: provider 参数。
            credentials: credentials 参数。
        """
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
