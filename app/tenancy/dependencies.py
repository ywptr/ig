from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.tenancy.context import TenantContext
from app.tenancy.service import get_tenant_by_hostname


def get_tenant_context(
    request: Request,
    db: Session = Depends(get_db),
) -> TenantContext:
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
from app.tenancy.membership import get_tenant_membership


def get_authenticated_tenant_context(
    tenant_context: TenantContext = Depends(
        get_tenant_context
    ),
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(get_db),
) -> TenantContext:
    membership = get_tenant_membership(
        db,
        tenant_context.tenant.tenant_id,
        current_user.user_id,
    )

    if membership is None:
        raise HTTPException(
            status_code=403,
            detail="User is not a member of this tenant",
        )

    return TenantContext(
        tenant=tenant_context.tenant,
        membership=membership,
    )