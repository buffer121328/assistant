from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy import select
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
    skill_response,
    task_response,
)
from .models import ApprovalStatus, NotificationOutbox
from .services import ApprovalService, TaskService, TaskServiceError
from .skill_lifecycle import SkillLifecycleError, SkillLifecycleService
from .worker import enqueue_task_execution

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


def account_service(request: Request, session: AsyncSession) -> AccountConnectionService:
    try:
        cipher = CredentialCipher(request.app.state.settings.credential_master_key.get_secret_value())
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
            .order_by(NotificationOutbox.updated_at.desc(), NotificationOutbox.id.desc())
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


@router.post("/api/notifications/{outbox_id}/ack", status_code=status.HTTP_204_NO_CONTENT)
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
        item = await account_service(request, session).test(connection_id, payload.user_id)
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
        )
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
        )
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
