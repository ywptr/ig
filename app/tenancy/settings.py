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
    allow_password_authentication: bool,
    allow_google_authentication: bool,
) -> TenantSettings:
    settings.allow_self_registration = allow_self_registration
    settings.allow_password_authentication = allow_password_authentication
    settings.allow_google_authentication = allow_google_authentication

    db.commit()
    db.refresh(settings)

    return settings