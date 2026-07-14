from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from packages.capabilities import build_default_registry
from packages.agent_harness import ManagedSkillStore
from packages.observability import Observability

from .config import Settings, load_settings
from .database import create_database_sessionmaker
from .errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    request_validation_error_handler,
)
from .logging import configure_logging
from .observability import build_observability
from .routes import router


def create_app(
    settings: Settings | None = None,
    *,
    observability: Observability | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    logger = configure_logging(settings.log_level)
    runtime_observability = observability or build_observability(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            runtime_observability.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.logger = logger
    app.state.observability = runtime_observability
    app.state.db_sessionmaker = create_database_sessionmaker(settings.database_url)
    builtin_skills_root = Path(__file__).resolve().parents[3] / "prompts" / "skills"
    app.state.managed_skill_store = ManagedSkillStore(
        builtin_root=builtin_skills_root,
        managed_root=settings.managed_skills_root,
    )
    app.state.capability_registry = build_default_registry(
        builtin_skills_root,
        managed_store=app.state.managed_skill_store,
    )
    app.include_router(router)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)

    return app


app = create_app()
