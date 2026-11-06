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

def get_canonical_tenant_id(db: Session, tenant_id: str) -> str | None:
    """Return an explicit mapping only; unmapped local IDs are not canonical."""
    return db.scalar(select(Tenant.nexus_tenant_id).where(Tenant.tenant_id == tenant_id))


def get_tenant_by_canonical_id(db: Session, canonical_tenant_id: str) -> Tenant | None:
    return db.scalar(select(Tenant).where(Tenant.nexus_tenant_id == canonical_tenant_id))
