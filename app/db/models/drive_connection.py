"""User-owned storage authorization, independent of login identities."""
from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.time import UTCDateTime, utc_now
from app.db.database import Base
from datetime import datetime


class DriveConnection(Base):
    __tablename__ = "drive_connections"
    __table_args__ = (Index("uq_drive_active_user", "user_id", unique=True,
                           postgresql_where=text("active = true"), sqlite_where=text("active = 1")),)
    connection_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    account_id: Mapped[str] = mapped_column(String(255))  # Drive permissionId, not IS subject/email.
    account_email: Mapped[str | None] = mapped_column(String(320))
    account_name: Mapped[str | None] = mapped_column(String(255))
    folder_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="healthy")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, onupdate=utc_now)
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DriveOAuthState(Base):
    __tablename__ = "drive_oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    session_hash: Mapped[str] = mapped_column(String(64))
    browser_hash: Mapped[str] = mapped_column(String(64))
    encrypted_verifier: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(32), default="connect_drive")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
