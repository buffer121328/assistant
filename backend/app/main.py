from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from capabilities import build_default_registry
from agent import ManagedSkillStore
from observability import Observability
from integrations import DefaultConnectionTester

from infrastructure.config import Settings, load_settings
from infrastructure.auth import LocalApiAuthMiddleware
from infrastructure.database import create_database_sessionmaker
from app.support.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    request_validation_error_handler,
)
from infrastructure.logging import configure_logging
from infrastructure.observability import build_observability
from app.api.router import router


def create_app(
    settings: Settings | None = None,
    *,
    observability: Observability | None = None,
) -> FastAPI:
    """创建 app。

    Args:
        settings: settings 参数。
        observability: observability 参数。
    """
    settings = settings or load_settings()
    logger = configure_logging(settings.log_level)
    runtime_observability = observability or build_observability(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """处理 lifespan。

        Args:
            _app: _app 参数。
        """
        try:
            yield
        finally:
            runtime_observability.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.logger = logger
    app.state.observability = runtime_observability
    app.state.db_sessionmaker = create_database_sessionmaker(settings.database_url)
    app.state.connection_tester = DefaultConnectionTester()
    builtin_skills_root = (
        Path(__file__).resolve().parents[1] / "resources" / "skillpacks"
    )
    app.state.managed_skill_store = ManagedSkillStore(
        builtin_root=builtin_skills_root,
        managed_root=settings.managed_skills_root,
    )
    app.state.capability_registry = build_default_registry(
        builtin_skills_root,
        managed_store=app.state.managed_skill_store,
    )
    app.add_middleware(LocalApiAuthMiddleware)
    app.include_router(router)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)

    return app


app = create_app()
