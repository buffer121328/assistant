from importlib import import_module


EXPECTED_EXPORTS = {
    "ApprovalRepository",
    "MessageRepository",
    "MemoryCreate",
    "MemoryRepository",
    "ModelLogCreate",
    "ModelLogRepository",
    "ProcessedMessageCreate",
    "SkillAuditRepository",
    "TaskCreate",
    "TaskRepository",
    "ToolLogCreate",
    "ToolLogRepository",
    "eligible_memory_conditions",
}


def test_infrastructure_repositories_public_exports_remain_available() -> None:
    """All existing public repository symbols stay available from the package."""
    repositories = import_module("infrastructure.repositories")

    assert set(repositories.__all__) == EXPECTED_EXPORTS
    for name in EXPECTED_EXPORTS:
        assert getattr(repositories, name) is not None
