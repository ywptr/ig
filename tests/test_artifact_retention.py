"""Isolated DB/real local objects and authenticated retention API checks."""
from datetime import timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import select, func
from tests import test_media_job_contracts as contracts
from tests import test_service_access as access
from tests.test_artifact_storage import install_storage, local_store
from app.artifacts import service, previews
from app.artifacts.retention import cleanup, eligible, policy, unavailable
from app.artifacts.storage.vercel_blob import VercelBlobArtifactStore
from app.core.time import utc_now
from app.db.models import Artifact, ArtifactPreview, ArtifactInput, TenantSettings, TenantMembership, Job
from app.jobs.service import create_job


class RetentionTests(contracts.MediaJobTestCase):
    def setUp(self):
        super().setUp()
        self.store = install_storage(self)
        self.now = utc_now()

    def artifact(self, *, age=29, provenance="generated", tenant="tenant-one"):
        with self.sessions() as db:
            row = service.create_artifact(db, tenant_id=tenant, user_id="user-one", media_type="image",
                mime_type="image/png", storage_uri=self.store.put_bytes(filename="source", data=b"source"),
                size_bytes=6, provenance=provenance)
            row.created_at = (self.now - timedelta(days=age)).replace(tzinfo=None)
            db.commit()
            return row.artifact_id

    def run_cleanup(self, **kwargs):
        with self.sessions() as db:
            return cleanup(db, now=self.now, store=self.store, **kwargs)

    def admin(self):
        with self.sessions() as db:
            db.get(TenantMembership, ("tenant-one", "user-one")).role = "admin"
            db.commit()

    def test_default_and_override_survives_backend_change(self):
        self.assertTrue(policy(None, "local")["generated_artifact_retention_enabled"])
        self.assertEqual(policy(None, "vercel")["generated_artifact_retention_days"], 28)
        self.assertFalse(policy(None, "future-user-owned")["generated_artifact_retention_enabled"])
        settings = TenantSettings(generated_artifact_retention_enabled=True, generated_artifact_retention_days=45)
        value = policy(settings, "future-user-owned")
        self.assertTrue(value["generated_artifact_retention_enabled"])
        self.assertEqual(value["generated_artifact_retention_days"], 45)
        self.assertEqual(value["retention_policy_source"], "tenant")

    def test_admin_api_persists_and_tenant_scopes(self):
        self.admin()
        path = "/v2/tenant/settings/retention"
        self.assertEqual(self.client.get(path).json()["generated_artifact_retention_days"], 28)
        response = self.client.patch(path, json={"generated_artifact_retention_enabled": False,
                                                 "generated_artifact_retention_days": 90})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.client.get(path).json()["generated_artifact_retention_enabled"])
        self.assertEqual(self.client.get(path).json()["generated_artifact_retention_days"], 90)
        with self.sessions() as db:
            self.assertIsNone(db.get(TenantSettings, "tenant-two"))

    def test_member_cannot_read_or_change(self):
        path = "/v2/tenant/settings/retention"
        self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.patch(path, json={"generated_artifact_retention_enabled": False,
            "generated_artifact_retention_days": 28}).status_code, 403)

    def test_duration_validation(self):
        self.admin()
        for days in (0, -1, 3651, 1.2, True, "28"):
            response = self.client.patch("/v2/tenant/settings/retention", json={
                "generated_artifact_retention_enabled": True, "generated_artifact_retention_days": days})
            self.assertEqual(response.status_code, 422, (days, response.text))

    def test_age_boundary_uploads_and_utc(self):
        young, boundary, old = self.artifact(age=27), self.artifact(age=28), self.artifact(age=29)
        upload = self.artifact(age=100, provenance="uploaded")
        unknown = self.artifact(age=100, provenance="unknown")
        with self.sessions() as db:
            for identity, expected in ((young, False), (boundary, False), (old, True), (upload, False), (unknown, False)):
                self.assertEqual(eligible(db, db.get(Artifact, identity), self.now.astimezone(timezone(timedelta(hours=8)))), expected)
        result = self.run_cleanup()
        self.assertEqual(result["deleted"], 1, result)
        self.assertEqual(result["bytes_reclaimed"], 6)

    def test_subsecond_utc_threshold(self):
        identity = self.artifact(age=28)
        with self.sessions() as db:
            db.get(Artifact, identity).created_at -= timedelta(microseconds=1)
            db.commit()
        self.assertEqual(self.run_cleanup()["deleted"], 1)

    def test_policy_disabled_and_custom_duration(self):
        self.artifact(age=40)
        with self.sessions() as db:
            db.add(TenantSettings(tenant_id="tenant-one", generated_artifact_retention_enabled=False,
                                 generated_artifact_retention_days=30))
            db.commit()
        self.assertEqual(self.run_cleanup()["eligible"], 0)
        with self.sessions() as db:
            settings = db.get(TenantSettings, "tenant-one")
            settings.generated_artifact_retention_enabled = True
            settings.generated_artifact_retention_days = 50
            db.commit()
        self.assertEqual(self.run_cleanup()["eligible"], 0)
        with self.sessions() as db:
            db.get(TenantSettings, "tenant-one").generated_artifact_retention_days = 35
            db.commit()
        self.assertEqual(self.run_cleanup()["deleted"], 1)

    def test_active_jobs_protect_references_all_lifecycles(self):
        for index, status in enumerate(("queued", "running", "waiting", "polling", "cancel_requested")):
            identity = self.artifact()
            with self.sessions() as db:
                db.add(Job(job_id=f"active-{index}", tenant_id="tenant-one", user_id="user-one",
                    capability="video.generate", status=status,
                    input_json={"request": {"references": [{"artifact_id": identity}]}}))
                db.commit()
        self.assertEqual(self.run_cleanup()["eligible"], 0)
        with self.sessions() as db:
            for job in db.scalars(select(Job)): job.status = "completed"
            db.commit()
        self.assertEqual(self.run_cleanup()["deleted"], 5)

    def test_dry_run_bounded_batch_and_rerun(self):
        for _ in range(4): self.artifact()
        self.assertEqual(self.run_cleanup(dry_run=True)["eligible"], 4)
        with self.sessions() as db:
            self.assertFalse(any(unavailable(a) for a in db.scalars(select(Artifact))))
        self.assertEqual(self.run_cleanup(limit=2)["deleted"], 2)
        self.assertEqual(self.run_cleanup()["deleted"], 2)
        self.assertEqual(self.run_cleanup()["deleted"], 0)

    def test_preview_all_versions_lineage_and_api_tombstone(self):
        source, downstream = self.artifact(), self.artifact(age=1)
        with self.sessions() as db:
            db.add(ArtifactInput(output_artifact_id=downstream, position=0, input_artifact_id=source, role="character"))
            for version in (1, 2):
                db.add(ArtifactPreview(artifact_id=source, kind="thumbnail", version=version, status="ready",
                    claim_id="claim", lease_expires_at=self.now, storage_uri=self.store.put_bytes(filename="preview", data=b"webp"), size_bytes=4))
            db.commit()
        self.assertEqual(self.run_cleanup()["bytes_reclaimed"], 14)
        self.assertEqual(len(list(self.store.base_dir.iterdir())), 1)
        response = self.client.get(f"/v2/artifacts/{source}")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["content_available"])
        self.assertEqual(response.json()["content_status"], "removed")
        self.assertNotIn("storage_uri", response.text)
        self.assertEqual(response.json()["preview"]["status"], "deleted")
        for suffix in ("content", "preview"):
            self.assertEqual(self.client.get(f"/v2/artifacts/{source}/{suffix}").status_code, 410)
        self.assertEqual(self.client.get(f"/v2/artifacts/{downstream}/content").content, b"source")
        self.assertEqual(self.client.get(f"/v2/artifacts/{downstream}").json()["inputs"][0]["artifact_id"], source)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ArtifactInput)), 1)
            self.assertTrue(all(p.status == "deleted" and p.storage_uri is None for p in db.scalars(select(ArtifactPreview))))
        self.assertTrue(any(a["content_status"] == "removed" for a in self.client.get("/v2/artifacts/page").json()["items"]))

    def test_failure_isolation_fence_and_retry(self):
        identities = [self.artifact(), self.artifact()]
        real = self.store.delete
        with self.sessions() as db: failed_uri = db.get(Artifact, identities[0]).storage_uri
        def fail(uri):
            if uri == failed_uri: raise RuntimeError("private-canary")
            real(uri)
        with patch.object(self.store, "delete", side_effect=fail): result = self.run_cleanup()
        self.assertEqual((result["deleted"], result["failed"]), (1, 1))
        self.assertEqual(self.client.get(f"/v2/artifacts/{identities[0]}/content").status_code, 410)
        self.assertEqual(self.run_cleanup()["eligible"], 0)
        with self.sessions() as db:
            db.get(Artifact, identities[0]).cleanup_started_at = self.now - timedelta(minutes=16)
            db.commit()
        self.assertEqual(self.run_cleanup()["deleted"], 1)

    def test_active_preview_protected_and_new_input_rejected_after_fence(self):
        identity = self.artifact()
        with self.sessions() as db:
            db.add(ArtifactPreview(artifact_id=identity, kind="thumbnail", version=1, status="processing",
                claim_id="c", lease_expires_at=self.now + timedelta(seconds=120)))
            db.commit()
        self.assertEqual(self.run_cleanup()["eligible"], 0)
        with self.sessions() as db:
            db.get(Artifact, identity).cleanup_started_at = self.now
            db.commit()
            with self.assertRaisesRegex(ValueError, "unavailable"):
                create_job(db, capability="image.generate", tenant_id="tenant-one", user_id="user-one",
                    input_data={"references": [{"artifact_id": identity}]}, defer_dispatch=True)
            db.rollback()
            self.assertIsNone(previews.ensure_preview(db, db.get(Artifact, identity)))

    def test_missing_objects_do_not_inflate_reclaimed_bytes(self):
        identity = self.artifact()
        with self.sessions() as db: self.store.delete(db.get(Artifact, identity).storage_uri)
        result = self.run_cleanup()
        self.assertEqual((result["deleted"], result["bytes_reclaimed"]), (1, 0))

    def test_expired_preview_processor_cannot_publish_after_cleanup(self):
        identity = self.artifact()
        def process(*args):
            with self.sessions() as other:
                row = other.get(ArtifactPreview, (identity, "thumbnail", 1))
                row.lease_expires_at = self.now - timedelta(seconds=1)
                other.commit()
            self.assertEqual(self.run_cleanup()["deleted"], 1)
            return b"derivative", {"width": 1, "height": 1, "frame_count": 1, "positions": []}
        with self.sessions() as db, patch("app.artifacts.previews.process_source", side_effect=process):
            self.assertEqual(previews.ensure_preview(db, db.get(Artifact, identity)).status, "deleted")
        self.assertFalse(list(self.store.base_dir.iterdir()))

    def test_legacy_content_route_respects_tombstone(self):
        from app.db.models import ImageRequest
        identity = self.artifact()
        with self.sessions() as db:
            db.add(ImageRequest(request_id="legacy", tenant_id="tenant-one", user_id="user-one",
                                prompt="old", status="completed", artifact_id=identity))
            db.commit()
        self.run_cleanup()
        self.assertEqual(self.client.get("/v1/images/legacy/content").status_code, 410)

    def test_cron_requires_secret(self):
        with patch.dict("os.environ", {"CRON_SECRET": "test-only"}):
            path = "/v2/internal/artifacts/cleanup"
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers={"Authorization": "Bearer test-only"}).status_code, 200)

    def test_migration_round_trip(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        spec = importlib.util.spec_from_file_location("retention_migration", Path(__file__).resolve().parents[1] / "alembic/versions/1a0006000006_artifact_retention.py")
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        self.assertEqual(migration.down_revision, "1a0003000003")
        self.assertEqual(ScriptDirectory.from_config(Config("alembic.ini")).get_heads(), [migration.revision])
        identity = self.artifact()
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade(); migration.upgrade()
        with self.sessions() as db: self.assertIsNotNone(db.get(Artifact, identity))


class DeleteTests(unittest.TestCase):
    def test_local_idempotent_containment_and_symlinks(self):
        store = local_store(self)
        uri = store.put_bytes(filename="object", data=b"abc")
        store.delete(uri); store.delete(uri)
        outside = store.base_dir.parent / "outside"; outside.write_bytes(b"private")
        link = store.base_dir / "link"; link.symlink_to(outside)
        nested = store.base_dir / "nested"; nested.symlink_to(outside.parent, target_is_directory=True)
        for locator in (outside.as_uri(), link.as_uri(), (nested / outside.name).as_uri(), store.base_dir.as_uri() + "/../outside"):
            with self.assertRaises(ValueError): store.delete(locator)
        self.assertEqual(outside.read_bytes(), b"private")

    def test_blob_delete_missing_safe_and_errors_private(self):
        from vercel.blob import BlobNotFoundError
        store = VercelBlobArtifactStore()
        with patch("app.artifacts.storage.vercel_blob.delete") as delete:
            store.delete("vercel-blob:///artifacts/key"); delete.assert_called_once_with("artifacts/key")
        with patch("app.artifacts.storage.vercel_blob.delete", side_effect=BlobNotFoundError()):
            store.delete("vercel-blob:///artifacts/key")
        with patch("app.artifacts.storage.vercel_blob.delete", side_effect=RuntimeError("private-canary")):
            with self.assertRaisesRegex(RuntimeError, "^Blob artifact deletion failed$"): store.delete("vercel-blob:///artifacts/key")
        with self.assertRaisesRegex(ValueError, "^Invalid Blob artifact locator$"): store.delete("https://private-canary")


class OrphanTests(contracts.MediaJobTestCase):
    def setUp(self):
        super().setUp()
        self.store = install_storage(self)

    def test_known_write_rollback_reclaims_but_commit_preserves(self):
        from app.artifacts.orphans import track_write
        uri = self.store.put_bytes(filename="orphan", data=b"orphan")
        with self.sessions() as db:
            track_write(db, uri, self.store, sessions=self.sessions)
            db.rollback()
        self.assertFalse(self.store.exists(uri))
        uri = self.store.put_bytes(filename="published", data=b"published")
        with self.sessions() as db:
            track_write(db, uri, self.store, sessions=self.sessions)
            service.create_artifact(db, tenant_id="tenant-one", user_id="user-one", media_type="image", storage_uri=uri)
            db.commit()
        self.assertTrue(self.store.exists(uri))

    def test_ambiguous_or_published_write_never_deleted(self):
        from app.artifacts.orphans import discard_unpublished
        uri = self.store.put_bytes(filename="published", data=b"published")
        with self.sessions() as db:
            service.create_artifact(db, tenant_id="tenant-one", user_id="user-one", media_type="image", storage_uri=uri)
            db.commit()
        discard_unpublished(uri, self.store, sessions=self.sessions)
        self.assertTrue(self.store.exists(uri))
        uri2 = self.store.put_bytes(filename="unknown", data=b"unknown")
        discard_unpublished(uri2, self.store, sessions=lambda: (_ for _ in ()).throw(RuntimeError("DB unavailable")))
        self.assertTrue(self.store.exists(uri2))

    def test_upload_write_failure_compensated(self):
        with patch("app.artifacts.service.create_artifact", side_effect=RuntimeError("publication failed")):
            with self.assertRaises(RuntimeError):
                service.create_uploaded_artifact(tenant_id="tenant-one", user_id="user-one", data=b"source",
                    mime_type="image/png", filename="input", origin="reference")
        self.assertFalse(list(self.store.base_dir.iterdir()))


class DelegatedRetentionTests(contracts.MediaJobTestCase):
    setUpClass = classmethod(access.ServiceAccessTests.setUpClass.__func__)
    register = access.ServiceAccessTests.register
    token = access.ServiceAccessTests.token
    headers = access.ServiceAccessTests.headers

    def test_service_scopes_cannot_administer_even_with_admin_actor(self):
        from app.auth.dependencies import get_current_user
        from app.db.models import UserIdentity
        from app.main import app
        app.dependency_overrides.pop(get_current_user)
        with self.sessions() as db:
            db.add(UserIdentity(identity_id="actor", user_id="user-one", issuer=access.ISSUER, subject="human-one"))
            db.get(TenantMembership, ("tenant-one", "user-one")).role = "admin"
            self.register(db); db.commit()
        for method in ("GET", "PATCH"):
            options = {"json": {"generated_artifact_retention_enabled": True, "generated_artifact_retention_days": 28}} if method == "PATCH" else {}
            response = self.client.request(method, "/v2/tenant/settings/retention", headers=self.headers(), **options)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"]["code"], "SERVICE_ROUTE_FORBIDDEN")
