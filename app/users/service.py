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

def update_last_login(
    db: Session,
    user: User,
) -> User:
    user.last_login_at = datetime.utcnow()

    db.commit()
    db.refresh(user)

    return user