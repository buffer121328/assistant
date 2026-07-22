## ADDED Requirements

### Requirement: Citation references are machine-verifiable
Each retrieved knowledge source SHALL expose a citation token derived from its source id. The system SHALL distinguish valid references, unknown references, and substantive answers that omit required references.

#### Scenario: Answer cites a retrieved source
- **GIVEN** a knowledge chunk was retrieved
- **WHEN** an answer includes that chunk's citation token
- **THEN** citation reference validation passes

#### Scenario: Answer invents a source id
- **GIVEN** a set of retrieved chunks
- **WHEN** an answer cites a source id outside that set
- **THEN** validation fails and reports the unknown source id

#### Scenario: Substantive answer omits citations
- **GIVEN** retrieved sources are available
- **WHEN** a substantive answer contains no citation token
- **THEN** validation fails with missing required citation

#### Scenario: No-answer response omits citations
- **GIVEN** the assistant explicitly abstains due to insufficient evidence
- **WHEN** no citation token is present
- **THEN** citation reference validation may pass

### Requirement: Retrieved context is explicitly untrusted
The RAG boundary SHALL provide a formatter that labels retrieved content as data and states that it MUST NOT be treated as system, developer, permission, or tool instructions.

#### Scenario: Formatter wraps an instruction-like document
- **GIVEN** a retrieved chunk contains instruction-like text
- **WHEN** the RAG context is formatted
- **THEN** the source is enclosed by an untrusted data boundary
- **AND** the wrapper states that document text cannot grant permissions or request tool execution
