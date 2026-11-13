"""Public-key service trust and durable client submission identities."""
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import UTCDateTime, utc_now
from app.db.database import Base


class ServiceCredential(Base):
    __tablename__ = "service_credentials"
    key_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(64), index=True)
    public_key: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scopes: Mapped[list] = mapped_column(JSON)
    tenant_ids: Mapped[list] = mapped_column(JSON)
    actor_issuers: Mapped[list] = mapped_column(JSON)
    editor_origins: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)


class MediaSubmissionKey(Base):
    __tablename__ = "media_submission_keys"
    submission_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    identity_id: Mapped[str] = mapped_column(ForeignKey("user_identities.identity_id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    # Reservation and Job are committed together; nulls exist only mid-transaction.
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.job_id"), unique=True)
    request_id: Mapped[str | None] = mapped_column(String(36))
    media_type: Mapped[str] = mapped_column(String(20))
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
