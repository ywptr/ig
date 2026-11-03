"""Run with: .venv/bin/python -B -m unittest tests.test_media_job_contracts -v."""

import os
import unittest
from unittest.mock import ANY, Mock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, event, func, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Configure the import-time engine before importing any application modules.
# Never inherit a developer's DATABASE_URL or connect to an external database.
with patch.dict(os.environ, {"DATABASE_URL": "sqlite://"}):
    from app.auth.dependencies import get_current_user, get_db
    from app.db.database import Base
    from app.db.models import (
        Artifact,
        Execution,
        ImageRequest,
        Job,
        Tenant,
        TenantDomain,
        TenantMembership,
        User,
    )
    from app.main import app
    from app.images.tasks import execute_image_generation
    from app.jobs.service import create_job
    from app.jobs.worker import execute_job
    from app.media.providers.base import ImageProviderResult
    from app.tenancy.provider_configs import get_provider_config, set_provider_config


@compiles(BigInteger, "sqlite")
def sqlite_big_integer(type_, compiler, **kwargs):
    # SQLite autoincrement requires the exact type name INTEGER. Production
    # models and PostgreSQL compilation remain unchanged.
    return "INTEGER"


class MediaJobTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.user_id = "user-one"

        with self.sessions() as db:
            db.add_all([
                Tenant(tenant_id="tenant-one", slug="one", name="One"),
                Tenant(tenant_id="tenant-two", slug="two", name="Two"),
                User(user_id="user-one", email="one@example.test"),
                User(user_id="user-two", email="two@example.test"),
            ])
            db.commit()
            db.add_all([
                TenantDomain(hostname="testserver", tenant_id="tenant-one"),
                TenantDomain(hostname="other.test", tenant_id="tenant-two"),
            ])
            db.add_all([
                TenantMembership(tenant_id=tenant, user_id=user)
                for tenant in ("tenant-one", "tenant-two")
                for user in ("user-one", "user-two")
            ])
            db.commit()

        def test_db():
            with self.sessions() as db:
                yield db

        def authenticated_user():
            with self.sessions() as db:
                return db.get(User, self.user_id)

        previous_overrides = app.dependency_overrides.copy()
        self.addCleanup(self.restore_overrides, previous_overrides)
        app.dependency_overrides[get_db] = test_db
        app.dependency_overrides[get_current_user] = authenticated_user

        # Keep real create_job persistence; stop at the external queue boundary.
        self.queue = Mock(spec=["enqueue"])
        queue_patch = patch("app.jobs.service.get_job_queue", return_value=self.queue)
        queue_patch.start()
        self.addCleanup(queue_patch.stop)
        self.client = self.enterContext(TestClient(app))

    @staticmethod
    def restore_overrides(previous):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    def submit_image(self, **fields):
        return self.client.post(
            "/v2/media/generations",
            json={
                "media_type": "image",
                "intent": {"description": "A lighthouse at sunset"},
                **fields,
            },
        )


class MediaJobContractTests(MediaJobTestCase):
    def test_v2_image_submission_succeeds(self):
        response = self.submit_image()
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(set(body), {"media_type", "request_id", "status"})
        self.assertEqual(body["media_type"], "image")
        self.assertEqual(body["status"], "generating")
        with self.sessions() as db:
            image = db.scalar(select(ImageRequest))
            self.assertEqual(image.request_id, body["request_id"])
            self.assertEqual(image.prompt, "A lighthouse at sunset")
            self.assertEqual(image.tenant_id, "tenant-one")
            self.assertEqual(image.user_id, "user-one")

    def test_v2_image_submission_correlates_request_job_and_queue(self):
        response = self.submit_image(output={"quality": "high", "style": "cinematic"})
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            image = db.scalar(select(ImageRequest))
            job = db.scalar(select(Job))
            self.assertEqual(db.scalar(select(func.count()).select_from(ImageRequest)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(Job)), 1)
            self.assertEqual(job.capability, "image.generate")
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.tenant_id, image.tenant_id)
            self.assertEqual(job.user_id, image.user_id)
            self.assertEqual(job.input_json["request_id"], response.json()["request_id"])
            self.assertEqual(job.input_json["request_id"], image.request_id)
            self.assertEqual(job.input_json["intent"], {"description": image.prompt})
            self.assertEqual(job.input_json["media_type"], "image")
            self.assertEqual(job.input_json["request_version"], "1")
            self.assertEqual(job.input_json["output"]["aspect_ratio"], "2:3")
            self.assertEqual(job.input_json["output"]["quality"], "high")
            self.assertEqual(job.input_json["output"]["style"], "cinematic")
            self.queue.enqueue.assert_called_once_with(job.job_id)
            job_id = job.job_id

        job_response = self.client.get(f"/v2/jobs/{job_id}")
        self.assertEqual(job_response.status_code, 200, job_response.text)
        self.assertEqual(job_response.json()["job_id"], job_id)
        self.assertEqual(
            job_response.json()["input"]["request_id"], response.json()["request_id"],
        )

    def create_job(self):
        with self.sessions() as db:
            job = Job(
                job_id="job-one",
                tenant_id="tenant-one",
                user_id="user-one",
                capability="image.generate",
                status="queued",
                input_json={"request_id": "request-one"},
            )
            db.add(job)
            db.commit()
        return "job-one"

    def test_get_job_for_authorized_user(self):
        job_id = self.create_job()
        response = self.client.get(f"/v2/jobs/{job_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["job_id"], job_id)
        self.assertEqual(response.json()["capability"], "image.generate")
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(response.json()["input"], {"request_id": "request-one"})

    def test_get_job_is_scoped_to_user_and_hostname_tenant(self):
        job_id = self.create_job()
        self.user_id = "user-two"
        response = self.client.get(f"/v2/jobs/{job_id}")
        self.assertEqual(response.status_code, 404, response.text)
        self.user_id = "user-one"
        response = self.client.get(f"http://other.test/v2/jobs/{job_id}")
        self.assertEqual(response.status_code, 404, response.text)

    def test_video_remains_unavailable_without_persisting_or_enqueueing(self):
        response = self.submit_image(media_type="video")
        self.assertEqual(response.status_code, 501, response.text)
        self.assertEqual(response.json(), {"detail": {
            "code": "CAPABILITY_NOT_AVAILABLE",
            "capability": "video.generate",
        }})
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(ImageRequest)))
            self.assertIsNone(db.scalar(select(Job)))
        self.queue.enqueue.assert_not_called()

    def test_legacy_v1_image_submission_preserves_response_and_job_input(self):
        response = self.client.post(
            "/v1/images/generations", json={"prompt": "A lighthouse at sunset"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(set(body), {
            "request_id", "created_at", "prompt", "model", "status", "filename",
            "mime_type", "size_bytes", "generation_time_ms",
        })
        self.assertEqual(body["status"], "generating")
        self.assertEqual(body["prompt"], "A lighthouse at sunset")
        self.assertIsNone(body["model"])
        with self.sessions() as db:
            image = db.scalar(select(ImageRequest))
            job = db.scalar(select(Job))
            self.assertEqual(image.request_id, body["request_id"])
            self.assertEqual((image.tenant_id, image.user_id), ("tenant-one", "user-one"))
            self.assertEqual((job.tenant_id, job.user_id), ("tenant-one", "user-one"))
            self.assertEqual(job.capability, "image.generate")
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.input_json, {
                "request_version": "1", "media_type": "image",
                "request_id": image.request_id, "interface_level": "easy",
                "intent": {"description": body["prompt"]},
                "output": {
                    "quality": "standard", "aspect_ratio": None,
                    "style": "auto", "count": 1,
                    "layout": {"type": "single", "panel_count": None},
                },
                "references": [], "advanced": None,
            })
            self.queue.enqueue.assert_called_once_with(job.job_id)


class JobDispatchSecurityTests(MediaJobTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, {
            "TENANT_SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        }))
        with self.sessions() as db:
            set_provider_config(
                db, tenant_id="tenant-one", provider="openai", enabled=True,
                credentials={"api_key": "fake-test-key"},
                config={"default_model": "gpt-image-2"},
            )

        # Run the actual worker, handler, provider configuration resolution, and
        # artifact persistence against the same isolated database as the API.
        self.enterContext(patch("app.jobs.worker.SessionLocal", self.sessions))
        self.enterContext(patch("app.images.tasks.SessionLocal", self.sessions))
        self.provider = Mock(spec=["generate"])
        self.provider.generate.return_value = ImageProviderResult(
            provider="openai", model="gpt-image-2", mime_type="image/png",
            data=b"fake image bytes", size_bytes=16,
        )
        self.provider_factory = self.enterContext(patch(
            "app.images.tasks.create_image_provider", return_value=self.provider,
        ))
        self.provider_lookup = self.enterContext(patch(
            "app.images.tasks.get_provider_config", wraps=get_provider_config,
        ))
        self.store = Mock(spec=["put_bytes", "exists", "iter_bytes"])
        self.store.put_bytes.return_value = "file:///test-output.png"
        self.enterContext(patch("app.images.tasks.artifact_store", self.store))

    def create_target(self, tenant_id, user_id):
        with self.sessions() as db:
            db.add(ImageRequest(
                request_id="target-request", tenant_id=tenant_id, user_id=user_id,
                prompt="Original target prompt", status="generating", model=None,
            ))
            db.commit()
        return "target-request"

    def assert_public_generation_rejected(self, tenant_id, user_id):
        request_id = self.create_target(tenant_id, user_id)
        response = self.client.post("/v2/jobs", json={
            "capability": "image.generate",
            "tenant_id": tenant_id, "user_id": user_id,
            "input": {
                "request_id": request_id, "prompt": "Untrusted replacement",
                "tenant_id": tenant_id, "user_id": user_id,
            },
        })
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("/v2/media/generations", response.json()["detail"])
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Job)))
            self.assertEqual(db.scalar(select(ImageRequest)).status, "generating")
        self.queue.enqueue.assert_not_called()
        self.provider_lookup.assert_not_called()
        self.provider_factory.assert_not_called()
        self.store.put_bytes.assert_not_called()

    def test_public_dispatch_rejects_another_users_image_request(self):
        self.assert_public_generation_rejected("tenant-one", "user-two")

    def test_public_dispatch_rejects_another_tenants_image_request(self):
        self.assert_public_generation_rejected("tenant-two", "user-one")

    def test_public_dispatch_rejects_even_an_owned_image_request(self):
        self.assert_public_generation_rejected("tenant-one", "user-one")

    def assert_worker_rejects_target(self, tenant_id, user_id):
        request_id = self.create_target(tenant_id, user_id)
        with self.sessions() as db:
            # Model a pre-existing/malformed job. Forged input ownership must not
            # override the server-owned columns used by the worker.
            job = create_job(
                db, capability="image.generate",
                tenant_id="tenant-one", user_id="user-one",
                input_data={
                    "request_id": request_id, "prompt": "Untrusted replacement",
                    "tenant_id": tenant_id, "user_id": user_id,
                },
            )
            job_id = job.job_id

        with self.assertRaisesRegex(RuntimeError, "not found or not accessible"):
            execute_job(job_id)

        with self.sessions() as db:
            image = db.scalar(select(ImageRequest))
            self.assertEqual(image.status, "generating")
            self.assertEqual(image.prompt, "Original target prompt")
            self.assertIsNone(image.artifact_id)
            self.assertIsNone(db.scalar(select(Artifact)))
            self.assertEqual(db.scalar(select(Job)).status, "failed")
            execution = db.scalar(select(Execution))
            self.assertEqual(execution.status, "failed")
            self.assertEqual(
                (execution.tenant_id, execution.user_id), ("tenant-one", "user-one"),
            )
        self.provider_lookup.assert_not_called()
        self.provider_factory.assert_not_called()
        self.provider.generate.assert_not_called()
        self.store.put_bytes.assert_not_called()

    def test_worker_rejects_another_users_request_despite_forged_input(self):
        self.assert_worker_rejects_target("tenant-one", "user-two")

    def test_worker_rejects_another_tenants_request_despite_forged_input(self):
        self.assert_worker_rejects_target("tenant-two", "user-one")

    def test_image_handler_rejects_missing_trusted_ownership(self):
        request_id = self.create_target("tenant-one", "user-one")
        for tenant_id, user_id in (("tenant-one", None), ("", "user-one")):
            with self.subTest(tenant_id=tenant_id, user_id=user_id):
                with self.assertRaisesRegex(RuntimeError, "requires trusted tenant and user"):
                    execute_image_generation(
                        {"request_id": request_id, "prompt": "Untrusted replacement",
                         "tenant_id": "tenant-one", "user_id": "user-one"},
                        tenant_id=tenant_id, user_id=user_id,
                    )
        self.provider_lookup.assert_not_called()
        self.store.put_bytes.assert_not_called()

    def assert_authorized_execution(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            job = db.scalar(select(Job))
            self.assertEqual((job.tenant_id, job.user_id), ("tenant-one", "user-one"))
            self.assertEqual(job.input_json["request_id"], response.json()["request_id"])
            # Even bogus ownership fields in payload cannot replace Job columns.
            job.input_json = {
                **job.input_json, "tenant_id": "tenant-two", "user_id": "user-two",
            }
            db.commit()
            job_id = job.job_id

        execute_job(job_id)

        self.provider_lookup.assert_called_once_with(
            ANY, tenant_id="tenant-one", provider="openai",
        )
        self.provider_factory.assert_called_once_with(
            "openai", credentials={"api_key": "fake-test-key"},
            config={"default_model": "gpt-image-2"},
        )
        self.provider.generate.assert_called_once()
        resolved_request = self.provider.generate.call_args.args[0]
        self.assertEqual(resolved_request.description, "A lighthouse at sunset")
        self.assertEqual(resolved_request.model, "gpt-image-2")
        self.store.put_bytes.assert_called_once()
        with self.sessions() as db:
            image = db.scalar(select(ImageRequest))
            artifact = db.scalar(select(Artifact))
            execution = db.scalar(select(Execution))
            self.assertEqual(image.status, "completed")
            self.assertEqual(db.scalar(select(Job)).status, "completed")
            self.assertEqual(execution.status, "completed")
            self.assertEqual(image.artifact_id, artifact.artifact_id)
            self.assertEqual(execution.metadata_json["artifact_id"], artifact.artifact_id)
            self.assertEqual(execution.metadata_json["request_id"], image.request_id)
            self.assertEqual((artifact.tenant_id, artifact.user_id), ("tenant-one", "user-one"))
            self.assertEqual((execution.tenant_id, execution.user_id), ("tenant-one", "user-one"))

    def test_authorized_v2_internal_image_execution_succeeds(self):
        self.assert_authorized_execution(self.submit_image())

    def test_authorized_legacy_v1_internal_image_execution_succeeds(self):
        self.assert_authorized_execution(self.client.post(
            "/v1/images/generations", json={"prompt": "A lighthouse at sunset"},
        ))

    def test_public_noop_preserves_trusted_job_ownership_and_executes(self):
        response = self.client.post("/v2/jobs", json={
            "capability": "system.noop",
            "input": {"tenant_id": "tenant-two", "user_id": "user-two"},
        })
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["job_id"]
        with self.sessions() as db:
            job = db.scalar(select(Job))
            self.assertEqual((job.tenant_id, job.user_id), ("tenant-one", "user-one"))
        execute_job(job_id)
        self.assertEqual(self.client.get(f"/v2/jobs/{job_id}").json()["status"], "completed")
        self.provider_factory.assert_not_called()

    def test_unknown_capability_remains_rejected(self):
        response = self.client.post("/v2/jobs", json={"capability": "unknown"})
        self.assertEqual(response.status_code, 400, response.text)
        self.queue.enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
