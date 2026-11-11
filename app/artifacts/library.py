"""Private, keyset-paginated artifact queries. Cursors are positions, never grants."""
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json

from sqlalchemy import and_, or_, select
from app.db.models import Artifact


class InvalidCursor(ValueError):
    pass


def utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value is not None and value.tzinfo else value


def artifact_page(db, *, tenant_id, user_id, page_size=30, cursor=None, media_type=None,
                  provenance=None, q=None, created_after=None, created_before=None, order="desc"):
    after, before = utc_naive(created_after), utc_naive(created_before)
    q = q.strip() if q else None
    scope = sha256(json.dumps([tenant_id, user_id, media_type, provenance, q,
        after.isoformat() if after else None, before.isoformat() if before else None, order],
        separators=(",", ":")).encode()).hexdigest()
    statement = select(Artifact).where(Artifact.tenant_id == tenant_id, Artifact.user_id == user_id)
    if media_type:
        statement = statement.where(Artifact.media_type == media_type)
    if provenance:
        statement = statement.where(Artifact.provenance == provenance)
    if q:
        statement = statement.where(Artifact.filename.icontains(q, autoescape=True))
    if after:
        statement = statement.where(Artifact.created_at >= after)
    if before:
        statement = statement.where(Artifact.created_at < before)
    if cursor:
        try:
            if len(cursor) > 1024:
                raise ValueError
            position = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if set(position) != {"v", "scope", "time", "id"} or position["v"] != 1 or position["scope"] != scope:
                raise ValueError
            timestamp = datetime.fromisoformat(position["time"])
            if timestamp.tzinfo is not None or not isinstance(position["id"], str) or not 1 <= len(position["id"]) <= 36:
                raise ValueError
            compare_time = Artifact.created_at < timestamp if order == "desc" else Artifact.created_at > timestamp
            compare_id = Artifact.artifact_id < position["id"] if order == "desc" else Artifact.artifact_id > position["id"]
            statement = statement.where(or_(compare_time, and_(Artifact.created_at == timestamp, compare_id)))
        except (ValueError, TypeError, KeyError, OverflowError):
            raise InvalidCursor("Invalid artifact cursor") from None
    ordering = [column.desc() if order == "desc" else column.asc() for column in (Artifact.created_at, Artifact.artifact_id)]
    rows = list(db.scalars(statement.order_by(*ordering).limit(page_size + 1)))
    has_more = len(rows) > page_size
    rows = rows[:page_size]
    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps({"v": 1, "scope": scope,
            "time": last.created_at.isoformat(), "id": last.artifact_id}, separators=(",", ":")).encode()).decode().rstrip("=")
    return rows, next_cursor, has_more
