from dataclasses import dataclass

from app.db.models import Tenant, TenantMembership


@dataclass(frozen=True)
class TenantContext:
    tenant: Tenant
    membership: TenantMembership | None = None