import asyncio
import json
from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent_harness.skill_store import MAX_ARCHIVE_BYTES
from packages.capabilities import (
    CapabilityKind,
    CapabilityRegistry,
    build_default_registry,
)
from packages.integrations import CredentialCipher, CredentialError
from packages.knowledge import KnowledgeError, KnowledgeService, MAX_IMPORT_BYTES
from packages.notifications import NotificationError, ReminderService

from .account_connections import AccountConnectionError, AccountConnectionService
from .database import get_session
from .conversation_memory import ConversationMemoryService
from .conversations import ConversationError, ConversationService
from .errors import AppError
from .langbot import handle_langbot_webhook
from .model_gateway import handle_model_chat
from .schemas import (
    AccountConnectionActorRequest,
    AccountConnectionCreateRequest,
    AccountConnectionListResponse,
    AccountConnectionResponse,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalListResponse,
    CapabilityCatalogResponse,
    ConversationActorRequest,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationMessageListResponse,
    ConversationResponse,
    LangBotWebhookRequest,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
    KnowledgeImportResponse,
    KnowledgeSearchItem,
    KnowledgeSearchResponse,
    ReminderActorRequest,
    ReminderCreateRequest,
    ReminderListResponse,
    ReminderResponse,
    DesktopNotificationListResponse,
    DesktopNotificationResponse,
    ModelChatRequest,
    ModelChatResponse,
    SkillActorRequest,
    SkillCreateRequest,
    SkillListResponse,
    SkillResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskSubmissionResponse,
    account_connection_response,
    approval_response,
    capability_response,
    conversation_message_response,
    conversation_response,
    skill_response,
    task_response,
)
from .models import (
    ApprovalStatus,
    Memory,
    MemoryConsolidationDigest,
    MemoryFeedback,
    MemoryIndexOutbox,
    MemoryLink,
    MemoryPolicy,
    MemoryRetrievalTrace,
    MemoryRetrievalTraceItem,
    NotificationOutbox,
    Task,
    TaskStatus,
    User,
)
from .services import ApprovalService, MemoryService, TaskService, TaskServiceError
from .skill_lifecycle import SkillLifecycleError, SkillLifecycleService
from .worker import enqueue_task_execution
from .task_events import TaskEventRepository, event_record

router = APIRouter()


def raise_knowledge_error(exc: KnowledgeError) -> None:
    raise AppError(
        code=exc.code,
        message="Knowledge operation failed.",
        status_code=404 if exc.code == "knowledge_user_not_found" else 400,
    ) from exc


def raise_notification_error(exc: NotificationError) -> None:
    raise AppError(
        code=exc.code,
        message="Notification operation failed.",
        status_code=404 if exc.code.endswith("not_found") else 409,
    ) from exc


def account_service(
    request: Request, session: AsyncSession
) -> AccountConnectionService:
    try:
        cipher = CredentialCipher(
            request.app.state.settings.credential_master_key.get_secret_value()
        )
    except CredentialError as exc:
        raise AppError(
            code="credential_master_key_unavailable",
            message="Credential storage is not configured.",
            status_code=503,
        ) from exc
    return AccountConnectionService(
        session,
        cipher=cipher,
        tester=getattr(request.app.state, "connection_tester", None),
    )


def raise_account_error(exc: AccountConnectionError) -> None:
    raise AppError(
        code=exc.code,
        message="Account connection operation failed.",
        status_code=exc.status_code,
    ) from exc


@router.get("/health")
def health_check(request: Request) -> dict[str, str]:
    return {
        "service_name": request.app.state.settings.service_name,
        "status": "ok",
    }


def reminder_response(item: object) -> ReminderResponse:
    return ReminderResponse(
        reminder_id=str(getattr(item, "id")),
        user_id=str(getattr(item, "user_id")),
        title=str(getattr(item, "title")),
        message=str(getattr(item, "message")),
        due_at=getattr(item, "due_at"),
        channel=str(getattr(item, "channel")),
        status=str(getattr(item, "status")),
        cancelled_at=getattr(item, "cancelled_at"),
    )


@router.post(
    "/api/reminders",
    response_model=ReminderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_reminder(
    payload: ReminderCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReminderResponse:
    try:
        reminder = await ReminderService(session).create(
            user_id=payload.user_id,
            title=payload.title,
            message=payload.message,
            due_at=payload.due_at,
            channel=payload.channel,
        )
    except NotificationError as exc:
        raise_notification_error(exc)
    return reminder_response(reminder)


@router.get("/api/reminders", response_model=ReminderListResponse)
async def list_reminders(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReminderListResponse:
    reminders = await ReminderService(session).list(user_id=user_id)
    items: list[ReminderResponse] = []
    for reminder in reminders:
        outcome = await session.scalar(
            select(NotificationOutbox)
            .where(NotificationOutbox.reminder_id == reminder.id)
            .order_by(
                NotificationOutbox.updated_at.desc(), NotificationOutbox.id.desc()
            )
            .limit(1)
        )
        response = reminder_response(reminder)
        if outcome is not None:
            response.delivery_status = outcome.status
            response.last_error_code = outcome.last_error_code
        items.append(response)
    return ReminderListResponse(items=items)


@router.post("/api/reminders/{reminder_id}/cancel", response_model=ReminderResponse)
async def cancel_reminder(
    reminder_id: str,
    payload: ReminderActorRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReminderResponse:
    try:
        reminder = await ReminderService(session).cancel(
            user_id=payload.user_id, reminder_id=reminder_id
        )
    except NotificationError as exc:
        raise_notification_error(exc)
    return reminder_response(reminder)


@router.get("/api/notifications/poll", response_model=DesktopNotificationListResponse)
async def poll_notifications(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DesktopNotificationListResponse:
    items = await ReminderService(session).poll_desktop(user_id=user_id)
    return DesktopNotificationListResponse(
        items=[DesktopNotificationResponse(**item.__dict__) for item in items]
    )


@router.post(
    "/api/notifications/{outbox_id}/ack", status_code=status.HTTP_204_NO_CONTENT
)
async def acknowledge_notification(
    outbox_id: str,
    payload: ReminderActorRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    try:
        await ReminderService(session).acknowledge_desktop(
            user_id=payload.user_id, outbox_id=outbox_id
        )
    except NotificationError as exc:
        raise_notification_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/knowledge/import",
    response_model=KnowledgeImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_knowledge(
    request: Request,
    user_id: Annotated[str, Form(min_length=1)],
    document: Annotated[UploadFile, File()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeImportResponse:
    content = await document.read(MAX_IMPORT_BYTES + 1)
    await document.close()
    try:
        result = await KnowledgeService(
            session, import_root=request.app.state.settings.knowledge_root
        ).store_upload(
            user_id=user_id,
            filename=document.filename or "",
            content=content,
        )
    except KnowledgeError as exc:
        raise_knowledge_error(exc)
    return KnowledgeImportResponse(**result.__dict__)


@router.get("/api/knowledge/documents", response_model=KnowledgeDocumentListResponse)
async def list_knowledge_documents(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeDocumentListResponse:
    items = await KnowledgeService(
        session, import_root=request.app.state.settings.knowledge_root
    ).list_documents(user_id=user_id)
    return KnowledgeDocumentListResponse(
        items=[KnowledgeDocumentResponse(**item.__dict__) for item in items]
    )


@router.get("/api/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    query: Annotated[str, Query(min_length=1, max_length=200)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> KnowledgeSearchResponse:
    try:
        results = await KnowledgeService(
            session, import_root=request.app.state.settings.knowledge_root
        ).search(user_id=user_id, query=query, limit=limit)
    except KnowledgeError as exc:
        raise_knowledge_error(exc)
    return KnowledgeSearchResponse(
        items=[KnowledgeSearchItem(**item.__dict__) for item in results]
    )


@router.post(
    "/api/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: ConversationCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationResponse:
    try:
        item = await ConversationService(session).create(
            user_id=payload.user_id, title=payload.title
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    return conversation_response(item)


@router.get("/api/conversations", response_model=ConversationListResponse)
async def list_conversations(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationListResponse:
    try:
        items = await ConversationService(session).list_active(user_id)
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    return ConversationListResponse(
        items=[conversation_response(item) for item in items]
    )


@router.get(
    "/api/conversations/{conversation_id}/messages",
    response_model=ConversationMessageListResponse,
)
async def list_conversation_messages(
    conversation_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ConversationMessageListResponse:
    try:
        items = await ConversationService(session).list_messages(
            conversation_id=conversation_id, user_id=user_id, limit=limit
        )
        summary = await ConversationMemoryService(session).get_active_summary(
            conversation_id=conversation_id, user_id=user_id
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    return ConversationMessageListResponse(
        items=[conversation_message_response(item) for item in items],
        compacted=summary is not None,
        summary_updated_at=summary.updated_at if summary else None,
        summary_version=summary.summary_version if summary else None,
    )


@router.post(
    "/api/conversations/{conversation_id}/archive",
    response_model=ConversationResponse,
)
async def archive_conversation(
    conversation_id: str,
    payload: ConversationActorRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationResponse:
    try:
        item = await ConversationService(session).archive(
            conversation_id=conversation_id, user_id=payload.user_id
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    return conversation_response(item)


@router.get("/api/capabilities", response_model=CapabilityCatalogResponse)
def list_capabilities(
    request: Request,
    kind: Annotated[CapabilityKind | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
) -> CapabilityCatalogResponse:
    registry: CapabilityRegistry = request.app.state.capability_registry
    return CapabilityCatalogResponse(
        revision=registry.revision,
        items=[
            capability_response(metadata)
            for metadata in registry.list(kind=kind, enabled=enabled)
        ],
    )


@router.get("/api/connections", response_model=AccountConnectionListResponse)
async def list_connections(
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionListResponse:
    items = await account_service(request, session).list(user_id)
    return AccountConnectionListResponse(
        items=[account_connection_response(item) for item in items]
    )


@router.post(
    "/api/connections",
    response_model=AccountConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    payload: AccountConnectionCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionResponse:
    try:
        item = await account_service(request, session).create(
            user_id=payload.user_id,
            provider=payload.provider,
            display_name=payload.display_name,
            credentials=payload.credentials,
        )
    except AccountConnectionError as exc:
        raise_account_error(exc)
    return account_connection_response(item)


async def update_connection_status(
    *,
    request: Request,
    session: AsyncSession,
    connection_id: str,
    user_id: str,
    new_status: str,
) -> AccountConnectionResponse:
    try:
        item = await account_service(request, session).set_status(
            connection_id, user_id, new_status
        )
    except AccountConnectionError as exc:
        raise_account_error(exc)
    return account_connection_response(item)


@router.post(
    "/api/connections/{connection_id}/test",
    response_model=AccountConnectionResponse,
)
async def test_connection(
    connection_id: str,
    payload: AccountConnectionActorRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionResponse:
    try:
        item = await account_service(request, session).test(
            connection_id, payload.user_id
        )
    except AccountConnectionError as exc:
        raise_account_error(exc)
    return account_connection_response(item)


@router.post(
    "/api/connections/{connection_id}/disable",
    response_model=AccountConnectionResponse,
)
async def disable_connection(
    connection_id: str,
    payload: AccountConnectionActorRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionResponse:
    return await update_connection_status(
        request=request,
        session=session,
        connection_id=connection_id,
        user_id=payload.user_id,
        new_status="disabled",
    )


@router.delete(
    "/api/connections/{connection_id}",
    response_model=AccountConnectionResponse,
)
async def revoke_connection(
    connection_id: str,
    request: Request,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AccountConnectionResponse:
    return await update_connection_status(
        request=request,
        session=session,
        connection_id=connection_id,
        user_id=user_id,
        new_status="revoked",
    )


def raise_app_error(exc: TaskServiceError | SkillLifecycleError) -> None:
    raise AppError(
        code=exc.code,
        message=str(exc),
        status_code=exc.status_code,
    ) from exc


def lifecycle_service(
    request: Request,
    session: AsyncSession,
) -> SkillLifecycleService:
    store = request.app.state.managed_skill_store

    def refresh_registry() -> None:
        request.app.state.capability_registry = build_default_registry(
            store.builtin_root,
            managed_store=store,
        )

    return SkillLifecycleService(
        session,
        store=store,
        refresh_registry=refresh_registry,
    )


@router.get("/api/skills", response_model=SkillListResponse)
async def list_skills(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillListResponse:
    items = lifecycle_service(request, session).list_skills()
    return SkillListResponse(items=[skill_response(item) for item in items])


@router.post(
    "/api/skills",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(
    payload: SkillCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillResponse:
    try:
        item = await lifecycle_service(request, session).create(
            user_id=payload.user_id,
            name=payload.name,
            display_name=payload.display_name,
            summary=payload.summary,
            instructions=payload.instructions,
        )
    except (TaskServiceError, SkillLifecycleError) as exc:
        raise_app_error(exc)
    return skill_response(item)


@router.post(
    "/api/skills/install",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
)
async def install_skill(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: Annotated[str, Form(min_length=1)],
    package: Annotated[UploadFile, File()],
) -> SkillResponse:
    try:
        content = await package.read(MAX_ARCHIVE_BYTES + 1)
    finally:
        await package.close()
    try:
        item = await lifecycle_service(request, session).install(
            user_id=user_id,
            package=content,
        )
    except (TaskServiceError, SkillLifecycleError) as exc:
        raise_app_error(exc)
    return skill_response(item)


async def set_skill_enabled(
    *,
    request: Request,
    session: AsyncSession,
    payload: SkillActorRequest,
    name: str,
    enabled: bool,
) -> SkillResponse:
    try:
        item = await lifecycle_service(request, session).set_enabled(
            user_id=payload.user_id,
            name=name,
            enabled=enabled,
        )
    except (TaskServiceError, SkillLifecycleError) as exc:
        raise_app_error(exc)
    return skill_response(item)


@router.post("/api/skills/{name}/enable", response_model=SkillResponse)
async def enable_skill(
    name: str,
    payload: SkillActorRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillResponse:
    return await set_skill_enabled(
        request=request,
        session=session,
        payload=payload,
        name=name,
        enabled=True,
    )


@router.post("/api/skills/{name}/disable", response_model=SkillResponse)
async def disable_skill(
    name: str,
    payload: SkillActorRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SkillResponse:
    return await set_skill_enabled(
        request=request,
        session=session,
        payload=payload,
        name=name,
        enabled=False,
    )


@router.delete(
    "/api/skills/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def uninstall_skill(
    name: str,
    user_id: Annotated[str, Query(min_length=1)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    try:
        await lifecycle_service(request, session).uninstall(
            user_id=user_id,
            name=name,
        )
    except (TaskServiceError, SkillLifecycleError) as exc:
        raise_app_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/internal/models/chat", response_model=ModelChatResponse)
async def chat_with_model(
    payload: ModelChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ModelChatResponse:
    return await handle_model_chat(
        payload=payload,
        session=session,
        settings=request.app.state.settings,
    )


@router.post("/api/webhooks/langbot")
async def receive_langbot_webhook(
    payload: LangBotWebhookRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    return await handle_langbot_webhook(
        payload=payload,
        headers=request.headers,
        session=session,
        settings=request.app.state.settings,
    )


@router.post(
    "/api/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    payload: TaskCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResponse:
    try:
        task = await TaskService(session).create_task(
            user_id=payload.user_id,
            platform=payload.platform,
            task_type=payload.task_type,
            input_text=payload.input_text,
            workflow_key=payload.workflow_key,
            model_class=payload.model_class,
            conversation_id=payload.conversation_id,
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    except TaskServiceError as exc:
        raise_app_error(exc)
    return task_response(task)


@router.post(
    "/api/tasks/submit",
    response_model=TaskSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_task(
    payload: TaskCreateRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskSubmissionResponse:
    try:
        task = await TaskService(session).create_task(
            user_id=payload.user_id,
            platform=payload.platform,
            task_type=payload.task_type,
            input_text=payload.input_text,
            workflow_key=payload.workflow_key,
            model_class=payload.model_class,
            conversation_id=payload.conversation_id,
        )
    except ConversationError as exc:
        raise AppError(
            exc.code, "Conversation operation failed.", exc.status_code
        ) from exc
    except TaskServiceError as exc:
        raise_app_error(exc)
    queued = enqueue_task_execution(
        task.id,
        runtime_settings=request.app.state.settings,
    )
    return TaskSubmissionResponse(task=task_response(task), queued=queued)


@router.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks(
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskListResponse:
    try:
        tasks = await TaskService(session).list_tasks(user_id)
    except TaskServiceError as exc:
        raise_app_error(exc)
    return TaskListResponse(items=[task_response(task) for task in tasks])


@router.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskResponse:
    try:
        task = await TaskService(session).get_task(task_id)
    except TaskServiceError as exc:
        raise_app_error(exc)
    return task_response(task)


@router.get("/api/tasks/{task_id}/events/stream")
async def stream_task_events(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    after: Annotated[int, Query(ge=0)] = 0,
) -> StreamingResponse:
    task = await session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user_id)
    )
    if task is None:
        raise AppError("task_not_found", "Task not found.", 404)

    async def records():
        sequence = after
        terminal = {
            TaskStatus.SUCCESS.value,
            TaskStatus.FAILED.value,
            TaskStatus.WAITING_APPROVAL.value,
        }
        while not await request.is_disconnected():
            async with request.app.state.db_sessionmaker() as event_session:
                items = await TaskEventRepository(event_session).list_after(
                    task_id=task_id, after=sequence
                )
                current = await event_session.get(Task, task_id)
            for item in items:
                sequence = item.sequence
                yield json.dumps(event_record(item), ensure_ascii=False) + "\n"
            if current is None or (current.status in terminal and not items):
                return
            await asyncio.sleep(0.2)

    return StreamingResponse(records(), media_type="application/x-ndjson")


@router.get(
    "/api/tasks/{task_id}/approvals",
    response_model=ApprovalListResponse,
)
async def list_task_approvals(
    task_id: str,
    user_id: Annotated[str, Query(min_length=1)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalListResponse:
    try:
        approvals = await ApprovalService(session).list_for_owner(
            task_id=task_id,
            user_id=user_id,
        )
    except TaskServiceError as exc:
        raise_app_error(exc)
    return ApprovalListResponse(
        items=[approval_response(approval) for approval in approvals]
    )


def _memory_payload(memory: Memory) -> dict[str, object]:
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
            MemoryIndexOutbox.user_id == user_id, MemoryIndexOutbox.status == "pending"
        )
    )
    return {"counts": counts, "pending_index_count": int(pending_index or 0)}


@router.post("/api/memories", status_code=status.HTTP_201_CREATED)
async def create_memory_api(
    payload: dict[str, object],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
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
    linked_memory_ids = {
        item.source_memory_id for item in links
    } | {item.target_memory_id for item in links}
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
    from .memory_candidates import MemoryPolicyService

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


@router.post(
    "/api/tasks/{task_id}/approvals/{approval_id}/decision",
    response_model=ApprovalDecisionResponse,
)
async def decide_task_approval(
    task_id: str,
    approval_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalDecisionResponse:
    try:
        result = await ApprovalService(session).decide(
            task_id=task_id,
            approval_id=approval_id,
            user_id=payload.user_id,
            decision=ApprovalStatus(payload.decision),
        )
    except TaskServiceError as exc:
        raise_app_error(exc)

    queued = False
    if result.changed and result.approval.status == ApprovalStatus.APPROVED.value:
        queued = enqueue_task_execution(
            result.task.id,
            runtime_settings=request.app.state.settings,
        )
    return ApprovalDecisionResponse(
        approval=approval_response(result.approval),
        task=task_response(result.task),
        queued=queued,
    )
