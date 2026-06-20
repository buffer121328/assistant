from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .errors import AppError
from .schemas import (
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    task_response,
)
from .services import TaskService, TaskServiceError

router = APIRouter()


@router.get("/health")
def health_check(request: Request) -> dict[str, str]:
    return {
        "service_name": request.app.state.settings.service_name,
        "status": "ok",
    }


def raise_app_error(exc: TaskServiceError) -> None:
    raise AppError(
        code=exc.code,
        message=str(exc),
        status_code=exc.status_code,
    ) from exc


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
