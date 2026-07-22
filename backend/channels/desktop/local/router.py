from __future__ import annotations

from fastapi import APIRouter

from channels.desktop.local.approvals import router as approvals_router
from channels.desktop.local.events import router as events_router
from channels.desktop.local.settings import router as settings_router
from channels.desktop.local.tasks import router as tasks_router

router = APIRouter(prefix="/local")
router.include_router(settings_router)
router.include_router(tasks_router)
router.include_router(events_router)
router.include_router(approvals_router)
