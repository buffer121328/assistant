from fastapi import APIRouter, Request

from app.api.routers.accounts import router as account_router
from app.api.routers.capabilities import router as capability_router
from app.api.routers.conversations import router as conversation_router
from app.api.routers.knowledge import router as knowledge_router
from app.api.routers.memories import router as memory_router
from app.api.routers.model_chat import router as model_chat_router
from app.api.routers.notifications import router as notification_router
from app.api.routers.skills import router as skill_router
from app.api.routers.tasks import router as task_router
from channels.desktop.router import router as desktop_router
from channels.langbot.router import router as langbot_router
from workers.worker import enqueue_task_execution as enqueue_task_execution

router = APIRouter()
router.include_router(account_router)
router.include_router(notification_router)
router.include_router(knowledge_router)
router.include_router(model_chat_router)
router.include_router(langbot_router)
router.include_router(conversation_router)
router.include_router(capability_router)
router.include_router(skill_router)
router.include_router(memory_router)
router.include_router(task_router)
router.include_router(desktop_router)


@router.get("/health")
def health_check(request: Request) -> dict[str, str]:
    return {
        "service_name": request.app.state.settings.service_name,
        "status": "ok",
    }
