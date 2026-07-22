## MODIFIED Requirements

### Requirement: Backend boundaries evolve through compatible facades
The backend SHALL migrate one responsibility boundary at a time through a non-empty facade, while preserving old imports for a bounded compatibility stage.

#### Scenario: RAG facade is introduced
- **GIVEN** knowledge ingestion and retrieval currently live under `backend/knowledge`
- **WHEN** the first V12 directory migration is applied
- **THEN** `backend/rag` exports the supported RAG contract
- **AND** active API/tool callers use the facade
- **AND** the old package remains compatible until a later explicit migration
