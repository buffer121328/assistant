from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Request

from app.support.errors import AppError
from channels.desktop.local.schemas import (
    LocalSettingsValidationRequest,
    LocalSettingsValidationResponse,
)

router = APIRouter()


@router.get("/health")
def local_health(request: Request) -> dict[str, str]:
    """Return local desktop API health."""
    return {
        "service_name": request.app.state.settings.service_name,
        "status": "ok",
    }


@router.get("/config")
def local_config(request: Request) -> dict[str, object]:
    """Return non-secret local desktop runtime configuration."""
    settings = request.app.state.settings
    return {
        "service_name": settings.service_name,
        "app_env": settings.app_env,
        "local_api_auth_required": settings.local_api_auth_required,
        "features": {
            "browser_enabled": settings.browser_enabled,
            "sandbox_provider": settings.effective_sandbox_provider,
            "shell_exec_enabled": settings.effective_shell_exec_enabled,
            "sandbox_enabled": settings.effective_sandbox_provider != "none",
            "subagent_enabled": settings.subagent_enabled,
        },
    }


@router.post("/settings/validate", response_model=LocalSettingsValidationResponse)
def local_validate_settings(
    payload: LocalSettingsValidationRequest,
) -> LocalSettingsValidationResponse:
    """Validate local desktop settings before persisting them in the client."""
    return LocalSettingsValidationResponse(
        ok=True,
        settings={
            "api_base_url": validated_local_api_base_url(payload.api_base_url),
            "default_workdir": validated_workdir(payload.default_workdir),
            "default_model_class": payload.default_model_class,
            "approval_policy": payload.approval_policy,
        },
    )


def validated_local_api_base_url(value: str) -> str:
    """Validate and normalize a localhost-only local API base URL."""
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise AppError(
            "invalid_local_api_base_url",
            "Local API base URL must point to localhost.",
            400,
        )
    path = parsed.path.rstrip("/")
    if path not in {"", "/"}:
        raise AppError(
            "invalid_local_api_base_url",
            "Local API base URL must not include a path.",
            400,
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def validated_workdir(value: str | None) -> str | None:
    """Validate and normalize an existing default working directory."""
    if value is None or not value.strip():
        return None
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AppError(
            "invalid_default_workdir",
            "Default workdir does not exist.",
            400,
        ) from exc
    if not resolved.is_dir():
        raise AppError(
            "invalid_default_workdir",
            "Default workdir must be a directory.",
            400,
        )
    return str(resolved)
