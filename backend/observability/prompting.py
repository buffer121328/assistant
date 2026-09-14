from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol, cast

from domain.policies.redaction import sanitize_text
from infrastructure.settings.config import Settings

from agent.prompting.types import PromptModule, PromptModuleName


class LangfusePromptClient(Protocol):
    """定义当前组件的接口契约。"""

    def get_prompt(self, name: str, **kwargs: Any) -> Any:
        """Fetch a prompt by name and optional label/type.

        Args:
            name: 目标对象或能力的名称。
            kwargs: 用于执行当前操作的 kwargs 参数。
        """
        ...


class LangfusePromptSource:
    """定义当前组件的职责和边界。"""

    def __init__(
        self,
        *,
        client: LangfusePromptClient,
        prompt_map: Mapping[PromptModuleName, str],
        label: str,
        sensitive_values: tuple[str | None, ...] = (),
    ) -> None:
        """保存 Langfuse prompt 源配置和模块映射。

        Args:
            client: 用于执行当前操作的 client 参数。
            prompt_map: 用于执行当前操作的 prompt map 参数。
            label: 用于执行当前操作的 label 参数。
            sensitive_values: 用于执行当前操作的 sensitive values 参数。
        """
        self.client = client
        self.prompt_map = dict(prompt_map)
        self.label = label.strip() or "production"
        self.sensitive_values = sensitive_values

    def load_module(
        self,
        *,
        module_name: PromptModuleName,
        filename: str,
        runtime_context: Mapping[str, Any] | None = None,
    ) -> PromptModule | None:
        """加载指定 prompt 模块；不可用时返回空值而不阻断请求。

        Args:
            module_name: 用于执行当前操作的 module name 参数。
            filename: 用于执行当前操作的 filename 参数。
            runtime_context: 用于执行当前操作的 runtime context 参数。
        """
        prompt_name = self.prompt_map.get(module_name)
        if not prompt_name:
            return None
        prompt = self.client.get_prompt(
            prompt_name,
            label=self.label,
            type="text",
        )
        content = _compile_prompt(prompt, runtime_context or {}, self.sensitive_values)
        version = _string_attr(prompt, "version") or _string_attr(prompt, "version_id")
        return PromptModule(
            name=module_name,
            filename=filename,
            content=content,
            source="langfuse",
            fingerprint=_checksum(content),
            version=version,
            metadata={
                "prompt_name": prompt_name,
                "prompt_label": self.label,
                "prompt_version": version,
            },
        )


def build_langfuse_prompt_source(
    settings: Settings,
    *,
    client_factory: Any | None = None,
) -> LangfusePromptSource | None:
    """Build the optional Langfuse prompt source from placeholder-safe settings.

    Args:
        settings: 用于执行当前操作的 settings 参数。
        client_factory: 用于执行当前操作的 client factory 参数。
    """
    if not settings.langfuse_prompt_management_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    prompt_map = parse_langfuse_prompt_module_map(
        settings.langfuse_prompt_module_map_json
    )
    if not prompt_map:
        return None
    try:
        if client_factory is None:
            from langfuse import Langfuse

            factory = Langfuse
        else:
            factory = client_factory
        client = factory(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            environment=settings.app_env,
        )
    except Exception:
        return None
    return LangfusePromptSource(
        client=cast(LangfusePromptClient, client),
        prompt_map=prompt_map,
        label=settings.langfuse_prompt_label,
        sensitive_values=(
            settings.langfuse_public_key,
            settings.langfuse_secret_key,
            settings.langfuse_base_url,
        ),
    )


def parse_langfuse_prompt_module_map(value: str) -> dict[PromptModuleName, str]:
    """解析配置中的 Langfuse prompt 模块映射，并过滤无效项。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    if not value.strip():
        return {}
    try:
        payload = json.loads(value)
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[PromptModuleName, str] = {}
    for raw_key, raw_prompt_name in payload.items():
        key = str(raw_key).strip().upper()
        if key not in {"SYSTEM", "MEMORY_GUIDE", "TOOL_POLICY", "RESPONSE_STYLE", "AGENT_CONFIG"}:
            continue
        prompt_name = str(raw_prompt_name).strip()
        if prompt_name:
            result[cast(PromptModuleName, key)] = prompt_name
    return result


def _compile_prompt(
    prompt: object,
    runtime_context: Mapping[str, Any],
    sensitive_values: tuple[str | None, ...],
) -> str:
    """将 Langfuse 返回的 prompt 对象编译为运行时可调用的文本。

    Args:
        prompt: 用于执行当前操作的 prompt 参数。
        runtime_context: 用于执行当前操作的 runtime context 参数。
        sensitive_values: 用于执行当前操作的 sensitive values 参数。
    """
    safe_context = {
        str(key): sanitize_text(value, extra_sensitive_values=sensitive_values)
        for key, value in runtime_context.items()
    }
    compiled: object
    if hasattr(prompt, "compile"):
        try:
            compiled = prompt.compile(**safe_context)
        except TypeError:
            compiled = prompt.compile(safe_context)
    else:
        compiled = getattr(prompt, "prompt", None) or getattr(prompt, "text", None)
    if isinstance(compiled, list):
        compiled = "\n".join(str(item) for item in compiled)
    if not isinstance(compiled, str):
        compiled = str(compiled)
    return sanitize_text(compiled, extra_sensitive_values=sensitive_values)


def _string_attr(value: object, attr: str) -> str | None:
    """从对象中安全读取非空字符串属性。

    Args:
        value: 待校验、归一化或转换的输入值。
        attr: 用于执行当前操作的 attr 参数。
    """
    raw = getattr(value, attr, None)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _checksum(value: str) -> str:
    """计算 prompt 文本的短校验值，用于版本和审计标识。

    Args:
        value: 待校验、归一化或转换的输入值。
    """
    from hashlib import sha256

    return sha256(value.encode("utf-8")).hexdigest()
