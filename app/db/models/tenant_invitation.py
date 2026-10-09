from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TenantInvitation(Base):
    __tablename__ = "tenant_invitations"

    invitation_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.tenant_id"),
        index=True,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(30),
        default="member",
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
    )

    invited_by_user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.user_id"),
    )

    accepted_by_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.user_id"),
        nullable=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )