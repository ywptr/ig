import os
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from tests.test_media_job_contracts import MediaJobTestCase
from app.db.models import Tenant, User, UserIdentity
from app.tenancy.context import TenantContext
from app.tenancy.dependencies import get_authenticated_tenant_context, get_admin_tenant_context
from app.tenancy.governance import get_tenancy_governance
from app.tenancy.reconciliation import reconcile_tenancy
from app.tenancy.service import get_canonical_tenant_id, get_tenant_by_canonical_id


class TenancyPreparationTests(MediaJobTestCase):
    def test_mapping_and_unique_constraint(self):
        with self.sessions() as db:
            tenant = db.get(Tenant, "tenant-one")
            self.assertIsNone(get_canonical_tenant_id(db, tenant.tenant_id))
            tenant.nexus_tenant_id = "00000000-0000-0000-0000-000000000001"
            db.commit()
            self.assertEqual(get_tenant_by_canonical_id(db, tenant.nexus_tenant_id).tenant_id, "tenant-one")
            self.assertEqual(get_canonical_tenant_id(db, "tenant-one"), tenant.nexus_tenant_id)
            self.assertIsNone(get_tenant_by_canonical_id(db, "unknown"))
            db.add(Tenant(tenant_id="duplicate", slug="duplicate", name="Duplicate", nexus_tenant_id=tenant.nexus_tenant_id))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_legacy_membership_and_admin_semantics(self):
        from app.db.models import User
        with patch.dict(os.environ, {"NEXUS_TENANCY_MODE": "legacy"}), self.sessions() as db:
            governance = get_tenancy_governance()
            tenant = db.get(Tenant, "tenant-one")
            user = db.get(User, "user-one")
            context = get_authenticated_tenant_context(TenantContext(tenant), user, db, governance)
            with self.assertRaises(HTTPException) as error:
                get_admin_tenant_context(context)
            self.assertEqual(error.exception.detail, "Tenant admin access required")
            context.membership.role = "admin"
            self.assertIs(get_admin_tenant_context(context), context)
            for status in ("inactive", "suspended"):
                context.membership.status = status
                db.flush()
                with self.assertRaises(HTTPException) as error:
                    get_authenticated_tenant_context(TenantContext(tenant), user, db, governance)
                self.assertEqual(error.exception.status_code, 403)
            self.assertIsNone(governance.get_active_membership(db, "missing", user.user_id))

    def test_only_legacy_supported(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNotNone(get_tenancy_governance())
        for mode in ("shadow", "enforce", "invalid"):
            with patch.dict(os.environ, {"NEXUS_TENANCY_MODE": mode}), self.assertRaises(RuntimeError):
                get_tenancy_governance()

    def test_audit_is_read_only_and_identifies_unmapped_users(self):
        with self.sessions() as db:
            report = reconcile_tenancy(db, is_issuer="https://is.example")
            self.assertIn("tenant-one", report["unmapped_tenant_ids"])
            self.assertIn("user-one", report["active_members_without_is_identity"])
            self.assertTrue(all(count == 0 for count in report["orphaned_tenant_references"].values()))
            self.assertFalse(db.new or db.dirty or db.deleted)

    def test_audit_uses_exact_issuer_and_never_email_linking(self):
        with self.sessions() as db:
            db.get(User, "user-one").google_sub = "legacy-google"
            db.add(UserIdentity(identity_id="identity-one", user_id="user-one",
                                issuer="https://other.example", subject="subject"))
            db.commit()
            report = reconcile_tenancy(db, is_issuer="https://is.example")
            self.assertIn("user-one", report["legacy_users_without_is_identity"])
            db.add(UserIdentity(identity_id="identity-two", user_id="user-one",
                                issuer="https://is.example", subject="subject"))
            db.commit()
            report = reconcile_tenancy(db, is_issuer="https://is.example")
            self.assertNotIn("user-one", report["active_members_without_is_identity"])
            self.assertNotIn("user-one", report["legacy_users_without_is_identity"])
