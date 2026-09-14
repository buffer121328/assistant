from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
import re
from typing import Any, Literal, Protocol

from domain.policies.redaction import sanitize_text


ContentGuardOutcome = Literal["allow", "sanitize", "block"]
ContentGuardScope = Literal["untrusted_tool_result", "final_result"]

_INSTRUCTION_LIKE_PATTERNS = (
    re.compile(
        r"(?i)\b(ignore|disregard|override)\b.{0,80}\b(previous|prior|system|developer|instructions?)\b"
    ),
    re.compile(
        r"(?i)\b(system prompt|developer message|tool call|browser\.interact)\b"
    ),
    re.compile(r"忽略.{0,24}(之前|前面|系统|开发者).{0,24}(指令|提示|消息)"),
    re.compile(r"(系统提示词|开发者消息|调用工具|浏览器交互)"),
)
_URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]{}]+", re.IGNORECASE)
_ABSTENTION_MARKERS = (
    "无法从提供的资料中确认",
    "没有足够资料",
    "未找到相关资料",
    "没有找到可用搜索结果",
    "cannot determine from the provided sources",
    "insufficient evidence",
)


class ContentGuardError(RuntimeError):
    """定义当前组件可安全处理的错误类型。"""


class ContentGuardBlockedError(ContentGuardError):
    """定义当前组件可安全处理的错误类型。"""

    def __init__(self, decision: ContentGuardDecision) -> None:
        """保存治理决策，并用规则编码构造可安全记录的异常消息。

        Args:
            decision: 当前操作的治理决策。
        """
        self.decision = decision
        super().__init__(f"Content governance blocked output: {decision.rule_code}")


class ContentGuardProviderUnavailableError(ContentGuardError):
    """定义当前组件可安全处理的错误类型。"""


@dataclass(frozen=True)
class ContentSource:
    """定义当前组件的职责和边界。"""

    title: str  # title 对应的数据字段。
    url: str  # url 对应的数据字段。
    snippet: str  # snippet 对应的数据字段。
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)  # provider_metadata 对应的数据字段。

    @classmethod
    def from_workflow_dict(cls, value: Mapping[str, Any]) -> ContentSource:
        """从工作流持久化字典恢复规范化的外部内容来源。

        Args:
            value: 待校验、归一化或转换的输入值。
        """
        return cls(
            title=str(value.get("title") or ""),
            url=str(value.get("url") or ""),
            snippet=str(value.get("snippet") or ""),
            provider_metadata=(
                dict(value.get("provider_metadata", {}))
                if isinstance(value.get("provider_metadata"), Mapping)
                else {}
            ),
        )

    def to_workflow_dict(self) -> dict[str, Any]:
        """将内容来源转换为可写入工作流状态的普通字典。"""
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider_metadata": dict(self.provider_metadata),
        }

    def fingerprint_material(self) -> str:
        """返回用于内容指纹计算的稳定字段拼接结果。"""
        return "\n".join((self.title, self.url, self.snippet))


@dataclass(frozen=True)
class ContentGuardRequest:
    """定义当前接口使用的数据模型。"""

    scope: ContentGuardScope  # scope 对应的数据字段。
    task_id: str  # task_id 对应的数据字段。
    user_id: str  # user_id 对应的数据字段。
    task_type: str  # task_type 对应的数据字段。
    tool_name: str | None = None  # tool_name 对应的数据字段。
    source: ContentSource | None = None  # source 对应的数据字段。
    text: str | None = None  # text 对应的数据字段。
    safe_source_urls: tuple[str, ...] = ()  # safe_source_urls 对应的数据字段。

    @classmethod
    def for_source(
        cls,
        *,
        task_id: str,
        user_id: str,
        task_type: str,
        tool_name: str,
        source: ContentSource,
    ) -> ContentGuardRequest:
        """构造针对工具返回外部来源的内容治理请求。

        Args:
            task_id: 目标任务 ID。
            user_id: 目标用户 ID。
            task_type: 用于执行当前操作的 task type 参数。
            tool_name: 用于执行当前操作的 tool name 参数。
            source: 用于执行当前操作的 source 参数。
        """
        return cls(
            scope="untrusted_tool_result",
            task_id=task_id,
            user_id=user_id,
            task_type=task_type,
            tool_name=tool_name,
            source=source,
        )

    @classmethod
    def for_final_result(
        cls,
        *,
        task_id: str,
        user_id: str,
        task_type: str,
        text: str,
        safe_source_urls: tuple[str, ...],
    ) -> ContentGuardRequest:
        """构造针对最终输出文本及其安全来源的治理请求。

        Args:
            task_id: 目标任务 ID。
            user_id: 目标用户 ID。
            task_type: 用于执行当前操作的 task type 参数。
            text: 需要处理的文本内容。
            safe_source_urls: 用于执行当前操作的 safe source urls 参数。
        """
        return cls(
            scope="final_result",
            task_id=task_id,
            user_id=user_id,
            task_type=task_type,
            text=text,
            safe_source_urls=safe_source_urls,
        )


@dataclass(frozen=True)
class ContentGuardDecision:
    """定义当前组件的职责和边界。"""

    outcome: ContentGuardOutcome  # outcome 对应的数据字段。
    rule_code: str  # rule_code 对应的数据字段。
    audit_summary: str  # audit_summary 对应的数据字段。
    source: ContentSource | None = None  # source 对应的数据字段。
    text: str | None = None  # text 对应的数据字段。
    content_fingerprint: str | None = None  # content_fingerprint 对应的数据字段。

    @property
    def blocked(self) -> bool:
        """判断本次治理决策是否阻止内容继续发布。"""
        return self.outcome == "block"


class ContentGuard(Protocol):
    """定义当前组件的接口契约。"""

    enabled: bool

    async def inspect_untrusted_source(
        self, request: ContentGuardRequest
    ) -> ContentGuardDecision:
        """Inspect a normalized source before it enters model context.

        Args:
            request: 当前操作的结构化请求。
        """
        ...

    async def inspect_final_result(
        self, request: ContentGuardRequest
    ) -> ContentGuardDecision:
        """Inspect the complete final result before it is published.

        Args:
            request: 当前操作的结构化请求。
        """
        ...


class NoopContentGuard:
    """定义当前组件的职责和边界。"""

    enabled = False

    async def inspect_untrusted_source(
        self, request: ContentGuardRequest
    ) -> ContentGuardDecision:
        """在治理关闭时原样放行外部来源，并保留内容指纹。

        Args:
            request: 当前操作的结构化请求。
        """
        source = request.source
        if source is None:
            raise ContentGuardError("Untrusted source request requires a source")
        return ContentGuardDecision(
            outcome="allow",
            rule_code="content_governance_disabled",
            audit_summary="Content governance is disabled for this source.",
            source=source,
            content_fingerprint=_fingerprint(source.fingerprint_material()),
        )

    async def inspect_final_result(
        self, request: ContentGuardRequest
    ) -> ContentGuardDecision:
        """在治理关闭时原样放行最终输出，并保留内容指纹。

        Args:
            request: 当前操作的结构化请求。
        """
        text = request.text
        if text is None:
            raise ContentGuardError("Final result request requires text")
        return ContentGuardDecision(
            outcome="allow",
            rule_code="content_governance_disabled",
            audit_summary="Content governance is disabled for this result.",
            text=text,
            content_fingerprint=_fingerprint(text),
        )


class LocalContentGuard:
    """定义当前组件的职责和边界。"""

    enabled = True

    def __init__(self, *, sensitive_values: tuple[str | None, ...] = ()) -> None:
        """初始化本地规则治理器及需要脱敏的敏感值集合。

        Args:
            sensitive_values: 用于执行当前操作的 sensitive values 参数。
        """
        self.sensitive_values = tuple(sensitive_values)

    async def inspect_untrusted_source(
        self, request: ContentGuardRequest
    ) -> ContentGuardDecision:
        """检查工具来源中的指令注入、引用和敏感内容风险。

        Args:
            request: 当前操作的结构化请求。
        """
        if request.scope != "untrusted_tool_result" or request.source is None:
            raise ContentGuardError("Untrusted source request requires a source")
        source = request.source
        material = source.fingerprint_material()
        fingerprint = _fingerprint(material)
        if _is_instruction_like(material):
            return ContentGuardDecision(
                outcome="block",
                rule_code="instruction_like_source",
                audit_summary="Untrusted source was excluded by the instruction-like content rule.",
                content_fingerprint=fingerprint,
            )
        sanitized = ContentSource(
            title=sanitize_text(
                source.title, extra_sensitive_values=self.sensitive_values
            ),
            url=sanitize_text(source.url, extra_sensitive_values=self.sensitive_values),
            snippet=sanitize_text(
                source.snippet, extra_sensitive_values=self.sensitive_values
            ),
            provider_metadata=source.provider_metadata,
        )
        if sanitized != source:
            return ContentGuardDecision(
                outcome="sanitize",
                rule_code="sensitive_value_redacted",
                audit_summary="Untrusted source was retained after sensitive values were redacted.",
                source=sanitized,
                content_fingerprint=fingerprint,
            )
        return ContentGuardDecision(
            outcome="allow",
            rule_code="content_allowed",
            audit_summary="Untrusted source was retained as data-only reference material.",
            source=source,
            content_fingerprint=fingerprint,
        )

    async def inspect_final_result(
        self, request: ContentGuardRequest
    ) -> ContentGuardDecision:
        """检查最终答案的引用完整性、敏感信息和可发布性。

        Args:
            request: 当前操作的结构化请求。
        """
        if request.scope != "final_result" or request.text is None:
            raise ContentGuardError("Final result request requires text")
        original = request.text
        fingerprint = _fingerprint(original)
        sanitized = sanitize_text(original, extra_sensitive_values=self.sensitive_values)
        citation_rule = _citation_rule(
            task_type=request.task_type,
            answer=sanitized,
            safe_source_urls=request.safe_source_urls,
        )
        if citation_rule is not None:
            return ContentGuardDecision(
                outcome="block",
                rule_code=citation_rule,
                audit_summary="Final learn result was blocked by the safe-source citation rule.",
                content_fingerprint=fingerprint,
            )
        if sanitized != original:
            return ContentGuardDecision(
                outcome="sanitize",
                rule_code="sensitive_value_redacted",
                audit_summary="Final result was retained after sensitive values were redacted.",
                text=sanitized,
                content_fingerprint=fingerprint,
            )
        return ContentGuardDecision(
            outcome="allow",
            rule_code="content_allowed",
            audit_summary="Final result passed local content governance.",
            text=sanitized,
            content_fingerprint=fingerprint,
        )


def format_untrusted_source_payload(source: ContentSource) -> dict[str, Any]:
    """Create an explicit model payload for reference data, never instructions.

    Args:
        source: 用于执行当前操作的 source 参数。
    """
    return {
        "trust_boundary": "untrusted_reference_data",
        "instruction_authority": "none",
        "title": source.title,
        "url": source.url,
        "snippet": source.snippet,
    }


def _fingerprint(value: str) -> str:
    """计算文本的短 SHA-256 指纹，用于审计关联而不记录原文。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    return sha256(value.encode("utf-8")).hexdigest()


def _is_instruction_like(value: str) -> bool:
    """判断文本是否包含疑似劫持模型行为的指令式内容。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    return any(pattern.search(value) for pattern in _INSTRUCTION_LIKE_PATTERNS)


def _citation_rule(
    *,
    task_type: str,
    answer: str,
    safe_source_urls: tuple[str, ...],
) -> str | None:
    """根据答案中的 URL 与可信来源集合确定引用治理规则。

    Args:
        task_type: 用于执行当前操作的 task type 参数。
        answer: 用于执行当前操作的 answer 参数。
        safe_source_urls: 用于执行当前操作的 safe source urls 参数。
    """
    if task_type != "learn" or not safe_source_urls or _is_abstention(answer):
        return None
    marker = "参考来源"
    if marker not in answer:
        return "citation_missing"
    citation_section = answer.split(marker, maxsplit=1)[1]
    cited_urls = _urls(citation_section)
    if not cited_urls:
        return "citation_missing"
    allowed = set(safe_source_urls)
    if any(url not in allowed for url in cited_urls):
        return "citation_unknown_source"
    return None


def _urls(value: str) -> tuple[str, ...]:
    """提取文本中的 HTTP(S) URL，并去除常见尾部标点。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    return tuple(
        dict.fromkeys(
            match.rstrip(".,;:!?。；：！？")
            for match in _URL_PATTERN.findall(value)
        )
    )


def _is_abstention(answer: str) -> bool:
    """判断答案是否明确表示缺少足够证据而拒绝下结论。

    Args:
        answer: 用于执行当前操作的 answer 参数。
    """
    folded = answer.casefold()
    return any(marker in folded for marker in _ABSTENTION_MARKERS)


__all__ = [
    "ContentGuard",
    "ContentGuardBlockedError",
    "ContentGuardDecision",
    "ContentGuardError",
    "ContentGuardProviderUnavailableError",
    "ContentGuardRequest",
    "ContentGuardScope",
    "ContentSource",
    "LocalContentGuard",
    "NoopContentGuard",
    "format_untrusted_source_payload",
]
