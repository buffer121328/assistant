from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _as_utc(value: datetime) -> datetime:
    """执行 处理 as utc 的内部辅助逻辑。

    Args:
        value: value 参数。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _timezone(name: str) -> ZoneInfo:
    """执行 处理 timezone 的内部辅助逻辑。

    Args:
        name: name 参数。
    """
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc


def _next_cron_time(expr: str, after: datetime, timezone: ZoneInfo) -> datetime:
    """执行 处理 next cron time 的内部辅助逻辑。

    Args:
        expr: expr 参数。
        after: after 参数。
        timezone: timezone 参数。
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron_expr must contain five fields")
    minute_s, hour_s, day_s, month_s, weekday_s = fields
    current = _as_utc(after).astimezone(timezone).replace(second=0, microsecond=0)
    current += timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if (
            _cron_match(minute_s, current.minute)
            and _cron_match(hour_s, current.hour)
            and _cron_match(day_s, current.day)
            and _cron_match(month_s, current.month)
            and _cron_match(weekday_s, (current.weekday() + 1) % 7)
        ):
            return current.astimezone(UTC)
        current += timedelta(minutes=1)
    raise ValueError("cron_expr has no next run within one year")


def _cron_match(field: str, value: int) -> bool:
    """执行 处理 cron match 的内部辅助逻辑。

    Args:
        field: field 参数。
        value: value 参数。
    """
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and value % step == 0
    return any(part.isdigit() and int(part) == value for part in field.split(","))
