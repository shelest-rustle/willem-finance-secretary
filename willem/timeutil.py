from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def period_bounds(period: str, tz_name: str, now: datetime | None = None) -> tuple[str, str]:
    """Границы текущего периода (today/week/month) в локальной tz, возвращённые как UTC ISO 8601."""
    tz = ZoneInfo(tz_name)
    now_local = (now or datetime.now(timezone.utc)).astimezone(tz)

    if period == "today":
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_local = (now_local - timedelta(days=now_local.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif period == "month":
        start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"unknown period: {period}")

    start_utc = start_local.astimezone(timezone.utc).isoformat()
    end_utc = now_local.astimezone(timezone.utc).isoformat()
    return start_utc, end_utc


def format_local_datetime(iso_utc: str, tz_name: str) -> str:
    dt = datetime.fromisoformat(iso_utc).astimezone(ZoneInfo(tz_name))
    return dt.strftime("%d.%m %H:%M")


def format_sheet_datetime(iso_utc: str, tz_name: str) -> str:
    dt = datetime.fromisoformat(iso_utc).astimezone(ZoneInfo(tz_name))
    return dt.strftime("%d.%m.%Y %H:%M")
