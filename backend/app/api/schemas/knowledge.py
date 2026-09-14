from __future__ import annotations

from pydantic import BaseModel


class KnowledgeImportResponse(BaseModel):
    """表示 处理 knowledge import response 的后端数据结构或服务对象。"""

    document_id: str
    source_label: str
    status: str
    chunk_count: int
    unchanged: bool


class KnowledgeDocumentResponse(BaseModel):
    """表示 处理 knowledge document response 的后端数据结构或服务对象。"""

    document_id: str
    source_label: str
    media_type: str
    status: str
    chunk_count: int
    last_error_code: str | None


class KnowledgeDocumentListResponse(BaseModel):
    """表示 处理 knowledge document list response 的后端数据结构或服务对象。"""

    items: list[KnowledgeDocumentResponse]


class KnowledgeDeleteResponse(BaseModel):
    """表示 处理 knowledge delete response 的后端数据结构或服务对象。"""

    document_id: str
    status: str
    chunk_count: int


class KnowledgeSearchItem(BaseModel):
    """表示 处理 knowledge search item 的后端数据结构或服务对象。"""

    document_id: str
    source_id: str
    source_label: str
    citation: str
    citation_token: str
    ordinal: int
    content: str
    score: int
    trust_boundary: str
    instruction_risk: bool


class KnowledgeSearchResponse(BaseModel):
    """表示 处理 knowledge search response 的后端数据结构或服务对象。"""

    items: list[KnowledgeSearchItem]
    answerable: bool
