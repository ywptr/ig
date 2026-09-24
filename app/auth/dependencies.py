from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.session import (
    get_session_by_token,
    is_session_active,
)
from app.db.database import SessionLocal
from app.db.models import User
from app.users.service import get_user_by_id


SESSION_COOKIE_NAME = "ig_session"


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def get_current_user(
    ig_session: str | None = Cookie(
        default=None,
        alias=SESSION_COOKIE_NAME,
    ),
    db: Session = Depends(get_db),
) -> User:
    if ig_session is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    session = get_session_by_token(
        db,
        ig_session,
    )

    if session is None or not is_session_active(session):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session",
        )

    user = get_user_by_id(
        db,
        session.user_id,
    )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="User not found",
        )

    return user