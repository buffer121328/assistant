"""harden V5 knowledge idempotency

Revision ID: 202607150001
Revises: 202607140005
"""

from collections.abc import Sequence

from alembic import op

revision: str = "202607150001"
down_revision: str | None = "202607140005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RANKED_DOCUMENTS = """
SELECT
    id,
    first_value(id) OVER (
        PARTITION BY user_id, checksum, parser_version
        ORDER BY CASE WHEN status = 'ready' THEN 0 ELSE 1 END, created_at, id
    ) AS keep_id,
    row_number() OVER (
        PARTITION BY user_id, checksum, parser_version
        ORDER BY CASE WHEN status = 'ready' THEN 0 ELSE 1 END, created_at, id
    ) AS duplicate_rank
FROM knowledge_documents
"""


def upgrade() -> None:
    op.execute(
        f"""
        WITH ranked AS ({_RANKED_DOCUMENTS}),
        duplicates AS (
            SELECT id, keep_id FROM ranked WHERE duplicate_rank > 1
        )
        UPDATE import_audits AS audit
        SET document_id = duplicates.keep_id
        FROM duplicates
        WHERE audit.document_id = duplicates.id
        """
    )
    op.execute(
        f"""
        WITH ranked AS ({_RANKED_DOCUMENTS}),
        duplicates AS (
            SELECT id FROM ranked WHERE duplicate_rank > 1
        )
        DELETE FROM knowledge_chunks AS chunk
        USING duplicates
        WHERE chunk.document_id = duplicates.id
        """
    )
    op.execute(
        f"""
        WITH ranked AS ({_RANKED_DOCUMENTS}),
        duplicates AS (
            SELECT id FROM ranked WHERE duplicate_rank > 1
        )
        DELETE FROM knowledge_documents AS document
        USING duplicates
        WHERE document.id = duplicates.id
        """
    )
    op.create_unique_constraint(
        "uq_knowledge_documents_user_checksum_parser",
        "knowledge_documents",
        ["user_id", "checksum", "parser_version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_knowledge_documents_user_checksum_parser",
        "knowledge_documents",
        type_="unique",
    )
