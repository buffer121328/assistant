from __future__ import annotations

import importlib
from typing import Any

_EXPORTS = {
    "build_langgraph_callback_handler": "observability.callbacks",
    "run_langfuse_dataset_experiment": "observability.experiments",
    "run_langfuse_experiment": "observability.experiments",
    "LangfuseObservability": "observability.observability",
    "build_langfuse_observability": "observability.observability",
    "LangfusePromptSource": "observability.prompting",
    "build_langfuse_prompt_source": "observability.prompting",
    "parse_langfuse_prompt_module_map": "observability.prompting",
    "run_core_command_langfuse_experiment": "observability.runner",
}


def __getattr__(name: str) -> Any:
    """按需加载可选观测导出，避免缺少外部 SDK 时阻塞核心导入。"""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module 'observability' has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value
    return value


__all__ = list(_EXPORTS)
