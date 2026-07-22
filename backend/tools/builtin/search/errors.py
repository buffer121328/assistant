from __future__ import annotations

from collections.abc import Sequence

from .types import ProviderFailure


class TavilyClientError(Exception):
    """表示 处理 tavily client error 的后端数据结构或服务对象。"""

    pass


class SearchProviderError(Exception):
    """表示 搜索 provider error 的后端数据结构或服务对象。"""

    pass


class SearchProviderChainError(Exception):
    """表示 搜索 provider chain error 的后端数据结构或服务对象。"""

    def __init__(
        self,
        message: str,
        *,
        attempted_providers: Sequence[str],
        failures: Sequence[ProviderFailure],
    ) -> None:
        """初始化对象实例。

        Args:
            message: message 参数。
            attempted_providers: attempted_providers 参数。
            failures: failures 参数。
        """
        super().__init__(message)
        self.attempted_providers = tuple(attempted_providers)
        self.failures = tuple(failures)


class SearchWebToolError(Exception):
    """表示 搜索 web tool error 的后端数据结构或服务对象。"""

    pass
