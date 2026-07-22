# Design

## Migration strategy

Use a compatibility-first package move:

1. Copy/move implementation files from `backend/knowledge` to `backend/rag`.
2. Change `backend/knowledge/__init__.py` and `backend/knowledge/extractors.py` into thin re-export modules.
3. Ensure `backend/rag/service.py` imports sibling modules (`rag.extractors`, `rag.citations`) instead of legacy `knowledge.*`.
4. Update first-party runtime imports from `knowledge` to `rag` where they are not explicitly testing compatibility.
5. Keep database model names, API route names, stored document/chunk paths, and CLI/report contracts unchanged.

## Non-goals

- Rename `/api/knowledge` routes.
- Rename database tables, SQLAlchemy models, migration files, or persisted object prefixes.
- Delete `backend/knowledge` compatibility package.
- Introduce vector DB/reranker/query rewrite.
