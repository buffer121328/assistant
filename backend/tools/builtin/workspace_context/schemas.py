from __future__ import annotations

from typing import Any


def _object(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    """执行 处理 object 的内部辅助逻辑。

    Args:
        properties: properties 参数。
        required: required 参数。
    """
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _text(max_length: int = 2_048, *, allow_empty: bool = False) -> dict[str, Any]:
    """执行 处理 text 的内部辅助逻辑。

    Args:
        max_length: max_length 参数。
        allow_empty: allow_empty 参数。
    """
    return {
        "type": "string",
        "minLength": 0 if allow_empty else 1,
        "maxLength": max_length,
    }


def _positive_int(maximum: int) -> dict[str, Any]:
    """执行 处理 positive int 的内部辅助逻辑。

    Args:
        maximum: maximum 参数。
    """
    return {"type": "integer", "minimum": 1, "maximum": maximum}


def _list_schema() -> dict[str, Any]:
    """执行 列出 schema 的内部辅助逻辑。"""
    return _object(
        {"path": _text(1_024, allow_empty=True), "max_entries": _positive_int(200)},
        (),
    )


def _read_file_schema() -> dict[str, Any]:
    """执行 处理 read file schema 的内部辅助逻辑。"""
    return _object(
        {"path": _text(1_024), "max_bytes": _positive_int(2_000_000)},
        ("path",),
    )


def _search_text_schema() -> dict[str, Any]:
    """执行 搜索 text schema 的内部辅助逻辑。"""
    return _object(
        {
            "query": _text(500),
            "path": _text(1_024, allow_empty=True),
            "max_results": _positive_int(200),
            "max_file_bytes": _positive_int(2_000_000),
        },
        ("query",),
    )


def _find_files_schema() -> dict[str, Any]:
    """执行 处理 find files schema 的内部辅助逻辑。"""
    return _object(
        {
            "pattern": _text(500),
            "path": _text(1_024, allow_empty=True),
            "max_results": _positive_int(200),
        },
        ("pattern",),
    )


def _readonly_shell_schema() -> dict[str, Any]:
    """执行 处理 readonly shell schema 的内部辅助逻辑。"""
    return _object(
        {
            "command": {
                "type": "array",
                "items": _text(1_000),
                "minItems": 1,
                "maxItems": 32,
            }
        },
        ("command",),
    )
