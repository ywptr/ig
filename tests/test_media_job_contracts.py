"""Run with: .venv/bin/python -B -m unittest tests.test_media_job_contracts -v."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock
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
    from app.jobs.handlers import HANDLERS
    from app.jobs.result import Completed, Pending
    from app.jobs.worker import execute_job
    from app.media.providers.base import ImageProviderResult
    from app.tenancy.provider_configs import get_provider_config, set_provider_config


@compiles(BigInteger, "sqlite")
def sqlite_big_integer(type_, compiler, **kwargs):
    # SQLite autoincrement requires the exact type name INTEGER. Production
    # models and PostgreSQL compilation remain unchanged.
    return "INTEGER"


class MediaJobTestCase(unittest.TestCase):
    def create_test_engine(self):
        return create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    def setUp(self):
        self.engine = self.create_test_engine()
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            if self.engine.dialect.name == "sqlite":
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
        self.enterContext(patch("app.jobs.continuation.SessionLocal", self.sessions))
        self.enterContext(patch("app.jobs.continuation.get_job_queue", return_value=self.queue))
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
    def setUp(self):
        super().setUp()
        self.enterContext(patch.dict(os.environ, {
            "TENANT_SECRET_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        }))
        with self.sessions() as db:
            set_provider_config(db, tenant_id="tenant-one", provider="openai", enabled=True,
                                credentials={"api_key": "test-only"}, config={"default_model": "gpt-image-2"})

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

    def test_audio_remains_unavailable_without_persisting_or_enqueueing(self):
        response = self.submit_image(media_type="audio")
        self.assertEqual(response.status_code, 501, response.text)
        self.assertEqual(response.json(), {"detail": {
            "code": "CAPABILITY_NOT_AVAILABLE",
            "capability": "audio.generate",
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
                "selection": {"provider": "openai", "model": "gpt-image-2",
                              "purpose": "general", "reason": "tenant_default"},
            })
            self.queue.enqueue.assert_called_once_with(job.job_id)


class ImageExecutionTestCase(MediaJobTestCase):
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


class JobDispatchSecurityTests(ImageExecutionTestCase):
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


class JobIdempotencyTests(ImageExecutionTestCase):
    def create_test_engine(self):
        # Use separate connections, unlike StaticPool, so racing workers have
        # independent transactions and exercise the real database claim.
        directory = self.enterContext(TemporaryDirectory())
        return create_engine(
            f"sqlite:///{Path(directory) / 'jobs.sqlite'}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )

    def submit_job(self, legacy=False):
        response = (
            self.client.post("/v1/images/generations", json={"prompt": "A lighthouse"})
            if legacy else self.submit_image()
        )
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            return db.scalar(select(Job.job_id))

    def snapshot(self):
        with self.sessions() as db:
            job = db.scalar(select(Job))
            execution = db.scalar(select(Execution))
            image = db.scalar(select(ImageRequest))
            return {
                "job": (job.status, job.started_at, job.completed_at, job.error_message),
                "execution": (
                    execution.execution_id, execution.status, execution.attempt,
                    execution.started_at, execution.completed_at,
                    execution.error_message, execution.metadata_json,
                ) if execution else None,
                "image": (image.status, image.artifact_id),
                "artifacts": db.scalar(select(func.count()).select_from(Artifact)),
                "executions": db.scalar(select(func.count()).select_from(Execution)),
            }

    def assert_completed_once(self):
        self.provider.generate.assert_called_once()
        self.store.put_bytes.assert_called_once()
        state = self.snapshot()
        self.assertEqual(state["job"][0], "completed")
        self.assertEqual(state["execution"][1:3], ("completed", 1))
        self.assertEqual(state["image"][0], "completed")
        self.assertEqual(state["artifacts"], 1)
        self.assertEqual(state["executions"], 1)

    def test_authorized_image_job_completes_once(self):
        execute_job(self.submit_job())
        self.assert_completed_once()

    def test_completed_job_redelivery_preserves_results_without_dispatch(self):
        job_id = self.submit_job()
        execute_job(job_id)
        before = self.snapshot()
        with patch("app.jobs.worker.get_handler") as dispatch:
            execute_job(job_id)
            execute_job(job_id)
        dispatch.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assert_completed_once()

    def test_running_job_redelivery_does_not_dispatch_or_create_execution(self):
        job_id = self.submit_job()
        with self.sessions() as db:
            db.scalar(select(Job)).status = "running"
            db.commit()
        before = self.snapshot()
        with patch("app.jobs.worker.get_handler") as dispatch:
            execute_job(job_id)
        dispatch.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.provider.generate.assert_not_called()
        self.store.put_bytes.assert_not_called()

    def test_concurrent_deliveries_only_one_claim_and_execution(self):
        job_id = self.submit_job()
        barrier = Barrier(2)
        rowcounts = []
        lock = Lock()

        def is_claim(statement):
            return statement.startswith("UPDATE jobs SET") and "started_at=" in statement.partition(" WHERE ")[0] and "AND jobs.status =" in statement

        def before_claim(connection, cursor, statement, parameters, context, executemany):
            if is_claim(statement):
                # Both connections reach the conditional UPDATE before either
                # executes it; the database, rather than a Python lock, decides.
                barrier.wait(timeout=10)

        def after_claim(connection, cursor, statement, parameters, context, executemany):
            if is_claim(statement):
                with lock:
                    rowcounts.append(cursor.rowcount)

        event.listen(self.engine, "before_cursor_execute", before_claim)
        event.listen(self.engine, "after_cursor_execute", after_claim)
        try:
            with ThreadPoolExecutor(max_workers=2) as workers:
                attempts = [workers.submit(execute_job, job_id) for _ in range(2)]
                for attempt in attempts:
                    attempt.result(timeout=20)
        finally:
            event.remove(self.engine, "before_cursor_execute", before_claim)
            event.remove(self.engine, "after_cursor_execute", after_claim)

        self.assertEqual(sorted(rowcounts), [0, 1])
        self.assert_completed_once()

    def test_failed_job_redelivery_does_not_retry_provider(self):
        job_id = self.submit_job()
        self.provider.generate.side_effect = RuntimeError("Provider failed")
        with self.assertRaisesRegex(RuntimeError, "Provider failed"):
            execute_job(job_id)
        before = self.snapshot()
        self.assertEqual(before["job"][0], "failed")
        self.assertEqual(before["execution"][1], "failed")
        with patch("app.jobs.worker.get_handler") as dispatch:
            execute_job(job_id)
        dispatch.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.provider.generate.assert_called_once()
        self.assertEqual(before["executions"], 1)
        self.assertEqual(before["artifacts"], 0)
        self.store.put_bytes.assert_not_called()

    def test_legacy_image_redelivery_does_not_repeat_generation(self):
        job_id = self.submit_job(legacy=True)
        execute_job(job_id)
        execute_job(job_id)
        self.assert_completed_once()

    def test_crash_after_artifact_commit_skips_redelivery_but_remains_running(self):
        job_id = self.submit_job()
        # BaseException models abrupt termination: the handler's artifact/request
        # transaction commits, but the worker cannot finalize its own transaction.
        with patch("app.jobs.worker.apply_execution_result", side_effect=SystemExit("crash")):
            with self.assertRaises(SystemExit):
                execute_job(job_id)
        before = self.snapshot()
        self.assertEqual(before["job"][0], "running")
        self.assertEqual(before["execution"][1], "running")
        self.assertEqual(before["image"][0], "completed")
        self.assertEqual(before["artifacts"], 1)
        execute_job(job_id)
        self.assertEqual(self.snapshot(), before)
        self.provider.generate.assert_called_once()
        self.store.put_bytes.assert_called_once()

    def test_crash_after_claim_before_dispatch_is_not_automatically_reclaimed(self):
        job_id = self.submit_job()
        with patch("app.jobs.worker.get_handler", side_effect=SystemExit("crash")):
            with self.assertRaises(SystemExit):
                execute_job(job_id)
        before = self.snapshot()
        self.assertEqual(before["job"][0], "running")
        self.assertEqual(before["execution"][1], "running")
        self.assertEqual(before["executions"], 1)
        self.assertEqual(before["artifacts"], 0)
        execute_job(job_id)
        self.assertEqual(self.snapshot(), before)
        self.provider.generate.assert_not_called()
        self.store.put_bytes.assert_not_called()

    def test_execution_creation_failure_rolls_back_claim_before_dispatch(self):
        job_id = self.submit_job()
        with patch("app.jobs.worker.create_execution", side_effect=RuntimeError("attempt failed")):
            with self.assertRaisesRegex(RuntimeError, "attempt failed"):
                execute_job(job_id)
        state = self.snapshot()
        self.assertEqual(state["job"][0], "queued")
        self.assertIsNone(state["job"][1])
        self.assertEqual(state["executions"], 0)
        self.provider.generate.assert_not_called()
        execute_job(job_id)
        self.assert_completed_once()

    def test_missing_job_still_raises_without_dispatch(self):
        with patch("app.jobs.worker.get_handler") as dispatch:
            with self.assertRaisesRegex(RuntimeError, "Job not found"):
                execute_job("missing-job")
        dispatch.assert_not_called()
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(Execution)))


class HandlerOutcomeTests(ImageExecutionTestCase):
    def create_test_engine(self):
        directory = self.enterContext(TemporaryDirectory())
        return create_engine(
            f"sqlite:///{Path(directory) / 'outcomes.sqlite'}",
            connect_args={"check_same_thread": False},
        )

    def submit_noop(self):
        response = self.client.post("/v2/jobs", json={"capability": "system.noop"})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["job_id"]

    def outcome_handler(self, result):
        def handle(input_data, *, tenant_id, user_id):
            with self.sessions() as db:
                job = db.scalar(select(Job).where(Job.status == "running"))
                self.assertIsNotNone(job)
                execution = db.scalar(select(Execution).where(Execution.job_id == job.job_id))
                self.assertEqual(execution.status, "running")
            self.assertEqual((tenant_id, user_id), ("tenant-one", "user-one"))
            return result

        handler = Mock(side_effect=handle)
        self.enterContext(patch.dict(HANDLERS, {"system.noop": handler}))
        return handler

    def pending(self):
        return Pending(
            provider="test-provider", model="test-model", operation_id="operation-one",
            operation_metadata={"state": "submitted", "checkpoint": {"region": "test"}},
        )

    def read_state(self, job_id, sessions=None):
        with (sessions or self.sessions)() as db:
            job = db.scalar(select(Job).where(Job.job_id == job_id))
            execution = db.scalar(select(Execution).where(Execution.job_id == job_id))
            return {
                "job_status": job.status, "job_completed_at": job.completed_at,
                "job_error": job.error_message, "execution_id": execution.execution_id,
                "execution_status": execution.status,
                "execution_completed_at": execution.completed_at,
                "execution_error": execution.error_message,
                "provider": execution.provider, "model": execution.model,
                "metadata": execution.metadata_json,
                "executions": db.scalar(select(func.count()).select_from(Execution).where(
                    Execution.job_id == job_id,
                )),
            }

    def test_image_handler_explicitly_completes_without_changing_image_behavior(self):
        response = self.submit_image()
        self.assertEqual(response.status_code, 202, response.text)
        with self.sessions() as db:
            job_id = db.scalar(select(Job.job_id))

        def generate(input_data, *, tenant_id, user_id):
            result = execute_image_generation(
                input_data, tenant_id=tenant_id, user_id=user_id,
            )
            self.assertIsInstance(result, Completed)
            return result

        with patch.dict(HANDLERS, {"image.generate": generate}):
            execute_job(job_id)
        state = self.read_state(job_id)
        self.assertEqual(state["job_status"], "completed")
        self.assertEqual(state["execution_status"], "completed")
        self.assertEqual(state["metadata"]["request_id"], response.json()["request_id"])
        self.provider.generate.assert_called_once()
        self.store.put_bytes.assert_called_once()
        with self.sessions() as db:
            image = db.scalar(select(ImageRequest))
            self.assertEqual(image.status, "completed")
            self.assertEqual(image.artifact_id, state["metadata"]["artifact_id"])

    def test_completed_persists_provider_model_and_metadata(self):
        job_id = self.submit_noop()
        handler = self.outcome_handler(Completed(
            provider="test-provider", model="test-model", metadata={"output": "done"},
        ))
        execute_job(job_id)
        handler.assert_called_once()
        state = self.read_state(job_id)
        self.assertEqual(state["job_status"], "completed")
        self.assertEqual(state["execution_status"], "completed")
        self.assertIsNotNone(state["job_completed_at"])
        self.assertIsNotNone(state["execution_completed_at"])
        self.assertEqual((state["provider"], state["model"]), ("test-provider", "test-model"))
        self.assertEqual(state["metadata"], {"capability": "system.noop", "output": "done"})

    def test_pending_checkpoint_survives_a_fresh_database_connection(self):
        job_id = self.submit_noop()
        handler = self.outcome_handler(self.pending())
        execute_job(job_id)
        handler.assert_called_once()

        # Read only committed state through a new engine/session; no worker
        # object or connection is needed to recover the provider operation ID.
        fresh_engine = create_engine(self.engine.url)
        try:
            state = self.read_state(job_id, sessionmaker(bind=fresh_engine))
        finally:
            fresh_engine.dispose()
        self.assertEqual(state["job_status"], "waiting")
        self.assertEqual(state["execution_status"], "waiting")
        self.assertIsNone(state["job_completed_at"])
        self.assertIsNone(state["execution_completed_at"])
        self.assertIsNone(state["job_error"])
        self.assertIsNone(state["execution_error"])
        self.assertEqual((state["provider"], state["model"]), ("test-provider", "test-model"))
        self.assertTrue(state["metadata"].pop("continuation")["id"])
        self.assertEqual(state["metadata"], {
            "capability": "system.noop",
            "provider_operation": {
                "operation_id": "operation-one",
                "metadata": {"state": "submitted", "checkpoint": {"region": "test"}},
            },
        })
        self.assertEqual(self.queue.enqueue.call_count, 2)  # Submission and delayed continuation.
        self.provider_factory.assert_not_called()
        self.store.put_bytes.assert_not_called()

    def test_waiting_redelivery_preserves_checkpoint_without_dispatch(self):
        job_id = self.submit_noop()
        handler = self.outcome_handler(self.pending())
        execute_job(job_id)
        before = self.read_state(job_id)
        with patch("app.jobs.worker.get_handler") as dispatch:
            execute_job(job_id)
            execute_job(job_id)
        dispatch.assert_not_called()
        handler.assert_called_once()
        self.assertEqual(self.read_state(job_id), before)
        self.assertEqual(before["executions"], 1)
        self.assertEqual(self.queue.enqueue.call_count, 4)  # Submission, poll, two safe republishes.

    def test_invalid_returns_fail_explicitly_instead_of_completing(self):
        for result in (None, {}, "completed", object()):
            with self.subTest(result_type=type(result).__name__):
                job_id = self.submit_noop()
                self.outcome_handler(result)
                with self.assertRaisesRegex(TypeError, "Handler must return Completed or Pending"):
                    execute_job(job_id)
                state = self.read_state(job_id)
                self.assertEqual(state["job_status"], "failed")
                self.assertEqual(state["execution_status"], "failed")
                self.assertIn("Completed or Pending", state["job_error"])
                self.assertNotIn("provider_operation", state["metadata"])

    def test_pending_requires_nonempty_operation_identity(self):
        for field in ("provider", "model", "operation_id"):
            with self.subTest(field=field):
                fields = {"provider": "test-provider", "model": "test-model", "operation_id": "op"}
                fields[field] = " "
                with self.assertRaisesRegex(ValueError, f"Pending {field}"):
                    Pending(**fields)

    def test_crash_before_checkpoint_commit_leaves_submission_ambiguous(self):
        job_id = self.submit_noop()
        handler = self.outcome_handler(self.pending())
        with patch("app.jobs.worker.wait_execution", side_effect=SystemExit("crash")):
            with self.assertRaises(SystemExit):
                execute_job(job_id)
        state = self.read_state(job_id)
        self.assertEqual(state["job_status"], "running")
        self.assertEqual(state["execution_status"], "running")
        self.assertEqual(state["metadata"], {"capability": "system.noop"})
        execute_job(job_id)
        handler.assert_called_once()
        self.assertEqual(self.read_state(job_id), state)

    def test_crash_after_checkpoint_commit_preserves_operation_for_future_resume(self):
        job_id = self.submit_noop()
        handler = self.outcome_handler(self.pending())

        def crash_after_waiting_commit(session):
            for value in session.identity_map.values():
                if isinstance(value, Job) and value.job_id == job_id and value.status == "waiting":
                    raise SystemExit("crash after commit")

        event.listen(self.sessions, "after_commit", crash_after_waiting_commit)
        try:
            with self.assertRaises(SystemExit):
                execute_job(job_id)
        finally:
            event.remove(self.sessions, "after_commit", crash_after_waiting_commit)

        fresh_engine = create_engine(self.engine.url)
        try:
            state = self.read_state(job_id, sessionmaker(bind=fresh_engine))
        finally:
            fresh_engine.dispose()
        self.assertEqual(state["job_status"], "waiting")
        self.assertEqual(state["execution_status"], "waiting")
        self.assertEqual(state["metadata"]["provider_operation"]["operation_id"], "operation-one")
        execute_job(job_id)
        handler.assert_called_once()
        self.assertEqual(self.read_state(job_id), state)


if __name__ == "__main__":
    unittest.main()
