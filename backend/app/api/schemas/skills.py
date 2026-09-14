from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent.skill_management.lifecycle import SkillInventoryItem


class SkillResponse(BaseModel):
    """表示 处理 skill response 的后端数据结构或服务对象。"""

    name: str
    display_name: str
    summary: str
    version: str
    source: Literal["builtin", "managed"]
    enabled: bool
    manageable: bool


class SkillListResponse(BaseModel):
    """表示 处理 skill list response 的后端数据结构或服务对象。"""

    items: list[SkillResponse]


class SkillCreateRequest(BaseModel):
    """表示 处理 skill create request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)
    name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    )
    display_name: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    instructions: str = Field(min_length=1, max_length=131072)


class SkillActorRequest(BaseModel):
    """表示 处理 skill actor request 的后端数据结构或服务对象。"""

    user_id: str = Field(min_length=1)


def skill_response(item: SkillInventoryItem) -> SkillResponse:
    """处理 skill response。

    Args:
        item: item 参数。
    """
    return SkillResponse(
        name=item.name,
        display_name=item.display_name,
        summary=item.summary,
        version=item.version,
        source=item.source,
        enabled=item.enabled,
        manageable=item.manageable,
    )
