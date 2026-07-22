## MODIFIED Requirements

### Requirement: RAG implementation package ownership

The project layout SHALL use `backend/rag` as the primary implementation package for RAG/knowledge retrieval services while preserving `backend/knowledge` only as a compatibility import boundary.

#### Scenario: Primary implementation lives under rag

- **WHEN** a developer inspects the repository layout
- **THEN** `backend/rag/service.py` SHALL contain the `KnowledgeService` implementation
- **AND** `backend/rag/extractors.py` SHALL contain extractor/parser implementation details
- **AND** first-party runtime, API, tool, and evaluation modules SHALL import `KnowledgeService` from `rag` instead of `knowledge`

#### Scenario: Legacy knowledge imports remain compatible

- **WHEN** existing code imports `KnowledgeService` from `knowledge`
- **THEN** it SHALL receive the same service class exported by `rag`
- **AND** importing parser constants from `knowledge.extractors` SHALL continue to work
