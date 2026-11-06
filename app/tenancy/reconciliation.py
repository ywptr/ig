"""Read-only local readiness report. No credentials or identity subjects emitted."""
from collections import Counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Tenant, TenantDomain, TenantMembership, User, UserIdentity


def _valid_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def reconcile_tenancy(db: Session, *, is_issuer: str) -> dict:
    # Select only the fields needed; never load provider credentials/password hashes.
    tenants = list(db.execute(select(Tenant.tenant_id, Tenant.nexus_tenant_id)))
    tenant_ids = {row.tenant_id for row in tenants}
    mappings = Counter(row.nexus_tenant_id for row in tenants if row.nexus_tenant_id is not None)
    identities = list(db.execute(select(UserIdentity.user_id, UserIdentity.issuer, UserIdentity.subject)))
    linked = {row.user_id for row in identities if row.issuer == is_issuer}
    identity_keys = Counter((row.issuer, row.subject) for row in identities)
    active_users = set(db.scalars(select(TenantMembership.user_id).where(TenantMembership.status == "active")))
    users = set(db.scalars(select(User.user_id)))
    legacy_users = set(db.scalars(select(User.user_id).where(
        (User.password_hash.is_not(None)) | (User.google_sub.is_not(None))
    )))
    orphaned = {}
    # Inspect every registered tenant-scoped table, including compatibility resources.
    for table in Base.metadata.sorted_tables:
        if table.name == "tenants" or "tenant_id" not in table.c:
            continue
        column = table.c.tenant_id
        orphaned[table.name] = sum(
            1 for value in db.scalars(select(column))
            if value is not None and value not in tenant_ids
        )
    domains = set(db.scalars(select(TenantDomain.tenant_id)))
    return {
        "tenant_count": len(tenants),
        "invalid_local_tenant_ids": sorted(t for t in tenant_ids if not _valid_uuid(t)),
        "duplicate_local_tenant_ids": sorted(t for t, count in Counter(r.tenant_id for r in tenants).items() if count > 1),
        "unmapped_tenant_ids": sorted(r.tenant_id for r in tenants if r.nexus_tenant_id is None),
        "invalid_mapping_tenant_ids": sorted(r.tenant_id for r in tenants if r.nexus_tenant_id is not None and not _valid_uuid(r.nexus_tenant_id)),
        "duplicate_canonical_ids": sorted(t for t, count in mappings.items() if count > 1),
        "tenants_without_domains": sorted(tenant_ids - domains),
        "orphaned_tenant_references": orphaned,
        "duplicate_identity_key_count": sum(count > 1 for count in identity_keys.values()),
        "orphaned_identity_count": sum(row.user_id not in users for row in identities),
        "active_members_without_is_identity": sorted(active_users - linked),
        "legacy_users_without_is_identity": sorted(legacy_users - linked),
        "nexus_reconciliation": "pending consumer contract and authoritative Nexus export",
    }
