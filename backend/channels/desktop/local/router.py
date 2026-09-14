from __future__ import annotations

from fastapi import APIRouter

from channels.desktop.local.approvals import router as approvals_router
from channels.desktop.local.conversations import router as conversations_router
from channels.desktop.local.commands import router as commands_router
from channels.desktop.local.artifacts import router as artifacts_router
from channels.desktop.local.events import router as events_router
from channels.desktop.local.resources import router as resources_router
from channels.desktop.local.settings import router as settings_router
from channels.desktop.local.tasks import router as tasks_router
from channels.desktop.local.workspace_files import router as workspace_files_router

router = APIRouter(prefix="/local")
router.include_router(settings_router)
router.include_router(conversations_router)
router.include_router(commands_router)
router.include_router(tasks_router)
router.include_router(events_router)
router.include_router(approvals_router)
router.include_router(resources_router)
router.include_router(artifacts_router)
router.include_router(workspace_files_router)
