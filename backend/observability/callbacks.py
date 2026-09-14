from __future__ import annotations

import os
from typing import Any

from infrastructure.settings.config import Settings


def build_langgraph_callback_handler(settings: Settings) -> Any | None:
    """Build Langfuse's official LangChain/LangGraph callback handler.

    Langfuse's LangGraph integration is exposed through the LangChain callback
    handler. We keep this import lazy so the optional observability dependency
    remains outside the core runtime path.

    Args:
        settings: 用于执行当前操作的 settings 参数。
    """
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None

    # The official handler reads standard Langfuse environment variables. Set
    # missing values from our typed settings, without overriding a deployer's
    # explicit process-level configuration.
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.langfuse_secret_key)
    if settings.langfuse_base_url:
        os.environ.setdefault("LANGFUSE_BASE_URL", settings.langfuse_base_url)
    if settings.app_env:
        os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", settings.app_env)

    try:
        from langfuse.langchain import CallbackHandler
    except Exception:
        return None

    try:
        return CallbackHandler()
    except Exception:
        return None
