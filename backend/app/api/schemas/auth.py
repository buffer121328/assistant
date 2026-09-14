from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LocalInitializationStatusResponse(BaseModel):
    """LocalInitializationStatusResponse 的接口数据模型。"""
    initialization_required: bool


class LocalInitializeRequest(BaseModel):
    """LocalInitializeRequest 的接口数据模型。"""
    display_name: str = Field(min_length=1, max_length=120)
    login_name: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=128)


class LocalLoginRequest(BaseModel):
    """LocalLoginRequest 的接口数据模型。"""
    login_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LocalRecoveryStatusResponse(BaseModel):
    enabled: bool
    question: str | None = None


class LocalRecoverPasswordRequest(BaseModel):
    login_name: str = Field(min_length=1, max_length=64)
    answer: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=128)


class LocalSessionResponse(BaseModel):
    """LocalSessionResponse 的接口数据模型。"""
    session_token: str | None = None
    expires_at: datetime
    tenant_id: str
    user_id: str
    display_name: str
    organization_ids: list[str]
    organization_names: list[str] = []
    roles: list[str]
    authority_revision: int
