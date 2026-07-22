from importlib import import_module


EXPECTED_EXPORTS = {
    "AccountConnection",
    "AgentRun",
    "AgentSchedule",
    "AgentScheduleRun",
    "Approval",
    "ApprovalStatus",
    "ApprovalType",
    "Base",
    "ConnectionAuditLog",
    "Conversation",
    "ConversationMessage",
    "ConversationSummary",
    "DeliveryAttempt",
    "EvolutionChange",
    "EvolutionVersion",
    "ImportAudit",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "Memory",
    "MemoryBlock",
    "MemoryConsolidationDecision",
    "MemoryConsolidationDigest",
    "MemoryConsolidationRun",
    "MemoryEffectiveness",
    "MemoryEffectivenessEvent",
    "MemoryFeedback",
    "MemoryIndexOutbox",
    "MemoryLink",
    "MemoryPolicy",
    "MemoryReleaseReport",
    "MemoryRetrievalPolicyVersion",
    "MemoryRetrievalTrace",
    "MemoryRetrievalTraceItem",
    "ModelLog",
    "NotificationOutbox",
    "PlatformAccount",
    "ProcessedMessage",
    "Reminder",
    "SkillAuditLog",
    "Task",
    "TaskEvent",
    "TaskStatus",
    "TimestampMixin",
    "ToolLog",
    "User",
    "new_id",
    "utc_now",
}

EXPECTED_TABLES = {
    "account_connections",
    "agent_runs",
    "agent_schedule_runs",
    "agent_schedules",
    "approvals",
    "connection_audit_logs",
    "conversation_messages",
    "conversation_summaries",
    "conversations",
    "delivery_attempts",
    "evolution_changes",
    "evolution_versions",
    "import_audits",
    "knowledge_chunks",
    "knowledge_documents",
    "memories",
    "memory_blocks",
    "memory_consolidation_decisions",
    "memory_consolidation_digests",
    "memory_consolidation_runs",
    "memory_effectiveness",
    "memory_effectiveness_events",
    "memory_feedback",
    "memory_index_outbox",
    "memory_links",
    "memory_policies",
    "memory_release_reports",
    "memory_retrieval_policy_versions",
    "memory_retrieval_trace_items",
    "memory_retrieval_traces",
    "model_logs",
    "notification_outbox",
    "platform_accounts",
    "processed_messages",
    "reminders",
    "skill_audit_logs",
    "task_events",
    "tasks",
    "tool_logs",
    "users",
}


def test_domain_models_public_exports_remain_available() -> None:
    """All existing public symbols stay available from domain.model_gateway."""
    models = import_module("domain.models")

    assert set(model_gateway.__all__) == EXPECTED_EXPORTS
    for name in EXPECTED_EXPORTS:
        assert getattr(models, name) is not None


def test_domain_models_metadata_registers_existing_tables() -> None:
    """Importing domain.models registers every ORM table on Base.metadata."""
    from domain.models import Base

    assert set(Base.metadata.tables) == EXPECTED_TABLES
