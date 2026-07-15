from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from assistant_api.models import ImportAudit, KnowledgeChunk, KnowledgeDocument, User

from .extractors import PARSER_VERSION, SUPPORTED_MEDIA_TYPES, ExtractionError, extract_text


MAX_IMPORT_BYTES = 20 * 1024 * 1024
CHUNK_CHARS = 1_200
CHUNK_OVERLAP = 120


class KnowledgeError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class IngestionResult:
    document_id: str
    source_label: str
    status: str
    chunk_count: int
    unchanged: bool


@dataclass(frozen=True)
class KnowledgeSearchResult:
    document_id: str
    source_label: str
    ordinal: int
    content: str
    score: int


@dataclass(frozen=True)
class KnowledgeDocumentStatus:
    document_id: str
    source_label: str
    media_type: str
    status: str
    chunk_count: int
    last_error_code: str | None


class KnowledgeService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        import_root: Path,
        max_import_bytes: int = MAX_IMPORT_BYTES,
    ) -> None:
        self.session = session
        self.import_root = import_root
        self.max_import_bytes = max_import_bytes

    async def store_upload(
        self, *, user_id: str, filename: str, content: bytes
    ) -> IngestionResult:
        if await self.session.get(User, user_id) is None:
            raise KnowledgeError("knowledge_user_not_found")
        safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(filename).name)[:180]
        if not safe_name or Path(safe_name).suffix.lower() not in SUPPORTED_MEDIA_TYPES:
            raise KnowledgeError("knowledge_type_unsupported")
        if not 0 < len(content) <= self.max_import_bytes:
            raise KnowledgeError("knowledge_size_invalid")
        checksum = sha256(content).hexdigest()
        existing = await self.session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.user_id == user_id,
                KnowledgeDocument.checksum == checksum,
                KnowledgeDocument.parser_version == PARSER_VERSION,
                KnowledgeDocument.status == "ready",
            )
        )
        if existing is not None:
            self._audit(
                user_id, safe_name, "unchanged", document_id=existing.id
            )
            await self.session.commit()
            return self._result(existing, unchanged=True)
        directory = self.import_root / user_id / str(uuid4())
        directory.mkdir(parents=True, exist_ok=False)
        destination = directory / safe_name
        destination.write_bytes(content)
        try:
            return await self.ingest(user_id=user_id, source=destination)
        except IntegrityError:
            await self.session.rollback()
            destination.unlink(missing_ok=True)
            directory.rmdir()
            existing = await self.session.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.user_id == user_id,
                    KnowledgeDocument.checksum == checksum,
                    KnowledgeDocument.parser_version == PARSER_VERSION,
                    KnowledgeDocument.status == "ready",
                )
            )
            if existing is None:
                raise
            self._audit(
                user_id, safe_name, "unchanged", document_id=existing.id
            )
            await self.session.commit()
            return self._result(existing, unchanged=True)
        except Exception:
            destination.unlink(missing_ok=True)
            directory.rmdir()
            raise

    async def ingest(self, *, user_id: str, source: Path) -> IngestionResult:
        if await self.session.get(User, user_id) is None:
            raise KnowledgeError("knowledge_user_not_found")
        source_label = source.name[:255] or "unnamed"
        try:
            resolved = self._validated_path(user_id, source)
        except KnowledgeError as exc:
            await self._reject(user_id, source_label, exc.code)
            raise

        canonical = str(resolved)
        existing = await self.session.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.user_id == user_id,
                KnowledgeDocument.source_path == canonical,
            )
        )
        checksum = sha256(resolved.read_bytes()).hexdigest()
        if (
            existing is not None
            and existing.checksum == checksum
            and existing.parser_version == PARSER_VERSION
            and existing.status == "ready"
        ):
            self._audit(user_id, source_label, "unchanged", document_id=existing.id)
            await self.session.commit()
            return self._result(existing, unchanged=True)
        duplicate_query = select(KnowledgeDocument).where(
            KnowledgeDocument.user_id == user_id,
            KnowledgeDocument.checksum == checksum,
            KnowledgeDocument.parser_version == PARSER_VERSION,
            KnowledgeDocument.status == "ready",
        )
        if existing is not None:
            duplicate_query = duplicate_query.where(
                KnowledgeDocument.id != existing.id
            )
        duplicate = await self.session.scalar(duplicate_query)
        if duplicate is not None:
            if existing is not None:
                await self.session.execute(
                    update(ImportAudit)
                    .where(ImportAudit.document_id == existing.id)
                    .values(document_id=duplicate.id)
                )
                await self.session.execute(
                    delete(KnowledgeChunk).where(
                        KnowledgeChunk.document_id == existing.id
                    )
                )
                await self.session.delete(existing)
            self._audit(
                user_id, source_label, "unchanged", document_id=duplicate.id
            )
            await self.session.commit()
            return self._result(duplicate, unchanged=True)

        try:
            text = extract_text(resolved)
            chunks = _chunks(text)
        except ExtractionError as exc:
            if existing is not None:
                existing.last_error_code = str(exc)
            await self._reject(
                user_id,
                source_label,
                str(exc),
                document_id=existing.id if existing else None,
            )
            raise KnowledgeError(str(exc)) from exc

        document = existing or KnowledgeDocument(
            user_id=user_id,
            source_label=source_label,
            source_path=canonical,
            media_type=SUPPORTED_MEDIA_TYPES[resolved.suffix.lower()],
            checksum=checksum,
            parser_version=PARSER_VERSION,
            status="ready",
            chunk_count=0,
        )
        if existing is None:
            self.session.add(document)
            await self.session.flush()
        else:
            await self.session.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
            )
        document.source_label = source_label
        document.media_type = SUPPORTED_MEDIA_TYPES[resolved.suffix.lower()]
        document.checksum = checksum
        document.parser_version = PARSER_VERSION
        document.status = "ready"
        document.chunk_count = len(chunks)
        document.last_error_code = None
        self.session.add_all(
            KnowledgeChunk(
                document_id=document.id,
                user_id=user_id,
                ordinal=index,
                content=content,
                content_checksum=sha256(content.encode()).hexdigest(),
            )
            for index, content in enumerate(chunks)
        )
        self._audit(user_id, source_label, "indexed", document_id=document.id)
        await self.session.commit()
        await self.session.refresh(document)
        return self._result(document, unchanged=False)

    async def search(
        self, *, user_id: str, query: str, limit: int = 5
    ) -> tuple[KnowledgeSearchResult, ...]:
        normalized_query = query.strip().casefold()[:200]
        if not normalized_query:
            raise KnowledgeError("knowledge_query_empty")
        bounded_limit = min(max(limit, 1), 20)
        rows = await self.session.execute(
            select(KnowledgeChunk, KnowledgeDocument)
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(
                KnowledgeChunk.user_id == user_id,
                KnowledgeDocument.user_id == user_id,
                KnowledgeDocument.status == "ready",
            )
        )
        terms = tuple(dict.fromkeys(normalized_query.split())) or (normalized_query,)
        ranked: list[KnowledgeSearchResult] = []
        for chunk, document in rows:
            content = chunk.content[:2_000]
            folded = content.casefold()
            score = sum(folded.count(term) for term in terms)
            if score:
                ranked.append(
                    KnowledgeSearchResult(
                        document_id=document.id,
                        source_label=document.source_label,
                        ordinal=chunk.ordinal,
                        content=content,
                        score=score,
                    )
                )
        ranked.sort(key=lambda item: (-item.score, item.source_label, item.ordinal, item.document_id))
        return tuple(ranked[:bounded_limit])

    async def list_documents(self, *, user_id: str) -> tuple[KnowledgeDocumentStatus, ...]:
        documents = await self.session.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.user_id == user_id)
            .order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id)
        )
        return tuple(
            KnowledgeDocumentStatus(
                document_id=document.id,
                source_label=document.source_label,
                media_type=document.media_type,
                status=document.status,
                chunk_count=document.chunk_count,
                last_error_code=document.last_error_code,
            )
            for document in documents
        )

    def _validated_path(self, user_id: str, source: Path) -> Path:
        try:
            user_root = (self.import_root / user_id).resolve(strict=True)
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise KnowledgeError("knowledge_path_invalid") from exc
        if not resolved.is_relative_to(user_root) or not resolved.is_file():
            raise KnowledgeError("knowledge_path_outside_root")
        suffix = resolved.suffix.lower()
        if suffix not in SUPPORTED_MEDIA_TYPES:
            raise KnowledgeError("knowledge_type_unsupported")
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            raise KnowledgeError("knowledge_path_invalid") from exc
        if size <= 0 or size > self.max_import_bytes:
            raise KnowledgeError("knowledge_size_invalid")
        return resolved

    async def _reject(
        self,
        user_id: str,
        source_label: str,
        error_code: str,
        *,
        document_id: str | None = None,
    ) -> None:
        self._audit(
            user_id,
            source_label,
            "rejected",
            error_code=error_code,
            document_id=document_id,
        )
        await self.session.commit()

    def _audit(
        self,
        user_id: str,
        source_label: str,
        status: str,
        *,
        error_code: str | None = None,
        document_id: str | None = None,
    ) -> None:
        self.session.add(
            ImportAudit(
                user_id=user_id,
                document_id=document_id,
                source_label=source_label,
                status=status,
                error_code=error_code,
            )
        )

    @staticmethod
    def _result(document: KnowledgeDocument, *, unchanged: bool) -> IngestionResult:
        return IngestionResult(
            document_id=document.id,
            source_label=document.source_label,
            status=document.status,
            chunk_count=document.chunk_count,
            unchanged=unchanged,
        )


def _chunks(text: str) -> tuple[str, ...]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        content = text[start:end].strip()
        if content:
            chunks.append(content)
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP
    return tuple(chunks)
