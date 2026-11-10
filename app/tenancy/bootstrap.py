"""Administrative tenant setup. Call inside a caller-owned transaction."""
import ipaddress
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Tenant, TenantDomain, TenantMembership, TenantSettings
from app.tenancy.service import normalize_hostname
from app.users.identities import get_user_by_external_identity


class BootstrapConflict(ValueError):
    pass


def bootstrap_hostname(value: str) -> str:
    hostname = normalize_hostname(value)
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if len(hostname) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        ):
            raise BootstrapConflict("Domain must be a hostname or IP address without scheme, port, or path")
    return hostname


def bootstrap_tenant(db: Session, *, slug: str, name: str, domains: list[str],
                     allow_self_registration: bool | None = None) -> tuple[Tenant, str]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", slug):
        raise BootstrapConflict("Slug must contain 1–100 lowercase letters, digits, underscores or hyphens")
    if not name.strip() or name != name.strip() or len(name) > 255:
        raise BootstrapConflict("Name must contain 1–255 characters without surrounding whitespace")
    if not domains:
        raise BootstrapConflict("At least one domain is required")
    hostnames = list(dict.fromkeys(bootstrap_hostname(domain) for domain in domains))
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug).with_for_update())
    created = tenant is None
    changed = created
    if tenant is None:
        tenant = Tenant(tenant_id=str(uuid4()), slug=slug, name=name)
        db.add(tenant)
        db.flush()
    elif tenant.name != name or tenant.status != "active":
        raise BootstrapConflict("Slug already exists with a different name or inactive status; no changes made")

    for hostname in hostnames:
        domain = db.get(TenantDomain, hostname)
        if domain is not None:
            if domain.tenant_id != tenant.tenant_id:
                raise BootstrapConflict(f"Domain {hostname} already belongs to another tenant")
        else:
            has_primary = db.scalar(select(TenantDomain.hostname).where(
                TenantDomain.tenant_id == tenant.tenant_id, TenantDomain.is_primary.is_(True)))
            db.add(TenantDomain(hostname=hostname, tenant_id=tenant.tenant_id,
                                is_primary=has_primary is None))
            db.flush()
            changed = True

    settings = db.get(TenantSettings, tenant.tenant_id)
    if settings is None:
        settings = TenantSettings(tenant_id=tenant.tenant_id)
        db.add(settings)
        db.flush()
        changed = True
    if allow_self_registration is not None and settings.allow_self_registration != allow_self_registration:
        settings.allow_self_registration = allow_self_registration
        changed = True
    db.flush()
    return tenant, "created" if created else "updated" if changed else "already configured"


def promote_admin(db: Session, *, slug: str, issuer: str, subject: str) -> tuple[Tenant, str]:
    """Promote only a previously authenticated, admitted identity; never link by email."""
    if not issuer or not subject or issuer != issuer.strip() or subject != subject.strip():
        raise BootstrapConflict("Exact nonempty IS issuer and subject are required")
    tenant = db.scalar(select(Tenant).where(Tenant.slug == slug).with_for_update())
    if tenant is None or tenant.status != "active":
        raise BootstrapConflict("Active tenant not found")
    user = get_user_by_external_identity(db, issuer=issuer, subject=subject)
    if user is None:
        raise BootstrapConflict("IS identity not found; complete IS login first")
    membership = db.get(TenantMembership, (tenant.tenant_id, user.user_id), with_for_update=True)
    if membership is None or membership.status != "active":
        raise BootstrapConflict("Identity must have an active membership; complete admission first")
    result = "already configured" if membership.role == "admin" else "updated"
    membership.role = "admin"
    db.flush()
    return tenant, result
