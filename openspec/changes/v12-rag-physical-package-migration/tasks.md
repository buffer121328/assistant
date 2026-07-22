## 1. Acceptance and OpenSpec

- [x] 1.1 Add directory-layout acceptance criteria for `rag` implementation ownership and `knowledge` compatibility shims
- [x] 1.2 Add/adjust automated tests for new primary imports and legacy imports

## 2. Implementation migration

- [x] 2.1 Move service/extractor implementation to `backend/rag`
- [x] 2.2 Convert `backend/knowledge` to compatibility re-export shims
- [x] 2.3 Update first-party runtime/evaluation/tool imports to use `rag`

## 3. Documentation and quality

- [x] 3.1 Update README and V12-08 docs with completed physical migration status
- [x] 3.2 Run OpenSpec strict validation, Ruff, mypy, targeted pytest, and V12 governance gate
- [x] 3.3 Commit migration changes
