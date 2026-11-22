"""Serialize this project's legacy UTC-naive storage without changing its instant."""
from datetime import datetime, timezone


def utc_iso(value: datetime) -> str:
    instant = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return instant.astimezone(timezone.utc).isoformat()
