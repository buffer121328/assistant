from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, load_settings
from .database import create_database_sessionmaker
from .errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    request_validation_error_handler,
)
from .logging import configure_logging
from .routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logger = configure_logging(settings.log_level)

    app = FastAPI()
    app.state.settings = settings
    app.state.logger = logger
    app.state.db_sessionmaker = create_database_sessionmaker(settings.database_url)
    app.include_router(router)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    return app


app = create_app()
