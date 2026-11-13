from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.tenancy.context import TenantContext
from app.tenancy.service import get_tenant_by_hostname


def get_tenant_context(
    request: Request,
    db: Session = Depends(get_db),
) -> TenantContext:
    from app.editor import editor_principal
    editor = editor_principal(request, db)
    if editor is not None:
        return editor
    from app.auth.service_access import service_principal
    principal = service_principal(request, db)
    if principal is not None:
        return principal
    hostname = request.url.hostname

    if hostname is None:
        raise HTTPException(
            status_code=400,
            detail="Unable to determine tenant hostname",
        )

    tenant = get_tenant_by_hostname(
        db,
        hostname,
    )

    if tenant is None:
        raise HTTPException(
            status_code=404,
            detail="Tenant not found",
        )

    return TenantContext(
        tenant=tenant,
    )

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.tenancy.governance import TenancyGovernance, get_tenancy_governance


def get_authenticated_tenant_context(
    tenant_context: TenantContext = Depends(
        get_tenant_context
    ),
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(get_db),
    governance: TenancyGovernance = Depends(get_tenancy_governance),
) -> TenantContext:
    from app.auth.principal import RequestPrincipal
    if isinstance(tenant_context, RequestPrincipal) and tenant_context.caller_type in {"service", "editor"}:
        return tenant_context
    membership = governance.get_active_membership(
        db,
        tenant_context.tenant.tenant_id,
        current_user.user_id,
    )

    if membership is None:
        raise HTTPException(
            status_code=403,
            detail="User is not a member of this tenant",
        )

    return RequestPrincipal(
        tenant=tenant_context.tenant, membership=membership, actor_user=current_user,
        actor_user_id=current_user.user_id, tenant_id=tenant_context.tenant.tenant_id,
    )

def get_admin_tenant_context(
    context: TenantContext = Depends(
        get_authenticated_tenant_context
    ),
) -> TenantContext:
    if (
        getattr(context, "caller_type", "user") in {"service", "editor"}
        or context.membership is None
        or context.membership.role != "admin"
    ):
        raise HTTPException(
            status_code=403,
            detail="Tenant admin access required",
        )

    return context
