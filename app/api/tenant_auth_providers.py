from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.db.models import TenantAuthProvider
from app.tenancy.auth_providers import (
    list_tenant_auth_providers,
    upsert_tenant_auth_provider,
)
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_admin_tenant_context,
)


router = APIRouter(
    prefix="/tenant/auth-providers",
    tags=["tenant-auth-providers"],
)


class TenantAuthProviderUpdate(BaseModel):
    client_id: str
    issuer: str | None = None
    metadata: dict | None = None


def auth_provider_to_dict(
    provider: TenantAuthProvider,
) -> dict:
    return {
        "provider": provider.provider,
        "client_id": provider.client_id,
        "issuer": provider.issuer,
        "metadata": provider.metadata_json,
        "created_at": (
            provider.created_at.isoformat()
        ),
    }


@router.get("")
def get_auth_providers(
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    providers = list_tenant_auth_providers(
        db,
        tenant_id=context.tenant.tenant_id,
    )

    return [
        auth_provider_to_dict(provider)
        for provider in providers
    ]


@router.put("/{provider}")
def put_auth_provider(
    provider: str,
    request: TenantAuthProviderUpdate,
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    provider = provider.strip().lower()

    if provider not in {
        "google",
        "entra",
    }:
        raise HTTPException(
            status_code=400,
            detail="Unsupported authentication provider",
        )

    client_id = request.client_id.strip()

    if not client_id:
        raise HTTPException(
            status_code=400,
            detail="client_id is required",
        )

    auth_provider = upsert_tenant_auth_provider(
        db,
        tenant_id=context.tenant.tenant_id,
        provider=provider,
        client_id=client_id,
        issuer=request.issuer,
        metadata=request.metadata,
    )

    return auth_provider_to_dict(
        auth_provider
    )