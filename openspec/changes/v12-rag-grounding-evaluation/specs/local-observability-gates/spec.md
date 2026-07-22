## ADDED Requirements

### Requirement: The local RAG gate executes the real retrieval implementation
The local governance gate SHALL execute KnowledgeService ingestion, chunking, and search against a temporary isolated database and knowledge root.

#### Scenario: Real retrieval fixture runs
- **GIVEN** the checked-in V12 retrieval dataset
- **WHEN** the governance gate runs
- **THEN** documents are ingested through KnowledgeService
- **AND** queries run through the production search method
- **AND** the report contains recall@k, abstention accuracy, and instruction-risk accuracy
- **AND** normal, no-answer, conflict, injection, and long-document cases pass
