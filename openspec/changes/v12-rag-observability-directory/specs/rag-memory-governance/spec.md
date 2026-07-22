## ADDED Requirements

### Requirement: Retrieved knowledge is citable and untrusted
Every returned knowledge chunk SHALL include a non-path source id, a human-readable citation, and an explicit untrusted-document boundary.

#### Scenario: Search returns a citable chunk
- **GIVEN** an owned document contains a matching term
- **WHEN** the user searches that term
- **THEN** each result contains `source_id` and `citation`
- **AND** no raw managed filesystem path is returned
- **AND** the result is marked `untrusted_document`

#### Scenario: Search has no supported answer
- **GIVEN** no owned chunk matches the query
- **WHEN** the search API responds
- **THEN** it returns an empty item list
- **AND** `answerable` is false

### Requirement: Document deletion removes retrievable chunks
An owner SHALL be able to delete an owned knowledge document, after which its chunks MUST not be returned by search.

#### Scenario: Owner deletes a document
- **GIVEN** an indexed owned document
- **WHEN** the owner deletes it
- **THEN** its status becomes deleted
- **AND** its chunk count becomes zero
- **AND** subsequent search does not return its content
