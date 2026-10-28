from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    User,
    UserIdentity,
)


def get_user_by_external_identity(
    db: Session,
    *,
    issuer: str,
    subject: str,
) -> User | None:
    statement = (
        select(User)
        .join(
            UserIdentity,
            UserIdentity.user_id
            == User.user_id,
        )
        .where(
            UserIdentity.issuer == issuer,
            UserIdentity.subject == subject,
        )
    )

    return db.scalar(statement)


def create_user_identity(
    db: Session,
    *,
    user_id: str,
    issuer: str,
    subject: str,
) -> UserIdentity:
    identity = UserIdentity(
        identity_id=str(uuid4()),
        user_id=user_id,
        issuer=issuer,
        subject=subject,
    )

    db.add(identity)
    db.commit()
    db.refresh(identity)

    return identity