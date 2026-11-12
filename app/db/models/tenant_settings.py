from sqlalchemy import Boolean, ForeignKey, String, Integer, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TenantSettings(Base):
    __tablename__ = "tenant_settings"

    __table_args__ = (CheckConstraint("generated_artifact_retention_days IS NULL OR generated_artifact_retention_days BETWEEN 1 AND 3650", name="ck_retention_days"),)

    # NULL means follow the storage policy default; explicit choices survive routing changes.
    generated_artifact_retention_enabled: Mapped[bool | None] = mapped_column(Boolean)
    generated_artifact_retention_days: Mapped[int | None] = mapped_column(Integer)

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.tenant_id"),
        primary_key=True,
    )

    allow_self_registration: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    allow_password_authentication: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    allow_google_authentication: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
