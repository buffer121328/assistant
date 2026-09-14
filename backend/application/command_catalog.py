from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import User
from domain.policies.enterprise import GovernanceValidationError, LOCAL_TENANT_ID
from features import CORE_FEATURES, FEATURE_COMMANDS, feature_for_command


@dataclass(frozen=True)
class OfficeCommandDescriptor:
    """定义当前组件的职责和边界。"""

    command_id: str  # command_id 对应的数据字段。
    label: str  # label 对应的数据字段。
    description: str  # description 对应的数据字段。
    task_type: str  # task_type 对应的数据字段。
    selectable: bool = True  # selectable 对应的数据字段。


_COMMAND_COPY: dict[str, tuple[str, str]] = {
    "plan": ("制定计划", "拆解目标并给出可执行的下一步"),
    "learn": ("调研并学习", "检索、核对并提炼可靠结论"),
    "daily": ("生成日报", "整理当日线索并输出工作摘要"),
    "office": ("办公写作", "根据材料起草办公文档或消息"),
    "memory": ("管理记忆", "查看或更新你的工作记忆"),
    "status": ("查看任务状态", "查看任务进度、事件和结果"),
    "screen": ("获取屏幕截图", "执行受控截图并登记为产物"),
}


class CommandCatalogError(ValueError):
    """定义当前组件可安全处理的错误类型。"""

    def __init__(self, code: str, status_code: int = 400) -> None:
        """初始化对象所需的运行时依赖和受限状态。

        Args:
            code: 可安全返回给调用方的错误码。
            status_code: 可安全返回给调用方的 HTTP 状态码。
        """
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def parse_task_type(text: str) -> str | None:
    """Resolve a leading slash command to its server-owned task type.

    Args:
        text: 需要处理的文本内容。
    """
    normalized = text.strip()
    if not normalized:
        return None
    command = normalized.split(maxsplit=1)[0]
    return FEATURE_COMMANDS.get(command)


def _descriptor(task_type: str) -> OfficeCommandDescriptor:
    """执行 descriptor 的内部处理逻辑。

    Args:
        task_type: 用于执行当前操作的 task type 参数。
    """
    feature = next(
        (item for item in CORE_FEATURES if item.task_type == task_type),
        None,
    )
    if feature is None:
        raise CommandCatalogError("unknown_command", 400)
    label, description = _COMMAND_COPY.get(
        task_type, (task_type.replace("_", " ").title(), "可用的办公动作")
    )
    return OfficeCommandDescriptor(
        command_id=task_type,
        label=label,
        description=description,
        task_type=task_type,
    )


def list_office_commands() -> tuple[OfficeCommandDescriptor, ...]:
    """执行当前组件定义的业务处理逻辑。"""
    return tuple(_descriptor(feature.task_type) for feature in CORE_FEATURES)


async def list_office_commands_for_user(
    session: AsyncSession, user_id: str
) -> tuple[OfficeCommandDescriptor, ...]:
    """Return a bounded catalog after resolving the current user's authority.

    Args:
        session: 当前数据库异步会话。
        user_id: 目标用户 ID。
    """
    user = await session.get(User, user_id)
    if user is None:
        raise CommandCatalogError("user_not_found", 404)
    descriptors = list_office_commands()
    if user.tenant_id == LOCAL_TENANT_ID:
        return descriptors

    from application.enterprise_governance import GovernedAgentProfileResolver

    try:
        profile = await GovernedAgentProfileResolver(session).resolve_for_user(user_id)
    except GovernanceValidationError as exc:
        # Keep governance failures bounded at the catalog boundary.
        raise CommandCatalogError("command_catalog_unavailable", 403) from exc
    tools = set(profile.tools)
    result: list[OfficeCommandDescriptor] = []
    for descriptor in descriptors:
        feature = feature_for_command(f"/{descriptor.command_id}")
        requested_tools = feature.requested_tools if feature is not None else ()
        selectable = bool(profile.capabilities) and (
            not requested_tools or bool(tools.intersection(requested_tools))
        )
        result.append(
            OfficeCommandDescriptor(
                command_id=descriptor.command_id,
                label=descriptor.label,
                description=descriptor.description,
                task_type=descriptor.task_type,
                selectable=selectable,
            )
        )
    return tuple(result)


async def resolve_office_command_for_user(
    session: AsyncSession, user_id: str, command_id: str
) -> OfficeCommandDescriptor:
    """Resolve a command and re-check that it is selectable for this user.

    Args:
        session: 当前数据库异步会话。
        user_id: 目标用户 ID。
        command_id: 用于执行当前操作的 command id 参数。
    """
    descriptor = resolve_office_command(command_id)
    catalog = await list_office_commands_for_user(session, user_id)
    selected = next(
        (item for item in catalog if item.command_id == descriptor.command_id),
        None,
    )
    if selected is None or not selected.selectable:
        raise CommandCatalogError("command_unavailable", 403)
    return selected


def resolve_office_command(command_id: str) -> OfficeCommandDescriptor:
    """Resolve one stable command ID from the server-owned catalog.

    Args:
        command_id: 用于执行当前操作的 command id 参数。
    """
    normalized = command_id.strip().lower()
    if not normalized:
        raise CommandCatalogError("command_required", 400)
    for descriptor in list_office_commands():
        if descriptor.command_id == normalized:
            return descriptor
    raise CommandCatalogError("unknown_command", 400)
