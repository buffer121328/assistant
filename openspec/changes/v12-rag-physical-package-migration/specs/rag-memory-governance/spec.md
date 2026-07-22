## MODIFIED Requirements

### Requirement: RAG behavior remains stable after package migration

Moving implementation ownership from `backend/knowledge` to `backend/rag` SHALL NOT change observable RAG governance behavior.

#### Scenario: Governance gate still exercises real retrieval

- **WHEN** the V12 governance gate runs after the package migration
- **THEN** it SHALL still execute KnowledgeService ingest, chunking, search, no-answer, and instruction-risk checks through the `rag` package
- **AND** it SHALL produce the same JSON metric categories for recall@k, abstention, and instruction-risk
