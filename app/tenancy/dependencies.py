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