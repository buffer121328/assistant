from fastapi import APIRouter, Request

from .account_routes import router as account_router
from .capability_routes import router as capability_router
from .channel_routes import router as channel_router
from .conversation_routes import router as conversation_router
from .knowledge_routes import router as knowledge_router
from .local_routes import router as local_router
from .memory_routes import router as memory_router
from .notification_routes import router as notification_router
from .skill_routes import router as skill_router
from .task_routes import router as task_router
from .worker import enqueue_task_execution as enqueue_task_execution

router = APIRouter()
router.include_router(account_router)
router.include_router(notification_router)
router.include_router(knowledge_router)
router.include_router(channel_router)
router.include_router(conversation_router)
router.include_router(capability_router)
router.include_router(skill_router)
router.include_router(memory_router)
router.include_router(task_router)
router.include_router(local_router)


@router.get("/health")
def health_check(request: Request) -> dict[str, str]:
    return {
        "service_name": request.app.state.settings.service_name,
        "status": "ok",
    }
