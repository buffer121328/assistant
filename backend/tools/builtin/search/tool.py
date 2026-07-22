from __future__ import annotations

from collections.abc import Iterable, Sequence
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from domain.policies.redaction import sanitize_text
from domain.models import ToolLog

from .chain import SearchProviderChain
from .clients import TavilyApiClient
from .constants import SEARCH_WEB_TOOL_NAME, TOOL_STATUS_FAILED, TOOL_STATUS_SUCCEEDED
from .errors import SearchProviderChainError, SearchWebToolError
from .utils import _truncate
from .providers import TavilySearchProvider
from .protocols import TavilyClientProtocol
from .types import (
    ProviderFailure,
    SearchProviderChainResult,
    SearchWebResult,
    TavilyConfig,
    TavilySearchRequest,
)


class SearchWebTool:
    """表示 搜索 web tool 的后端数据结构或服务对象。"""

    def __init__(
        self,
        *,
        session: AsyncSession,
        config: TavilyConfig,
        client: TavilyClientProtocol | None = None,
        provider_chain: SearchProviderChain | None = None,
        sensitive_values: Iterable[str | None] = (),
    ) -> None:
        """初始化对象实例。

        Args:
            session: session 参数。
            config: config 参数。
            client: client 参数。
            provider_chain: provider_chain 参数。
            sensitive_values: sensitive_values 参数。
        """
        self.session = session
        self.config = config
        self.sensitive_values = tuple(sensitive_values)
        if provider_chain is None:
            tavily_client = client or TavilyApiClient(config)
            provider_chain = SearchProviderChain(
                [
                    TavilySearchProvider(
                        client=tavily_client,
                        sensitive_values=self._extra_sensitive_values(),
                    )
                ],
                fallback_on_empty=config.fallback_on_empty,
                max_results=config.max_results,
                sensitive_values=self._extra_sensitive_values(),
            )
        self.provider_chain = provider_chain

    async def search(
        self,
        *,
        task_id: str,
        user_id: str,
        query: str,
    ) -> SearchWebResult:
        """搜索。

        Args:
            task_id: task_id 参数。
            user_id: user_id 参数。
            query: query 参数。
        """
        request = TavilySearchRequest(
            task_id=task_id,
            user_id=user_id,
            query=query,
            max_results=self.config.max_results,
        )
        input_text = self._request_summary(request)

        try:
            chain_result = await self.provider_chain.search(request)
            result = SearchWebResult(query=query, sources=chain_result.sources)
        except SearchProviderChainError as exc:
            safe_error = self._safe_error(exc)
            await self._record_log(
                task_id=task_id,
                status=TOOL_STATUS_FAILED,
                input_text=input_text,
                output_text=None,
                error_message=self._error_summary(
                    safe_error,
                    attempted_providers=exc.attempted_providers,
                    failures=exc.failures,
                ),
            )
            raise SearchWebToolError(f"search.web failed: {safe_error}") from exc
        except Exception as exc:
            safe_error = self._safe_error(exc)
            await self._record_log(
                task_id=task_id,
                status=TOOL_STATUS_FAILED,
                input_text=input_text,
                output_text=None,
                error_message=self._error_summary(safe_error),
            )
            raise SearchWebToolError(f"search.web failed: {safe_error}") from exc

        await self._record_log(
            task_id=task_id,
            status=TOOL_STATUS_SUCCEEDED,
            input_text=input_text,
            output_text=self._response_summary(result, chain_result),
            error_message=None,
        )
        return result

    async def _record_log(
        self,
        *,
        task_id: str,
        status: str,
        input_text: str,
        output_text: str | None,
        error_message: str | None,
    ) -> None:
        """执行 记录 log 的内部辅助逻辑。

        Args:
            task_id: task_id 参数。
            status: status 参数。
            input_text: input_text 参数。
            output_text: output_text 参数。
            error_message: error_message 参数。
        """
        self.session.add(
            ToolLog(
                task_id=task_id,
                tool_name=SEARCH_WEB_TOOL_NAME,
                status=status,
                input_text=input_text,
                output_text=output_text,
                error_message=error_message,
            )
        )
        await self.session.flush()

    def _request_summary(self, request: TavilySearchRequest) -> str:
        """执行 处理 request summary 的内部辅助逻辑。

        Args:
            request: request 参数。
        """
        return self._safe_json(
            {
                "tool": SEARCH_WEB_TOOL_NAME,
                "task_id": request.task_id,
                "user_id": request.user_id,
                "query": _truncate(request.query),
                "max_results": request.max_results,
            }
        )

    def _response_summary(
        self,
        result: SearchWebResult,
        chain_result: SearchProviderChainResult,
    ) -> str:
        """执行 处理 response summary 的内部辅助逻辑。

        Args:
            result: result 参数。
            chain_result: chain_result 参数。
        """
        return self._safe_json(
            {
                "status": TOOL_STATUS_SUCCEEDED,
                "provider_chain": list(chain_result.attempted_providers),
                "provider": chain_result.selected_provider,
                "fallback_reason": chain_result.fallback_reason,
                "provider_failures": [
                    failure.to_log_dict() for failure in chain_result.failures
                ],
                "source_count": len(result.sources),
                "sources": [
                    source.to_workflow_dict()
                    for source in result.sources[: self.config.max_results]
                ],
            }
        )

    def _error_summary(
        self,
        error: str,
        *,
        attempted_providers: Sequence[str] = (),
        failures: Sequence[ProviderFailure] = (),
    ) -> str:
        """执行 处理 error summary 的内部辅助逻辑。

        Args:
            error: error 参数。
            attempted_providers: attempted_providers 参数。
            failures: failures 参数。
        """
        return self._safe_json(
            {
                "status": TOOL_STATUS_FAILED,
                "provider_chain": list(attempted_providers),
                "provider_failures": [failure.to_log_dict() for failure in failures],
                "error": _truncate(error),
            }
        )

    def _safe_error(self, value: object) -> str:
        """执行 处理 safe error 的内部辅助逻辑。

        Args:
            value: value 参数。
        """
        text = sanitize_text(
            value,
            extra_sensitive_values=self._extra_sensitive_values(),
        )
        if "traceback" in text.lower():
            return "内部错误已脱敏"
        return text

    def _safe_json(self, payload: dict[str, Any]) -> str:
        """执行 处理 safe json 的内部辅助逻辑。

        Args:
            payload: payload 参数。
        """
        return sanitize_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ),
            extra_sensitive_values=self._extra_sensitive_values(),
        )

    def _extra_sensitive_values(self) -> tuple[str | None, ...]:
        """执行 处理 extra sensitive values 的内部辅助逻辑。"""
        return (
            self.config.api_key,
            self.config.brave_search_api_key,
            *self.sensitive_values,
        )
