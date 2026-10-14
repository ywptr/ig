from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
)
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import (
    get_admin_tenant_context,
)
from app.tenancy.provider_configs import (
    get_provider_config,
    list_provider_configs,
    provider_config_to_dict,
    set_provider_config,
)
from app.tenancy.provider_schemas import (
    TenantProviderConfigResponse,
    TenantProviderConfigUpdate,
)


router = APIRouter(
    prefix="/tenant/providers",
    tags=["tenant-providers"],
)


@router.get(
    "",
    response_model=list[
        TenantProviderConfigResponse
    ],
)
def read_provider_configs(
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    configs = list_provider_configs(
        db,
        tenant_id=context.tenant.tenant_id,
    )

    return [
        provider_config_to_dict(config)
        for config in configs
    ]


@router.get(
    "/{provider}",
    response_model=TenantProviderConfigResponse,
)
def read_provider_config(
    provider: str,
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    config = get_provider_config(
        db,
        tenant_id=context.tenant.tenant_id,
        provider=provider,
    )

    if config is None:
        raise HTTPException(
            status_code=404,
            detail="Provider configuration not found",
        )

    return provider_config_to_dict(
        config
    )


@router.put(
    "/{provider}",
    response_model=TenantProviderConfigResponse,
)
def update_provider_config(
    provider: str,
    request: TenantProviderConfigUpdate,
    context: TenantContext = Depends(
        get_admin_tenant_context
    ),
    db: Session = Depends(get_db),
):
    provider = provider.strip().lower()

    credentials = None

    if request.credentials is not None:
        credentials = {
            key: value.get_secret_value()
            for key, value
            in request.credentials.items()
        }

        if not credentials:
            raise HTTPException(
                status_code=400,
                detail=(
                    "credentials may not be "
                    "an empty object"
                ),
            )

    config = set_provider_config(
        db,
        tenant_id=context.tenant.tenant_id,
        provider=provider,
        enabled=request.enabled,
        credentials=credentials,
        config=request.config,
    )

    return provider_config_to_dict(
        config
    )