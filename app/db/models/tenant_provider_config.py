from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TenantProviderConfig(Base):
    __tablename__ = "tenant_provider_configs"

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.tenant_id"),
        primary_key=True,
    )

    provider: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    encrypted_credentials: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    default_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    config_json: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )