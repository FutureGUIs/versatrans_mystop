"""Normalize district API data without exposing credentials or raw student records."""
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from zoneinfo import ZoneInfo


def get(obj, key, default=None):
    if not isinstance(obj, dict):
        return default
    return next((v for k, v in obj.items() if k.lower() == key.lower()), default)


def password_encode(value):
    if any(ord(c) > 127 for c in value):
        raise ValueError("Only ASCII passwords are supported by this initial client")
    state, out = 19937, []
    for char in value:
        byte = ord(char) ^ (state >> 8)
        state = ((byte + state) * 52845 + 22719) & 65535
        out.append(f"{byte:02X}")
    return "".join(out)


@dataclass(frozen=True)
class Position:
    latitude: float
    longitude: float
    timestamp: datetime


def position(record, district_timezone):
    try:
        lat, lon = get(record, "Latitude"), get(record, "Longitude")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) for v in (lat, lon)):
            return None
        if not -90 <= lat <= 90 or not -180 <= lon <= 180 or (lat == 0 and lon == 0):
            return None
        stamp = datetime.fromisoformat(get(record, "LogTime", "").replace("Z", "+00:00"))
        if stamp.year < 2000:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo(district_timezone))
        return Position(lat, lon, stamp.astimezone(timezone.utc))
    except (ValueError, TypeError, AttributeError):
        return None


def latest(records, district_timezone):
    valid = [p for r in records if (p := position(r, district_timezone)) is not None]
    return max(valid, key=lambda p: p.timestamp) if valid else None


def freshness(pos, now, stale_after):
    if pos is None:
        return "no_position"
    age = (now - pos.timestamp).total_seconds()
    if age < -60:
        return "clock_mismatch"
    return "stale" if age > stale_after else "fresh"


@dataclass
class Student:
    record_id: str
    name: str
    vehicle: str | None
    scheduled: str | None
    active: bool | None
    position: Position | None
    status: str
    morning: str | None = None
    afternoon: str | None = None
    polling_status: str = "unknown"
    next_polling_window: datetime | None = None
    schedule_status: str = "unknown"
