from importlib import import_module


def test_langgraph_executor_public_export_remains_available() -> None:
    """The runtime package keeps the existing LangGraphExecutor import path."""
    module = import_module("runtime.langgraph_executor")

    assert module.__all__ == ["LangGraphExecutor"]
    assert module.LangGraphExecutor is not None
