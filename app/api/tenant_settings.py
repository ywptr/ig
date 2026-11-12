from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel, Field
from app.artifacts.retention import policy
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.db.models import Tenant

from app.auth.dependencies import get_db
from app.db.models import TenantSettings
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_admin_tenant_context,
)
from app.tenancy.settings import (
    get_tenant_settings,
    update_tenant_settings,
)


router = APIRouter(
    prefix="/tenant/settings",
    tags=["tenant-settings"],
)


class TenantSettingsResponse(BaseModel):
    allow_self_registration: bool
    allow_password_authentication: bool
    allow_google_authentication: bool


class TenantSettingsUpdate(BaseModel):
    allow_self_registration: bool
    allow_password_authentication: bool
    allow_google_authentication: bool


def settings_to_response(
    settings: TenantSettings,
) -> TenantSettingsResponse:
    return TenantSettingsResponse(
        allow_self_registration=(
            settings.allow_self_registration
        ),
        allow_password_authentication=(
            settings.allow_password_authentication
        ),
        allow_google_authentication=(
            settings.allow_google_authentication
        ),
    )


@router.get(
    "",
    response_model=TenantSettingsResponse,
)
def read_tenant_settings(
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    settings = get_tenant_settings(
        db,
        context.tenant.tenant_id,
    )

    if settings is None:
        raise HTTPException(
            status_code=404,
            detail="Tenant settings not found",
        )

    return settings_to_response(settings)


@router.patch(
    "",
    response_model=TenantSettingsResponse,
)
def patch_tenant_settings(
    request: TenantSettingsUpdate,
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    settings = get_tenant_settings(
        db,
        context.tenant.tenant_id,
    )

    if settings is None:
        raise HTTPException(
            status_code=404,
            detail="Tenant settings not found",
        )

    if (
        not request.allow_password_authentication
        and not request.allow_google_authentication
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "At least one authentication method must remain enabled"
            ),
        )

    settings = update_tenant_settings(
        db,
        settings,
        allow_self_registration=(
            request.allow_self_registration
        ),
        allow_password_authentication=(
            request.allow_password_authentication
        ),
        allow_google_authentication=(
            request.allow_google_authentication
        ),
    )

    return settings_to_response(settings)

class RetentionUpdate(BaseModel):
    generated_artifact_retention_enabled: bool = Field(strict=True)
    generated_artifact_retention_days: int = Field(ge=1, le=3650, strict=True)


@router.get("/retention")
def read_retention(context: TenantContext = Depends(get_admin_tenant_context), db: Session = Depends(get_db)):
    return policy(db.get(TenantSettings, context.tenant.tenant_id))


@router.patch("/retention")
def patch_retention(request: RetentionUpdate, context: TenantContext = Depends(get_admin_tenant_context),
                    db: Session = Depends(get_db)):
    db.scalar(select(Tenant).where(Tenant.tenant_id == context.tenant.tenant_id).with_for_update(key_share=True))
    settings = db.get(TenantSettings, context.tenant.tenant_id)
    if settings is None:
        settings = TenantSettings(tenant_id=context.tenant.tenant_id)
        db.add(settings)
    settings.generated_artifact_retention_enabled = request.generated_artifact_retention_enabled
    settings.generated_artifact_retention_days = request.generated_artifact_retention_days
    db.commit()
    return policy(settings)
