from __future__ import annotations

from typing import Any, Protocol

from .types import NormalizedSearchSource, TavilySearchRequest


class TavilyClientProtocol(Protocol):
    """表示 处理 tavily client protocol 的后端数据结构或服务对象。"""

    async def search(self, request: TavilySearchRequest) -> dict[str, Any]:
        """搜索。

        Args:
            request: request 参数。
        """
        pass


class SearchProvider(Protocol):
    """表示 搜索 provider 的后端数据结构或服务对象。"""

    name: str

    async def search(
        self, request: TavilySearchRequest
    ) -> list[NormalizedSearchSource]:
        """搜索。

        Args:
            request: request 参数。
        """
        pass
