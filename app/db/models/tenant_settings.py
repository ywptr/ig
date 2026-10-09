from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class TenantSettings(Base):
    __tablename__ = "tenant_settings"

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