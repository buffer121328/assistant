## ADDED Requirements

### Requirement: Tasks expose a local correlation trace
Every task response SHALL expose a trace id equivalent to the task id, and diagnostics SHALL aggregate local model, tool, approval, retrieval, event, and error records by that id.

#### Scenario: Owner reads task diagnostics
- **GIVEN** an owned task has model, tool, approval, and retrieval records
- **WHEN** the owner requests diagnostics
- **THEN** the response uses the task id as trace id
- **AND** it lists the related records and retrieval source ids
- **AND** diagnostic text is sanitized and bounded

### Requirement: Local governance evaluation is reportable
The repository SHALL provide deterministic V12 RAG and Agent governance datasets and a local command that saves a machine-readable report.

#### Scenario: Governance fixtures pass
- **GIVEN** the checked-in V12 governance datasets
- **WHEN** the local gate runs
- **THEN** it evaluates citation, abstention, deletion, injection, trace, trajectory, quality, and security categories
- **AND** writes a JSON report
- **AND** returns non-zero when a case fails or fixture data is invalid
