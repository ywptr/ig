"""Bootstrap tenant records or promote a previously authenticated IS member."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ops.database_safety import add_write_confirmation_arguments, require_safe_admin_target, UnsafeDatabaseTarget


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", help="Required for tenant bootstrap; must match an existing tenant")
    parser.add_argument("--domain", action="append", help="Repeat for each hostname (no scheme or port)")
    parser.add_argument("--allow-self-registration", action=argparse.BooleanOptionalAction, default=None,
                        help="Explicitly enable/disable IS JIT admission; otherwise preserve schema/existing defaults")
    parser.add_argument("--promote-admin", action="store_true", help="Promote an existing active IS member")
    parser.add_argument("--is-issuer")
    parser.add_argument("--is-subject")
    parser.add_argument("--dry-run", action="store_true", help="Validate and execute, then roll back all changes")
    add_write_confirmation_arguments(parser)
    args = parser.parse_args()
    if args.promote_admin:
        if not args.is_issuer or not args.is_subject or args.name or args.domain or args.allow_self_registration is not None:
            parser.error("--promote-admin requires --is-issuer and --is-subject; omit tenant creation options")
    elif not args.name or not args.domain or args.is_issuer or args.is_subject:
        parser.error("bootstrap requires --name and --domain; identity options require --promote-admin")
    # Deliberately before engine import/connection, including dry-run.
    require_safe_admin_target(os.environ.get("DATABASE_URL"),
        allow_production_write=args.allow_production_write,
        confirm_database_target=args.confirm_database_target)
    from sqlalchemy.exc import IntegrityError, OperationalError
    from app.db.database import SessionLocal
    from app.tenancy.bootstrap import BootstrapConflict, bootstrap_tenant, promote_admin

    try:
        with SessionLocal() as db, db.begin():
            if args.promote_admin:
                tenant, result = promote_admin(db, slug=args.slug, issuer=args.is_issuer, subject=args.is_subject)
            else:
                tenant, result = bootstrap_tenant(db, slug=args.slug, name=args.name, domains=args.domain,
                    allow_self_registration=args.allow_self_registration)
            lines = [f"Tenant: {tenant.name} ({tenant.slug})", f"Tenant ID: {tenant.tenant_id}"]
            if not args.promote_admin:
                from app.tenancy.bootstrap import bootstrap_hostname
                lines.extend(f"Domain: {bootstrap_hostname(domain)}" for domain in dict.fromkeys(args.domain))
                lines.append("Settings: ready")
            from sqlalchemy import select
            from app.db.models import TenantMembership
            has_admin = db.scalar(select(TenantMembership.user_id).where(
                TenantMembership.tenant_id == tenant.tenant_id,
                TenantMembership.role == "admin", TenantMembership.status == "active").limit(1))
            lines.append("Initial admin: " + ("assigned" if has_admin else "requires promotion"))
            lines.append(f"Result: {result}" + (" (dry run; rolled back)" if args.dry_run else ""))
            if args.dry_run:
                db.rollback()
        print("\n".join(lines))
    except BootstrapConflict as exc:
        raise SystemExit(str(exc)) from None
    except (IntegrityError, OperationalError):
        # Never expose SQL parameters, connection credentials, or external subjects.
        raise SystemExit("Database conflict or unavailable database; transaction rolled back. Retry; if persistent, check tenant/domain ownership and database availability.") from None


if __name__ == "__main__":
    try:
        main()
    except UnsafeDatabaseTarget as exc:
        raise SystemExit(str(exc)) from None
