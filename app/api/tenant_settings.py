from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

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


class TenantSettingsUpdate(BaseModel):
    allow_self_registration: bool


def settings_to_response(
    settings: TenantSettings,
) -> TenantSettingsResponse:
    return TenantSettingsResponse(
        allow_self_registration=(
            settings.allow_self_registration
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

    settings = update_tenant_settings(
        db,
        settings,
        allow_self_registration=(
            request.allow_self_registration
        ),
    )

    return settings_to_response(settings)