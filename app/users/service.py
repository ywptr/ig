from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.password import hash_password
from app.db.models import User


def get_user_by_id(
    db: Session,
    user_id: str,
) -> User | None:
    return db.get(User, user_id)


def get_user_by_email(
    db: Session,
    email: str,
) -> User | None:
    return db.scalar(
        select(User).where(
            User.email == email
        )
    )

def get_user_by_google_sub(
    db: Session,
    google_sub: str,
) -> User | None:
    return db.scalar(
        select(User).where(
            User.google_sub == google_sub
        )
    )

def create_user(
    db: Session,
    *,
    email: str,
    password: str,
    name: str | None = None,
) -> User:
    user = User(
        user_id=str(uuid4()),
        email=email,
        name=name,
        password_hash=hash_password(password),
        google_sub=None,
        picture_url=None,
        created_at=datetime.utcnow(),
        last_login_at=None,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user

def create_google_user(
    db: Session,
    *,
    google_sub: str,
    email: str,
    name: str | None = None,
    picture_url: str | None = None,
) -> User:
    user = User(
        user_id=str(uuid4()),
        email=email,
        name=name,
        password_hash=None,
        google_sub=google_sub,
        picture_url=picture_url,
        created_at=datetime.utcnow(),
        last_login_at=None,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user

def create_external_user(
    db: Session,
    *,
    email: str,
    name: str | None = None,
    picture_url: str | None = None,
) -> User:
    """
    Create an IG application principal for a user
    authenticated by an external identity service.

    Authentication identity is stored separately in
    UserIdentity. Email is profile data, not the
    external identity key.
    """

    user = User(
        user_id=str(uuid4()),
        email=email.lower(),
        name=name,
        password_hash=None,
        google_sub=None,
        picture_url=picture_url,
        created_at=datetime.utcnow(),
        last_login_at=None,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user

def update_last_login(
    db: Session,
    user: User,
) -> User:
    user.last_login_at = datetime.utcnow()

    db.commit()
    db.refresh(user)

    return user