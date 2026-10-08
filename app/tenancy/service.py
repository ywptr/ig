from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Tenant, TenantDomain


def normalize_hostname(hostname: str) -> str:
    return hostname.strip().lower().rstrip(".")


def get_tenant_by_hostname(
    db: Session,
    hostname: str,
) -> Tenant | None:
    hostname = normalize_hostname(hostname)

    statement = (
        select(Tenant)
        .join(
            TenantDomain,
            TenantDomain.tenant_id
            == Tenant.tenant_id,
        )
        .where(
            TenantDomain.hostname == hostname,
            Tenant.status == "active",
        )
    )

    return db.scalar(statement)