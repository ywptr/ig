from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


# User represents an authenticated IG user.
# Authentication providers are external; IG owns the user identity.
class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    google_sub: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=True,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        index=True,
    )

    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    picture_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )