from sqlalchemy.orm import Session

from app.db.models import TenantSettings


def get_tenant_settings(
    db: Session,
    tenant_id: str,
) -> TenantSettings | None:
    return db.get(
        TenantSettings,
        tenant_id,
    )


def update_tenant_settings(
    db: Session,
    settings: TenantSettings,
    *,
    allow_self_registration: bool,
) -> TenantSettings:
    settings.allow_self_registration = (
        allow_self_registration
    )

    db.commit()
    db.refresh(settings)

    return settings