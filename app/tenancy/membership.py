from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TenantMembership


def get_tenant_membership(
    db: Session,
    tenant_id: str,
    user_id: str,
) -> TenantMembership | None:
    statement = select(TenantMembership).where(
        TenantMembership.tenant_id == tenant_id,
        TenantMembership.user_id == user_id,
        TenantMembership.status == "active",
    )

    return db.scalar(statement)