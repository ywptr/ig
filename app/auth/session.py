from datetime import datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserSession


SESSION_TTL_DAYS = 30


def _hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def create_session(
    db: Session,
    *,
    user_id: str,
) -> tuple[UserSession, str]:
    token = token_urlsafe(32)

    session = UserSession(
        session_id=str(uuid4()),
        user_id=user_id,
        token_hash=_hash_token(token),
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow()
        + timedelta(days=SESSION_TTL_DAYS),
        revoked_at=None,
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    return session, token


def get_session_by_token(
    db: Session,
    token: str,
) -> UserSession | None:
    token_hash = _hash_token(token)

    return db.scalar(
        select(UserSession).where(
            UserSession.token_hash == token_hash
        )
    )


def revoke_session(
    db: Session,
    session: UserSession,
) -> None:
    session.revoked_at = datetime.utcnow()

    db.commit()


def is_session_active(
    session: UserSession,
) -> bool:
    now = datetime.utcnow()

    return (
        session.revoked_at is None
        and session.expires_at > now
    )