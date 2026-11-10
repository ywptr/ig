"""Mechanical derivatives, never creative/user-owned Artifact rows."""
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import UTCDateTime, utc_now
from app.db.database import Base


class ArtifactPreview(Base):
    __tablename__ = "artifact_previews"
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20))
    claim_id: Mapped[str] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    storage_uri: Mapped[str | None] = mapped_column(String(2048))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String(64))
    frame_count: Mapped[int | None] = mapped_column(Integer)
    positions: Mapped[list | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
