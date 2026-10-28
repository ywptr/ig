from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class UserIdentity(Base):
    """
    Maps an externally authenticated identity to
    an existing IG user.

    The durable external identity key is:

        (issuer, subject)

    Email is deliberately not used as the identity key.
    """

    __tablename__ = "user_identities"

    identity_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.user_id"),
        index=True,
        nullable=False,
    )

    issuer: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    subject: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "issuer",
            "subject",
            name="uq_user_identities_issuer_subject",
        ),
    )