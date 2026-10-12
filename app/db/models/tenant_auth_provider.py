from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TenantAuthProvider(Base):
    __tablename__ = "tenant_auth_providers"

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.tenant_id"),
        primary_key=True,
    )

    provider: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
    )

    client_id: Mapped[str] = mapped_column(
        String(512),
    )

    issuer: Mapped[str | None] = mapped_column(
        String(2048),
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