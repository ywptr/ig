from datetime import datetime

from sqlalchemy import BigInteger, DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

from app.db.database import Base

# ImageRequest model represents a request to generate an image using OpenAI's image generation service.
class ImageRequest(Base):
    __tablename__ = "image_requests"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    request_id: Mapped[str] = mapped_column(
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )

    prompt: Mapped[str] = mapped_column(Text)

    model: Mapped[str] = mapped_column(
        String(100)
    )

    status: Mapped[str] = mapped_column(
        String(30)
    )

    filename: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    mime_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    artifact_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("artifacts.artifact_id"),
        index=True,
        nullable=True,
    )

    generation_time_ms: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )