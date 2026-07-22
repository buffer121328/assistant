# V12 RAG physical package migration

## Why

V12-08 introduced `backend/rag` as a facade while keeping the historical `backend/knowledge` package as the implementation owner. This keeps runtime compatibility but leaves the physical directory evolution unfinished: future RAG work still lands in the legacy package unless the implementation is moved.

## What changes

- Move KnowledgeService, extractors, constants, and RAG citation helpers under `backend/rag` as the primary implementation package.
- Keep `backend/knowledge` as a compatibility shim that re-exports the new `rag` implementation for existing imports.
- Update first-party API/tool/evaluation imports to use `rag` directly.
- Add directory-layout acceptance tests proving implementation ownership and legacy compatibility.

## Impact

- No API path, database schema, persisted file layout, or behavior change is intended.
- Existing `from knowledge import KnowledgeService` and `from knowledge.extractors import ...` imports continue to work.
- New development should target `backend/rag`.
