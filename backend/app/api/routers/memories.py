from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database import get_session
from app.support.errors import AppError
from domain.models import (
    Memory,
    MemoryConsolidationDigest,
    MemoryFeedback,
    MemoryIndexOutbox,
    MemoryLink,
    MemoryPolicy,
    MemoryRetrievalTrace,
    MemoryRetrievalTraceItem,
    Task,
    User,
)
from application.services import MemoryService, TaskServiceError

router = APIRouter()


def _memory_payload(memory: Memory) -> dict[str, object]:
    """执行 处理 memory payload 的内部辅助逻辑。

    Args:
        memory: memory 参数。
    """
    content = memory.content
    if memory.sensitivity == "forbidden":
        content = "[FORBIDDEN]"
    elif memory.sensitivity == "sensitive":
        content = "[SENSITIVE]"
    return {
        "memory_id": memory.id,
        "user_id": memory.user_id,
        "memory_type": memory.memory_type,
        "status": memory.status,
        "content": content,
        "scope_kind": memory.scope_kind,
        "scope_id": memory.scope_id,
        "sensitivity": memory.sensitivity,
        "confidence": memory.confidence,
        "importance": memory.importance_score,
        "confirmed_by_user": memory.confirmed_by_user,
        "confirmed_at": memory.confirmed_at,
        "valid_from": memory.valid_from,
        "valid_to": memory.valid_to,
        "event_time": memory.event_time,
        "observed_at": memory.observed_at,
        "supersedes_id": memory.supersedes_id,
        "source_kind": memory.source_kind,
        "source_trust": memory.source_trust,
        "reason_code": memory.reason_code,
        "is_pinned": memory.is_pinned,
        "access_count": memory.access_count,
        "last_accessed_at": memory.last_accessed_at,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


@router.get("/api/memories/overview")
async def memory_overview(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """处理 memory overview。

    Args:
        user_id: user_id 参数。
        session: session 参数。
    """
    if await session.get(User, user_id) is None:
        raise AppError("user_not_found", "User not found.", 404)
    rows = list(
        await session.execute(
            select(Memory.status, func.count(Memory.id))
            .where(
                Memory.user_id == user_id,
                Memory.sensitivity != "forbidden",
            )
            .group_by(Memory.status)
        )
    )
    counts = {str(status): int(count) for status, count in rows}
    pending_index = await session.scalar(
        select(func.count(MemoryIndexOutbox.id)).where(
            MemoryIndexOutbox.user_id == user_id,
            MemoryIndexOutbox.status.in_(("pending", "retry", "processing")),
        )
    )
    return {"counts": counts, "pending_index_count": int(pending_index or 0)}


@router.post("/api/memories", status_code=status.HTTP_201_CREATED)
async def create_memory_api(
    payload: dict[str, object],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """创建 memory api。

    Args:
        payload: payload 参数。
        session: session 参数。
    """
    user_id = str(payload.get("user_id") or "")
    if await session.get(User, user_id) is None:
        raise AppError("user_not_found", "User not found.", 404)
    service = MemoryService(session)
    try:
        memory = await service.create_memory(
            user_id=user_id,
            content=str(payload.get("content") or ""),
            memory_type=str(payload.get("memory_type") or "preference"),
            source_kind="memory_center",
            reason_code="explicit_user_request",
        )
        scope_kind = str(payload.get("scope_kind") or "user/global")
        scope_id = str(payload["scope_id"]) if payload.get("scope_id") else None
        if scope_kind != "user/global" or scope_id is not None:
            memory = await service.change_memory_scope(
                user_id=user_id,
                memory_id=memory.id,
                scope_kind=scope_kind,
                scope_id=scope_id,
            )
        await session.commit()
        await session.refresh(memory)
        return {"memory": _memory_payload(memory)}
    except TaskServiceError as exc:
        raise AppError(exc.code, "Memory operation failed.", exc.status_code) from exc


@router.get("/api/memories")
async def list_memories_api(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = None,
    memory_type: str | None = None,
    scope_kind: str | None = None,
    sensitivity: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """列出 memories api。

    Args:
        user_id: user_id 参数。
        session: session 参数。
        status: status 参数。
        memory_type: memory_type 参数。
        scope_kind: scope_kind 参数。
        sensitivity: sensitivity 参数。
        limit: limit 参数。
        offset: offset 参数。
    """
    statement = select(Memory).where(
        Memory.user_id == user_id,
        Memory.sensitivity != "forbidden",
    )
    for column, value in (
        (Memory.status, status),
        (Memory.memory_type, memory_type),
        (Memory.scope_kind, scope_kind),
        (Memory.sensitivity, sensitivity),
    ):
        if value is not None:
            statement = statement.where(column == value)
    items = list(
        await session.scalars(
            statement.order_by(Memory.updated_at.desc(), Memory.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "items": [_memory_payload(item) for item in items],
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/memories/{memory_id}")
async def memory_detail_api(
    memory_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """处理 memory detail api。

    Args:
        memory_id: memory_id 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    memory = await session.scalar(
        select(Memory).where(Memory.id == memory_id, Memory.user_id == user_id)
    )
    if memory is None or memory.sensitivity == "forbidden":
        raise AppError("memory_not_found", "Memory not found.", 404)
    links = list(
        await session.scalars(
            select(MemoryLink).where(
                (MemoryLink.source_memory_id == memory_id)
                | (MemoryLink.target_memory_id == memory_id)
            )
        )
    )
    linked_memory_ids = {item.source_memory_id for item in links} | {
        item.target_memory_id for item in links
    }
    owned_linked_memory_ids = set(
        await session.scalars(
            select(Memory.id).where(
                Memory.id.in_(linked_memory_ids),
                Memory.user_id == user_id,
                Memory.sensitivity != "forbidden",
            )
        )
    )
    links = [
        item
        for item in links
        if item.source_memory_id in owned_linked_memory_ids
        and item.target_memory_id in owned_linked_memory_ids
    ]
    feedback = list(
        await session.scalars(
            select(MemoryFeedback).where(
                MemoryFeedback.memory_id == memory_id, MemoryFeedback.user_id == user_id
            )
        )
    )
    usage = list(
        await session.scalars(
            select(MemoryRetrievalTraceItem)
            .join(
                MemoryRetrievalTrace,
                MemoryRetrievalTrace.id == MemoryRetrievalTraceItem.trace_id,
            )
            .where(MemoryRetrievalTraceItem.memory_id == memory_id)
            .where(MemoryRetrievalTrace.user_id == user_id)
            .order_by(MemoryRetrievalTraceItem.id.desc())
            .limit(20)
        )
    )
    return {
        "memory": _memory_payload(memory),
        "links": [
            {
                "source_memory_id": item.source_memory_id,
                "target_memory_id": item.target_memory_id,
                "link_type": item.link_type,
                "confidence": item.confidence,
                "created_by": item.created_by,
            }
            for item in links
        ],
        "feedback": [
            {
                "feedback_type": item.feedback_type,
                "task_id": item.task_id,
                "conversation_id": item.conversation_id,
                "retrieval_trace_id": item.retrieval_trace_id,
                "created_at": item.created_at,
            }
            for item in feedback
        ],
        "usage": [
            {
                "trace_id": item.trace_id,
                "filter_reason": item.filter_reason,
                "final_rank": item.final_rank,
                "injected_tokens": item.injected_tokens,
            }
            for item in usage
        ],
    }


@router.post("/api/memories/{memory_id}/actions/{action}")
async def memory_action_api(
    memory_id: str,
    action: str,
    payload: dict[str, object],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """处理 memory action api。

    Args:
        memory_id: memory_id 参数。
        action: action 参数。
        payload: payload 参数。
        session: session 参数。
    """
    user_id = str(payload.get("user_id") or "")
    service = MemoryService(session)
    try:
        if action == "confirm":
            memory = await service.confirm_memory(user_id=user_id, memory_id=memory_id)
        elif action == "reject":
            memory = await service.reject_memory(user_id=user_id, memory_id=memory_id)
        elif action == "correct":
            memory = await service.correct_memory(
                user_id=user_id,
                memory_id=memory_id,
                content=str(payload.get("content") or ""),
                confirm=True,
            )
        elif action in {"pin", "unpin"}:
            memory = await service.set_memory_pinned(
                user_id=user_id, memory_id=memory_id, pinned=action == "pin"
            )
        elif action == "scope":
            memory = await service.change_memory_scope(
                user_id=user_id,
                memory_id=memory_id,
                scope_kind=str(payload.get("scope_kind") or ""),
                scope_id=(
                    str(payload.get("scope_id")) if payload.get("scope_id") else None
                ),
            )
        elif action == "archive":
            memory = await service.archive_memory(user_id=user_id, memory_id=memory_id)
        elif action == "forget":
            memory = await service.forget_memory(user_id=user_id, memory_id=memory_id)
        elif action == "validity":
            memory = await service.get_memory(user_id=user_id, memory_id=memory_id)
            try:
                memory.valid_from = (
                    datetime.fromisoformat(str(payload["valid_from"]))
                    if payload.get("valid_from")
                    else None
                )
                memory.valid_to = (
                    datetime.fromisoformat(str(payload["valid_to"]))
                    if payload.get("valid_to")
                    else None
                )
            except ValueError as exc:
                raise AppError(
                    "memory_validity_invalid", "Memory validity is invalid.", 400
                ) from exc
            if (
                memory.valid_from is not None
                and memory.valid_to is not None
                and memory.valid_from >= memory.valid_to
            ):
                raise AppError(
                    "memory_validity_invalid", "Memory validity is invalid.", 400
                )
            await session.flush()
        elif action == "rebuild-index":
            memory = await service.get_memory(user_id=user_id, memory_id=memory_id)
            await service.repository.queue_index_operation(
                memory=memory,
                operation="rebuild",
                error_code="user_requested_rebuild",
            )
        else:
            raise AppError("memory_action_invalid", "Memory action is invalid.", 400)
        await session.commit()
        await session.refresh(memory)
        return {"memory": _memory_payload(memory)}
    except TaskServiceError as exc:
        raise AppError(exc.code, "Memory operation failed.", exc.status_code) from exc


@router.get("/api/memory/policies")
async def list_memory_policies_api(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """列出 memory policies api。

    Args:
        user_id: user_id 参数。
        session: session 参数。
    """
    items = list(
        await session.scalars(
            select(MemoryPolicy)
            .where(MemoryPolicy.user_id == user_id)
            .order_by(MemoryPolicy.policy_key)
        )
    )
    return {
        "items": [
            {
                "policy_key": item.policy_key,
                "scope_kind": item.scope_kind,
                "scope_id": item.scope_id,
                "enabled": item.enabled,
                "value": json.loads(item.value_json),
            }
            for item in items
        ]
    }


@router.put("/api/memory/policies/{policy_key}")
async def update_memory_policy_api(
    policy_key: str,
    payload: dict[str, object],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """更新 memory policy api。

    Args:
        policy_key: policy_key 参数。
        payload: payload 参数。
        session: session 参数。
    """
    user_id = str(payload.get("user_id") or "")
    if await session.get(User, user_id) is None:
        raise AppError("user_not_found", "User not found.", 404)
    prefix = "never_remember:"
    memory_type = policy_key.removeprefix(prefix)
    if not policy_key.startswith(prefix) or memory_type not in {
        "episode",
        "fact",
        "preference",
        "constraint",
        "procedure",
        "reflection",
    }:
        raise AppError("memory_policy_invalid", "Memory policy is invalid.", 400)
    from application.memory_candidates import MemoryPolicyService

    item = await MemoryPolicyService(session).set_never_remember(
        user_id=user_id,
        memory_type=memory_type,
        scope_kind=str(payload.get("scope_kind") or "user/global"),
        scope_id=str(payload["scope_id"]) if payload.get("scope_id") else None,
        enabled=bool(payload.get("enabled", True)),
    )
    await session.commit()
    return {
        "policy": {
            "policy_key": item.policy_key,
            "scope_kind": item.scope_kind,
            "scope_id": item.scope_id,
            "enabled": item.enabled,
            "value": json.loads(item.value_json),
        }
    }


@router.get("/api/memory/consolidation-digests")
async def list_memory_consolidation_digests(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, object]:
    """列出 memory consolidation digests。

    Args:
        user_id: user_id 参数。
        session: session 参数。
        limit: limit 参数。
    """
    if await session.get(User, user_id) is None:
        raise AppError("user_not_found", "User not found.", 404)
    items = list(
        await session.scalars(
            select(MemoryConsolidationDigest)
            .where(MemoryConsolidationDigest.user_id == user_id)
            .order_by(MemoryConsolidationDigest.created_at.desc())
            .limit(limit)
        )
    )
    return {
        "items": [
            {
                "digest_id": item.id,
                "digest_type": item.digest_type,
                "window_start": item.window_start,
                "window_end": item.window_end,
                "content": json.loads(item.content_json),
                "created_at": item.created_at,
            }
            for item in items
        ]
    }


@router.get("/api/tasks/{task_id}/memory-retrieval")
async def get_task_memory_retrieval(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    """获取 task memory retrieval。

    Args:
        task_id: task_id 参数。
        user_id: user_id 参数。
        session: session 参数。
    """
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    if task is None:
        raise AppError("task_not_found", "Task not found.", 404)
    trace = await session.scalar(
        select(MemoryRetrievalTrace)
        .where(
            MemoryRetrievalTrace.task_id == task_id,
            MemoryRetrievalTrace.user_id == user_id,
        )
        .order_by(MemoryRetrievalTrace.created_at.desc())
        .limit(1)
    )
    if trace is None:
        return {"trace": None, "items": []}
    items = list(
        await session.scalars(
            select(MemoryRetrievalTraceItem)
            .where(MemoryRetrievalTraceItem.trace_id == trace.id)
            .order_by(
                MemoryRetrievalTraceItem.final_rank.asc().nulls_last(),
                MemoryRetrievalTraceItem.id.asc(),
            )
        )
    )
    return {
        "trace": {
            "trace_id": trace.id,
            "retrieval_mode": trace.retrieval_mode,
            "time_intent": trace.time_intent,
            "injected_count": trace.injected_count,
            "injected_tokens": trace.injected_tokens,
        },
        "items": [
            {
                "memory_id": item.memory_id,
                "filter_reason": item.filter_reason,
                "final_rank": item.final_rank,
                "injected_tokens": item.injected_tokens,
            }
            for item in items
        ],
    }
