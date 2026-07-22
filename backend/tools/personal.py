from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

from .artifacts import ProductivityTools
from .browser import PlaywrightBrowserReader
from .catalog import ToolDescriptor
from .providers import CalendarProvider, EmailProvider
from .approval import external_approval_binding
from .registry import ToolHandler, ToolInvocation, ToolRiskLevel, ToolSpec
from .sandbox import SandboxRunner


def build_personal_tool_descriptors(
    *,
    browser_available: bool,
    sandbox_available: bool,
    email_provider_available: bool = False,
    calendar_provider_available: bool = False,
) -> tuple[ToolDescriptor, ...]:
    """构建 personal tool descriptors。

    Args:
        browser_available: browser_available 参数。
        sandbox_available: sandbox_available 参数。
        email_provider_available: email_provider_available 参数。
        calendar_provider_available: calendar_provider_available 参数。
    """
    descriptors = [
        _descriptor(
            "email.draft",
            "Create a local RFC 822 email draft",
            _email_schema(),
            tags=("plan", "daily", "office"),
            parallel_safe=True,
        ),
        _descriptor(
            "calendar.create_event",
            "Create a local iCalendar event",
            _calendar_schema(),
            tags=("plan", "daily", "office"),
            parallel_safe=True,
        ),
        _descriptor(
            "office.create_docx",
            "Create a Word document artifact",
            _docx_schema(),
            tags=("office",),
            parallel_safe=True,
        ),
        _descriptor(
            "office.create_xlsx",
            "Create an Excel workbook artifact",
            _xlsx_schema(),
            tags=("office",),
            parallel_safe=True,
        ),
        _descriptor(
            "office.create_pptx",
            "Create a PowerPoint presentation artifact",
            _pptx_schema(),
            tags=("office",),
            parallel_safe=True,
        ),
        _descriptor(
            "browser.read",
            "Read bounded text from one public web page",
            _browser_schema(),
            tags=("learn", "daily"),
            enabled=browser_available,
        ),
        _descriptor(
            "shell.exec",
            "Execute an argv command in an isolated Docker container",
            _shell_schema(),
            tags=("office",),
            enabled=sandbox_available,
            risk_level="L3",
            requires_approval=True,
        ),
    ]
    if email_provider_available:
        descriptors.append(
            _descriptor(
                "email.send",
                "Send email through an explicitly configured provider",
                _send_email_schema(),
                tags=("daily", "office"),
                risk_level="L3",
                requires_approval=True,
            )
        )
    if calendar_provider_available:
        descriptors.append(
            _descriptor(
                "calendar.sync_event",
                "Create an event through an explicitly configured provider",
                _calendar_provider_schema(),
                tags=("daily", "office"),
                risk_level="L3",
                requires_approval=True,
            )
        )
    return tuple(descriptors)


def build_personal_tool_specs(
    *,
    productivity: ProductivityTools,
    browser: PlaywrightBrowserReader | None = None,
    sandbox: SandboxRunner | None = None,
    email_provider: EmailProvider | None = None,
    calendar_provider: CalendarProvider | None = None,
) -> tuple[ToolSpec, ...]:
    """构建 personal tool specs。

    Args:
        productivity: productivity 参数。
        browser: browser 参数。
        sandbox: sandbox 参数。
        email_provider: email_provider 参数。
        calendar_provider: calendar_provider 参数。
    """

    async def email_draft(invocation: ToolInvocation) -> Any:
        """处理 email draft。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        artifact = await asyncio.to_thread(
            productivity.create_email_draft,
            task_id=invocation.task_id,
            filename=str(args["filename"]),
            subject=str(args["subject"]),
            body=str(args["body"]),
            to=tuple(str(item) for item in args.get("to", ())),
        )
        return asdict(artifact)

    async def calendar_event(invocation: ToolInvocation) -> Any:
        """处理 calendar event。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        artifact = await asyncio.to_thread(
            productivity.create_calendar_event,
            task_id=invocation.task_id,
            filename=str(args["filename"]),
            title=str(args["title"]),
            start=str(args["start"]),
            end=str(args["end"]),
            description=str(args.get("description", "")),
        )
        return asdict(artifact)

    async def create_docx(invocation: ToolInvocation) -> Any:
        """创建 docx。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        artifact = await asyncio.to_thread(
            productivity.create_docx,
            task_id=invocation.task_id,
            filename=str(args["filename"]),
            title=str(args["title"]),
            paragraphs=tuple(str(item) for item in args.get("paragraphs", ())),
        )
        return asdict(artifact)

    async def create_xlsx(invocation: ToolInvocation) -> Any:
        """创建 xlsx。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        artifact = await asyncio.to_thread(
            productivity.create_xlsx,
            task_id=invocation.task_id,
            filename=str(args["filename"]),
            sheet_name=str(args["sheet_name"]),
            rows=tuple(tuple(row) for row in args.get("rows", ())),
        )
        return asdict(artifact)

    async def create_pptx(invocation: ToolInvocation) -> Any:
        """创建 pptx。

        Args:
            invocation: invocation 参数。
        """
        args = invocation.arguments
        slides = tuple(
            (
                str(item["title"]),
                tuple(str(bullet) for bullet in item.get("bullets", ())),
            )
            for item in args.get("slides", ())
        )
        artifact = await asyncio.to_thread(
            productivity.create_pptx,
            task_id=invocation.task_id,
            filename=str(args["filename"]),
            title=str(args["title"]),
            slides=slides,
        )
        return asdict(artifact)

    specs = [
        _spec(
            "email.draft",
            "Create a local RFC 822 email draft",
            email_draft,
            _email_schema(),
            parallel_safe=True,
        ),
        _spec(
            "calendar.create_event",
            "Create a local iCalendar event",
            calendar_event,
            _calendar_schema(),
            parallel_safe=True,
        ),
        _spec(
            "office.create_docx",
            "Create a Word document artifact",
            create_docx,
            _docx_schema(),
            parallel_safe=True,
        ),
        _spec(
            "office.create_xlsx",
            "Create an Excel workbook artifact",
            create_xlsx,
            _xlsx_schema(),
            parallel_safe=True,
        ),
        _spec(
            "office.create_pptx",
            "Create a PowerPoint presentation artifact",
            create_pptx,
            _pptx_schema(),
            parallel_safe=True,
        ),
    ]
    if browser is not None:

        async def browser_read(invocation: ToolInvocation) -> Any:
            """处理 browser read。

            Args:
                invocation: invocation 参数。
            """
            return asdict(await browser.read(str(invocation.arguments["url"])))

        specs.append(
            _spec(
                "browser.read",
                "Read bounded text from one public web page",
                browser_read,
                _browser_schema(),
            )
        )
    if sandbox is not None and sandbox.available:

        async def shell_exec(invocation: ToolInvocation) -> Any:
            """处理 shell exec。

            Args:
                invocation: invocation 参数。
            """
            command = tuple(str(item) for item in invocation.arguments["command"])
            return asdict(
                await sandbox.execute(task_id=invocation.task_id, command=command)
            )

        specs.append(
            _spec(
                "shell.exec",
                "Execute an argv command in an isolated Docker container",
                shell_exec,
                _shell_schema(),
                risk_level="L3",
            )
        )
    if email_provider is not None:

        async def email_send(invocation: ToolInvocation) -> Any:
            """处理 email send。

            Args:
                invocation: invocation 参数。
            """
            args = invocation.arguments
            provider_id = await email_provider.send(
                user_id=invocation.user_id,
                connection_id=str(args["connection_id"]),
                recipients=tuple(str(item) for item in args["to"]),
                subject=str(args["subject"]),
                body=str(args["body"]),
            )
            return {"provider_id": provider_id}

        specs.append(
            _spec(
                "email.send",
                "Send email through an explicitly configured provider",
                email_send,
                _send_email_schema(),
                risk_level="L3",
            )
        )
    if calendar_provider is not None:

        async def calendar_sync(invocation: ToolInvocation) -> Any:
            """处理 calendar sync。

            Args:
                invocation: invocation 参数。
            """
            args = invocation.arguments
            provider_id = await calendar_provider.create_event(
                user_id=invocation.user_id,
                connection_id=str(args["connection_id"]),
                title=str(args["title"]),
                start=str(args["start"]),
                end=str(args["end"]),
                description=str(args.get("description", "")),
                idempotency_key=(
                    f"{invocation.task_id}:"
                    f"{external_approval_binding(invocation.name, args).fingerprint}"
                ),
            )
            return {"provider_id": provider_id}

        specs.append(
            _spec(
                "calendar.sync_event",
                "Create an event through an explicitly configured provider",
                calendar_sync,
                _calendar_provider_schema(),
                risk_level="L3",
            )
        )
    return tuple(specs)


def _descriptor(
    name: str,
    description: str,
    schema: dict[str, Any],
    *,
    tags: tuple[str, ...],
    enabled: bool = True,
    risk_level: ToolRiskLevel = "L1",
    requires_approval: bool = False,
    parallel_safe: bool = False,
) -> ToolDescriptor:
    """执行 处理 descriptor 的内部辅助逻辑。

    Args:
        name: name 参数。
        description: description 参数。
        schema: schema 参数。
        tags: tags 参数。
        enabled: enabled 参数。
        risk_level: risk_level 参数。
        requires_approval: requires_approval 参数。
        parallel_safe: parallel_safe 参数。
    """
    return ToolDescriptor(
        name=name,
        description=description,
        input_schema=schema,
        source_id="builtin",
        source_kind="builtin",
        version="personal-v2",
        enabled=enabled,
        risk_level=risk_level,
        requires_approval=requires_approval,
        tags=tags,
        parallel_safe=parallel_safe,
    )


def _spec(
    name: str,
    description: str,
    handler: ToolHandler,
    schema: dict[str, Any],
    *,
    risk_level: ToolRiskLevel = "L1",
    parallel_safe: bool = False,
) -> ToolSpec:
    """执行 处理 spec 的内部辅助逻辑。

    Args:
        name: name 参数。
        description: description 参数。
        handler: handler 参数。
        schema: schema 参数。
        risk_level: risk_level 参数。
        parallel_safe: parallel_safe 参数。
    """
    return ToolSpec(
        name=name,
        description=description,
        risk_level=risk_level,
        handler=handler,
        input_schema=schema,
        version="personal-v2",
        parallel_safe=parallel_safe,
    )


def _object(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    """执行 处理 object 的内部辅助逻辑。

    Args:
        properties: properties 参数。
        required: required 参数。
    """
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _text(max_length: int = 10_000, *, allow_empty: bool = False) -> dict[str, Any]:
    """执行 处理 text 的内部辅助逻辑。

    Args:
        max_length: max_length 参数。
        allow_empty: allow_empty 参数。
    """
    return {
        "type": "string",
        "minLength": 0 if allow_empty else 1,
        "maxLength": max_length,
    }


def _email_schema() -> dict[str, Any]:
    """执行 处理 email schema 的内部辅助逻辑。"""
    return _object(
        {
            "filename": _text(128),
            "subject": _text(300),
            "body": _text(100_000),
            "to": {"type": "array", "items": _text(320), "maxItems": 20},
        },
        ("filename", "subject", "body"),
    )


def _send_email_schema() -> dict[str, Any]:
    """执行 处理 send email schema 的内部辅助逻辑。"""
    return _object(
        {
            "connection_id": _text(36),
            "to": {"type": "array", "items": _text(320), "minItems": 1, "maxItems": 20},
            "subject": _text(300),
            "body": _text(100_000),
        },
        ("connection_id", "to", "subject", "body"),
    )


def _calendar_fields(*, include_filename: bool) -> dict[str, Any]:
    """执行 处理 calendar fields 的内部辅助逻辑。

    Args:
        include_filename: include_filename 参数。
    """
    date_time = {"type": "string", "format": "date-time", "maxLength": 64}
    fields: dict[str, Any] = {
        "title": _text(500),
        "start": date_time,
        "end": date_time,
        "description": _text(10_000, allow_empty=True),
    }
    if include_filename:
        fields = {"filename": _text(128), **fields}
    return fields


def _calendar_schema() -> dict[str, Any]:
    """执行 处理 calendar schema 的内部辅助逻辑。"""
    return _object(
        _calendar_fields(include_filename=True), ("filename", "title", "start", "end")
    )


def _calendar_provider_schema() -> dict[str, Any]:
    """执行 处理 calendar provider schema 的内部辅助逻辑。"""
    return _object(
        {"connection_id": _text(36), **_calendar_fields(include_filename=False)},
        ("connection_id", "title", "start", "end"),
    )


def _docx_schema() -> dict[str, Any]:
    """执行 处理 docx schema 的内部辅助逻辑。"""
    return _object(
        {
            "filename": _text(128),
            "title": _text(500),
            "paragraphs": {"type": "array", "items": _text(), "maxItems": 100},
        },
        ("filename", "title", "paragraphs"),
    )


def _xlsx_schema() -> dict[str, Any]:
    """执行 处理 xlsx schema 的内部辅助逻辑。"""
    return _object(
        {
            "filename": _text(128),
            "sheet_name": _text(31),
            "rows": {
                "type": "array",
                "items": {"type": "array", "maxItems": 100},
                "maxItems": 10_000,
            },
        },
        ("filename", "sheet_name", "rows"),
    )


def _pptx_schema() -> dict[str, Any]:
    """执行 处理 pptx schema 的内部辅助逻辑。"""
    slide = _object(
        {
            "title": _text(500),
            "bullets": {"type": "array", "items": _text(2_000), "maxItems": 50},
        },
        ("title", "bullets"),
    )
    return _object(
        {
            "filename": _text(128),
            "title": _text(500),
            "slides": {"type": "array", "maxItems": 50, "items": slide},
        },
        ("filename", "title", "slides"),
    )


def _browser_schema() -> dict[str, Any]:
    """执行 处理 browser schema 的内部辅助逻辑。"""
    return _object({"url": _text(2_048)}, ("url",))


def _shell_schema() -> dict[str, Any]:
    """执行 处理 shell schema 的内部辅助逻辑。"""
    return _object(
        {
            "command": {
                "type": "array",
                "items": _text(4_096),
                "minItems": 1,
                "maxItems": 64,
            }
        },
        ("command",),
    )
