from __future__ import annotations


def test_memory_service_package_exports_public_api_from_split_modules() -> None:
    """memory.user_memory 应继续暴露原公共 API。"""
    from application import memory_service
    from memory.user_memory import errors, service

    assert memory_service.MemoryService is service.MemoryService
    assert memory_service.MemoryNotFoundError is errors.MemoryNotFoundError
    assert memory_service.InvalidMemoryCommandError is errors.InvalidMemoryCommandError
    assert (
        memory_service.ForbiddenMemoryContentError
        is errors.ForbiddenMemoryContentError
    )
