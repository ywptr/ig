from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.tenancy.auth_providers import (
    get_tenant_auth_provider,
)
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_tenant_context,
)
from app.tenancy.settings import (
    get_tenant_settings,
)


router = APIRouter(
    prefix="/tenant/auth-config",
    tags=["tenant-auth-config"],
)


class PasswordAuthConfig(BaseModel):
    enabled: bool


class GoogleAuthConfig(BaseModel):
    enabled: bool
    configured: bool
    client_id: str | None = None


class TenantAuthConfigResponse(BaseModel):
    allow_self_registration: bool
    password: PasswordAuthConfig
    google: GoogleAuthConfig


@router.get(
    "",
    response_model=TenantAuthConfigResponse,
)
def get_auth_config(
    context: TenantContext = Depends(
        get_tenant_context
    ),
    db: Session = Depends(get_db),
):
    settings = get_tenant_settings(
        db,
        context.tenant.tenant_id,
    )

    if settings is None:
        raise HTTPException(
            status_code=500,
            detail="Tenant settings not configured",
        )

    google_provider = get_tenant_auth_provider(
        db,
        tenant_id=context.tenant.tenant_id,
        provider="google",
    )

    google_configured = (
        google_provider is not None
        and bool(google_provider.client_id)
    )

    return TenantAuthConfigResponse(
        allow_self_registration=(
            settings.allow_self_registration
        ),
        password=PasswordAuthConfig(
            enabled=(
                settings.allow_password_authentication
            ),
        ),
        google=GoogleAuthConfig(
            enabled=(
                settings.allow_google_authentication
                and google_configured
            ),
            configured=google_configured,
            client_id=(
                google_provider.client_id
                if google_configured and settings.allow_google_authentication
                else None
            ),
        ),
    )