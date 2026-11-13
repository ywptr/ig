from fastapi import Cookie, Depends, HTTPException, Request
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

def get_optional_current_user(
    ig_session: str | None = Cookie(
        default=None,
        alias=SESSION_COOKIE_NAME,
    ),
    db: Session = Depends(get_db),
) -> User | None:
    if ig_session is None:
        return None

    session = get_session_by_token(
        db,
        ig_session,
    )

    if session is None or not is_session_active(session):
        return None

    user = get_user_by_id(
        db,
        session.user_id,
    )

    return user

def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(
        get_optional_current_user
    ),
) -> User:
    from app.editor import editor_principal
    editor = editor_principal(request, db)
    if editor is not None:
        return editor.actor_user
    from app.auth.service_access import service_principal
    principal = service_principal(request, db)
    if principal is not None:
        return principal.actor_user
    if current_user is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    return current_user
