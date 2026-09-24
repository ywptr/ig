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

# Execution represents an execution of a job, which may have multiple attempts.
class Execution(Base):
    __tablename__ = "executions"

    execution_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("jobs.job_id"),
        index=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.user_id"),
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        index=True,
    )

    attempt: Mapped[int] = mapped_column(
        default=1,
    )

    provider: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    metadata_json: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )