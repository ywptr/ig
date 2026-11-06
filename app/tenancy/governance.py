"""Authorization boundary; remote governance awaits the Nexus contract."""
import os
from typing import Protocol

from sqlalchemy.orm import Session

from app.db.models import TenantMembership
from app.tenancy.membership import get_tenant_membership


class TenancyGovernance(Protocol):
    def get_active_membership(
        self, db: Session, tenant_id: str, user_id: str,
    ) -> TenantMembership | None: ...


class LegacyTenancyGovernance:
    def get_active_membership(
        self, db: Session, tenant_id: str, user_id: str,
    ) -> TenantMembership | None:
        return get_tenant_membership(db, tenant_id, user_id)


def get_tenancy_governance() -> TenancyGovernance:
    mode = os.environ.get("NEXUS_TENANCY_MODE", "legacy")
    if mode != "legacy":
        raise RuntimeError(
            "NEXUS_TENANCY_MODE must be legacy; Nexus governance is not implemented"
        )
    return LegacyTenancyGovernance()
