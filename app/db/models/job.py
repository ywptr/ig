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


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    job_id: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        index=True,
    )

    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.user_id"),
        index=True,
        nullable=True,
    )

    capability: Mapped[str] = mapped_column(
        String(100),
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        index=True,
    )

    input_json: Mapped[dict] = mapped_column(
        JSON
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )