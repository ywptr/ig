"""Bootstrap tests use isolated SQLite only, never inherited database targets."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError, OperationalError
from fastapi import HTTPException

from tests.test_media_job_contracts import MediaJobTestCase, Base
from app.db.models import Tenant, TenantDomain, TenantSettings, TenantMembership, UserIdentity
from app.tenancy.bootstrap import bootstrap_tenant, promote_admin, BootstrapConflict, bootstrap_hostname
from app.api.auth_is import get_or_create_is_user

ROOT = Path(__file__).resolve().parents[1]


class TenantBootstrapTests(MediaJobTestCase):
    def bootstrap(self, db, **overrides):
        return bootstrap_tenant(db, **(dict(slug="fresh", name="Fresh", domains=["LOCALHOST."] ) | overrides))

    def test_fresh_records_defaults_and_idempotency(self):
        with self.sessions() as db, db.begin():
            tenant, result = self.bootstrap(db)
            tenant_id = tenant.tenant_id
            self.assertEqual(result, "created")
            self.assertIsNone(tenant.nexus_tenant_id)
            self.assertEqual(tenant.status, "active")
            settings = db.get(TenantSettings, tenant_id)
            self.assertFalse(settings.allow_self_registration)
            self.assertTrue(settings.allow_password_authentication)
            self.assertTrue(settings.allow_google_authentication)
            self.assertEqual(db.get(TenantDomain, "localhost").tenant_id, tenant_id)
        with self.sessions() as db, db.begin():
            tenant, result = self.bootstrap(db, domains=["localhost", "LOCALHOST."])
            self.assertEqual(tenant.tenant_id, tenant_id)
            self.assertEqual(result, "already configured")
            self.assertEqual(db.scalar(select(func.count()).select_from(TenantDomain).where(TenantDomain.tenant_id == tenant_id)), 1)

    def test_existing_settings_preserved_and_explicit_admission_only(self):
        with self.sessions() as db, db.begin():
            tenant, _ = self.bootstrap(db)
            settings = db.get(TenantSettings, tenant.tenant_id)
            settings.allow_google_authentication = False
            settings.allow_password_authentication = False
        with self.sessions() as db, db.begin():
            tenant, result = self.bootstrap(db, allow_self_registration=True)
            settings = db.get(TenantSettings, tenant.tenant_id)
            self.assertEqual(result, "updated")
            self.assertTrue(settings.allow_self_registration)
            self.assertFalse(settings.allow_google_authentication)
            self.assertFalse(settings.allow_password_authentication)
        with self.sessions() as db, db.begin():
            self.assertEqual(self.bootstrap(db)[1], "already configured")

    def test_domain_conflict_rolls_back_new_tenant_and_prior_domain(self):
        with self.assertRaises(BootstrapConflict), self.sessions() as db, db.begin():
            self.bootstrap(db, domains=["new.test", "testserver"])
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Tenant).where(Tenant.slug == "fresh")))
            self.assertIsNone(db.get(TenantDomain, "new.test"))

    def test_unexpected_failure_rolls_back_complete_bootstrap(self):
        with self.assertRaises(RuntimeError), self.sessions() as db, db.begin():
            self.bootstrap(db)
            raise RuntimeError("simulated failure before commit")
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Tenant).where(Tenant.slug == "fresh")))
            self.assertIsNone(db.get(TenantDomain, "localhost"))

    def test_missing_settings_repaired_for_existing_tenant(self):
        with self.sessions() as db, db.begin():
            tenant, result = self.bootstrap(db, slug="one", name="One", domains=["testserver"])
            self.assertEqual(result, "updated")
            self.assertIsNotNone(db.get(TenantSettings, tenant.tenant_id))

    def test_slug_name_and_inactive_conflicts(self):
        with self.sessions() as db, db.begin():
            self.bootstrap(db)
        with self.assertRaises(BootstrapConflict), self.sessions() as db, db.begin():
            self.bootstrap(db, name="Different")
        with self.sessions() as db, db.begin():
            db.scalar(select(Tenant).where(Tenant.slug == "fresh")).status = "inactive"
        with self.assertRaises(BootstrapConflict), self.sessions() as db, db.begin():
            self.bootstrap(db)

    def test_domain_validation(self):
        for domain in ("https://example.com", "localhost:5181", "x/y", "", "a..b", "-bad.test", "x?y"):
            with self.subTest(domain=domain), self.assertRaises(BootstrapConflict):
                bootstrap_hostname(domain)
        self.assertEqual(bootstrap_hostname(" EXAMPLE.COM. "), "example.com")
        self.assertEqual(bootstrap_hostname("::1"), "::1")

    def test_aliases_have_one_primary(self):
        with self.sessions() as db, db.begin():
            tenant, _ = self.bootstrap(db, domains=["localhost", "example.test", "127.0.0.1"])
            self.assertEqual(db.scalar(select(func.count()).select_from(TenantDomain).where(
                TenantDomain.tenant_id == tenant.tenant_id, TenantDomain.is_primary.is_(True))), 1)

    def test_jit_never_grants_admin_and_promotion_is_exact_and_idempotent(self):
        with self.sessions() as db, db.begin():
            tenant, _ = self.bootstrap(db, allow_self_registration=True)
            tenant_id = tenant.tenant_id
        with self.sessions() as db:
            user, member = get_or_create_is_user(db, tenant_id=tenant_id, issuer="https://is.test", claims={"sub":"intended", "email":"same@test"})
            arbitrary, other = get_or_create_is_user(db, tenant_id=tenant_id, issuer="https://is.test", claims={"sub":"arbitrary", "email":"same@test"})
            self.assertEqual(member.role, "member")
            self.assertEqual(other.role, "member")
        for issuer, subject in (("wrong", "intended"), ("https://is.test", "same@test")):
            with self.assertRaises(BootstrapConflict), self.sessions() as db, db.begin():
                promote_admin(db, slug="fresh", issuer=issuer, subject=subject)
        for result in ("updated", "already configured"):
            with self.sessions() as db, db.begin():
                self.assertEqual(promote_admin(db, slug="fresh", issuer="https://is.test", subject="intended")[1], result)
        with self.sessions() as db:
            members = list(db.scalars(select(TenantMembership).where(TenantMembership.tenant_id == tenant_id)))
            self.assertEqual(len(members), 2)
            self.assertEqual(sorted(m.role for m in members), ["admin", "member"])

    def test_closed_admission_and_prelogin_promotion_fail(self):
        with self.sessions() as db, db.begin():
            tenant, _ = self.bootstrap(db)
            tenant_id = tenant.tenant_id
        with self.assertRaises(HTTPException), self.sessions() as db:
            get_or_create_is_user(db, tenant_id=tenant_id, issuer="is", claims={"sub":"s", "email":"e@test"})
        with self.assertRaises(BootstrapConflict), self.sessions() as db, db.begin():
            promote_admin(db, slug="fresh", issuer="is", subject="s")

    def test_promotion_requires_active_membership(self):
        with self.sessions() as db, db.begin():
            db.add(UserIdentity(identity_id="id", user_id="user-one", issuer="is", subject="s"))
            db.get(TenantMembership, ("tenant-one", "user-one")).status = "inactive"
        for slug in ("one", "missing"):
            with self.assertRaises(BootstrapConflict), self.sessions() as db, db.begin():
                promote_admin(db, slug=slug, issuer="is", subject="s")

    def test_concurrent_bootstrap_constraints_and_retry(self):
        with TemporaryDirectory() as temp:
            engine = create_engine("sqlite:///" + str(Path(temp)/"race.db"))
            try:
                Base.metadata.create_all(engine)
                sessions = sessionmaker(engine)
                def run(_):
                    try:
                        with sessions() as db, db.begin():
                            self.bootstrap(db)
                    except (IntegrityError, OperationalError):
                        return "retry"
                    return "ok"
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(run, range(2)))
                self.assertIn("ok", results)
                with sessions() as db, db.begin():
                    self.assertEqual(self.bootstrap(db)[1], "already configured")
                    self.assertEqual(db.scalar(select(func.count()).select_from(Tenant)), 1)
                    self.assertEqual(db.scalar(select(func.count()).select_from(TenantDomain)), 1)
            finally:
                engine.dispose()

    def test_cli_safety_dry_run_and_local_execution(self):
        with TemporaryDirectory() as temp:
            url = "sqlite:///" + str(Path(temp)/"cli.db")
            engine = create_engine(url)
            Base.metadata.create_all(engine)
            engine.dispose()
            env = dict(os.environ, DATABASE_URL=url, VERCEL_ENV="development", VERCEL_TARGET_ENV="development")
            command = [sys.executable, "scripts/bootstrap-tenant.py", "--slug", "fresh", "--name", "Fresh", "--domain", "localhost"]
            def run(extra=(), **changes):
                return subprocess.run(command + list(extra), env=env | changes, cwd=ROOT, capture_output=True, text=True, timeout=20)
            dry = run(["--dry-run"])
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertIn("rolled back", dry.stdout)
            created = run()
            self.assertIn("Result: created", created.stdout, created.stderr)
            self.assertIn("already configured", run().stdout)
            blocked = run(VERCEL_ENV="production")
            self.assertIn("Refusing administrative writes", blocked.stderr)
            remote = run(DATABASE_URL="postgresql://u:secret@unknown.invalid/ig")
            self.assertIn("Refusing administrative writes", remote.stderr)
            self.assertNotIn("secret", remote.stderr)
            allowed = run(["--allow-production-write", "--confirm-database-target", "local/"+str(Path(temp)/"cli.db")], VERCEL_ENV="production")
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
