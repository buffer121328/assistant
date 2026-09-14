from __future__ import annotations


def test_user_memory_package_exports_public_api_from_split_modules() -> None:
    """user_memory 应暴露用户长期记忆服务的公共 API。"""
    from application.user_memory import (
        ForbiddenMemoryContentError,
        InvalidMemoryCommandError,
        MemoryNotFoundError,
        MemoryService,
        errors,
        service,
    )

    assert MemoryService is service.MemoryService
    assert MemoryNotFoundError is errors.MemoryNotFoundError
    assert InvalidMemoryCommandError is errors.InvalidMemoryCommandError
    assert ForbiddenMemoryContentError is errors.ForbiddenMemoryContentError
