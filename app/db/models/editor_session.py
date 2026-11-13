"""Bounded browser delegation, never a general service/browser login."""
from datetime import datetime
from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.time import UTCDateTime, utc_now
from app.db.database import Base


class EditorSession(Base):
    __tablename__ = "editor_sessions"
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    credential_id: Mapped[str] = mapped_column(ForeignKey("service_credentials.key_id"))
    service_id: Mapped[str] = mapped_column(String(64))
    identity_id: Mapped[str] = mapped_column(ForeignKey("user_identities.identity_id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    correlation_id: Mapped[str] = mapped_column(String(128))
    parent_origin: Mapped[str] = mapped_column(String(512))
    launch_hash: Mapped[str | None] = mapped_column(String(64))
    access_hash: Mapped[str | None] = mapped_column(String(64))
    draft: Mapped[dict] = mapped_column(JSON)
    policy: Mapped[dict] = mapped_column(JSON)
    callback_context: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    launch_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class EditorAttempt(Base):
    __tablename__ = "editor_attempts"
    session_id: Mapped[str] = mapped_column(ForeignKey("editor_sessions.session_id"), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), unique=True)
    request_id: Mapped[str] = mapped_column(String(36))
