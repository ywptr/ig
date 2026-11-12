from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Enum,
    Float,
    Integer,
    Index,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import UTCDateTime
from app.db.database import Base
from app.artifacts.schemas import ArtifactProvenance


# Artifact represents a media artifact (e.g., image, video, audio) associated with a user.
class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_library_order", "tenant_id", "user_id", "created_at", "artifact_id"),
                      Index("ix_artifacts_retention", "provenance", "content_removed_at", "created_at"))

    cleanup_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    content_removed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    content_removal_reason: Mapped[str | None] = mapped_column(String(30))

    provenance: Mapped[ArtifactProvenance] = mapped_column(
        Enum(ArtifactProvenance, native_enum=False, values_callable=lambda enum: [value.value for value in enum],
             validate_strings=True), default=ArtifactProvenance.UNKNOWN, server_default="unknown")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    fps: Mapped[float | None] = mapped_column(Float)
    sha256: Mapped[str | None] = mapped_column(String(64))

    artifact_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.tenant_id"),
        index=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.user_id"),
        index=True,
    )

    media_type: Mapped[str] = mapped_column(
        String(50),
        index=True,
    )

    mime_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    storage_uri: Mapped[str] = mapped_column(
        String(2048),
    )

    filename: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    parent_artifact_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("artifacts.artifact_id"),
        index=True,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )