"""Existing tenant admin APIs remain the authorization boundary for the new page."""
from sqlalchemy import select
from tests import test_media_job_contracts as contracts
from tests import test_service_access as access
from app.db.models import TenantSettings, TenantMembership, UserIdentity

PATHS = ("storage", "retention", "external-retention")


def body(path):
    return {"google_drive_enabled": True} if path == "storage" else {
        "generated_artifact_retention_enabled": False, "generated_artifact_retention_days": 60}


class TenantSettingsAccessTests(contracts.MediaJobTestCase):
    def test_member_cannot_read_or_update_admin_settings(self):
        for path in PATHS:
            for method in ("GET", "PATCH"):
                kwargs = {"json": body(path)} if method == "PATCH" else {}
                self.assertEqual(self.client.request(method, "/v2/tenant/settings/" + path, **kwargs).status_code, 403)
        self.assertEqual(self.client.get("/v2/tenant/providers").status_code, 403)
        with self.sessions() as db:
            self.assertIsNone(db.get(TenantSettings, "tenant-one"))

    def test_admin_updates_scopes_independently_and_identity_is_context_only(self):
        with self.sessions() as db:
            db.get(TenantMembership, ("tenant-one", "user-one")).role = "admin"
            db.commit()
        tenant = self.client.get("/v2/tenant").json()
        self.assertEqual((tenant["name"], tenant["slug"], tenant["tenant_id"]), ("One", "one", "tenant-one"))
        self.assertNotIn("nexus_tenant_id", tenant)
        for path in PATHS:
            self.assertEqual(self.client.get("/v2/tenant/settings/" + path).status_code, 200)
            response = self.client.patch("/v2/tenant/settings/" + path, json=body(path))
            self.assertEqual(response.status_code, 200, response.text)
        with self.sessions() as db:
            settings = db.get(TenantSettings, "tenant-one")
            self.assertTrue(settings.google_drive_enabled)
            self.assertFalse(settings.generated_artifact_retention_enabled)
            self.assertFalse(settings.external_generated_retention_enabled)
            self.assertIsNone(db.get(TenantSettings, "tenant-two"))
        self.assertEqual(self.client.get("/v2/tenant/providers").status_code, 200)


class DelegatedSettingsAccessTests(contracts.MediaJobTestCase):
    setUpClass = classmethod(access.ServiceAccessTests.setUpClass.__func__)
    register = access.ServiceAccessTests.register
    token = access.ServiceAccessTests.token
    headers = access.ServiceAccessTests.headers

    def test_sandcastle_scopes_never_grant_admin_even_for_admin_actor(self):
        from app.auth.dependencies import get_current_user
        from app.main import app
        app.dependency_overrides.pop(get_current_user)
        with self.sessions() as db:
            db.add(UserIdentity(identity_id="actor", user_id="user-one", issuer=access.ISSUER, subject="human-one"))
            db.get(TenantMembership, ("tenant-one", "user-one")).role = "admin"
            self.register(db); db.commit()
        for path in PATHS:
            for method in ("GET", "PATCH"):
                kwargs = {"json": body(path)} if method == "PATCH" else {}
                response = self.client.request(method, "/v2/tenant/settings/" + path, headers=self.headers(), **kwargs)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["detail"]["code"], "SERVICE_ROUTE_FORBIDDEN")
        self.assertEqual(self.client.get("/v2/tenant/providers", headers=self.headers()).status_code, 403)
