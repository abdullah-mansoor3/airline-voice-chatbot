from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Karachi"
# Pakistan Standard Time is UTC+5 year-round (no DST).
_PKT = timezone(timedelta(hours=5))


def _resolve_timezone(name: str) -> tuple[str, timezone]:
    try:
        return name, ZoneInfo(name)
    except Exception:
        if name in {DEFAULT_TIMEZONE, "Asia/Karachi", "PKT"}:
            return DEFAULT_TIMEZONE, _PKT
        return "UTC", timezone.utc


async def get_current_datetime(*, timezone: str = DEFAULT_TIMEZONE) -> dict[str, Any]:
    """Return the current date and time for the requested timezone."""
    label, tz = _resolve_timezone(timezone)
    now = datetime.now(tz)
    return {
        "timezone": label,
        "iso": now.isoformat(),
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "unix_timestamp": int(now.timestamp()),
    }
