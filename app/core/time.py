"""UTC instants at Python/API boundaries; legacy SQL timestamps remain UTC-naive."""
from datetime import datetime, timezone
from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def utc_iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value is not None else None


class UTCDateTime(TypeDecorator):
    """Compatible with existing timestamp-without-time-zone columns.

    Existing writers used utcnow(). Interpret those values as UTC, never the
    database/browser local zone. Aware inputs are normalized before persistence.
    """
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return as_utc(value).replace(tzinfo=None) if value is not None else None

    def process_result_value(self, value, dialect):
        return as_utc(value) if value is not None else None
