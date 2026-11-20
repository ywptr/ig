from datetime import datetime

from sqlalchemy import ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import UTCDateTime, utc_now
from app.db.database import Base


class MediaWorkflow(Base):
    __tablename__ = "media_workflows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "submission_key", name="uq_media_workflow_submission"),
        Index("ix_media_workflow_due", "next_check_at", "workflow_id"),
    )

    workflow_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    submission_key: Mapped[str | None] = mapped_column(String(128))
    submission_hash: Mapped[str] = mapped_column(String(64))
    definition: Mapped[dict] = mapped_column(JSON)
    steps: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    next_check_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
