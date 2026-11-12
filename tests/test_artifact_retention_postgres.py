"""Optional real PostgreSQL lifecycle, migration and locking checks.

IG_TEST_RETENTION_POSTGRES_URL must target a disposable local PostgreSQL database.
Each test creates and drops its own random schema; never uses DATABASE_URL.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Barrier
import os
import unittest
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from tests import test_artifact_retention as retention_tests
from app.artifacts.retention import cleanup
from app.db.models import Artifact, Tenant, Job
from app.jobs.service import create_job

URL = os.getenv("IG_TEST_RETENTION_POSTGRES_URL")


@unittest.skipUnless(URL, "Set explicit disposable local PostgreSQL test URL")
class PostgresRetentionTests(retention_tests.RetentionTests):
    def create_test_engine(self):
        url = make_url(URL)
        if url.host not in {"127.0.0.1", "localhost"} or url.get_backend_name() != "postgresql":
            raise ValueError("Retention integration tests require disposable local PostgreSQL")
        schema = "retention_" + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def remove_schema():
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
        self.addCleanup(remove_schema)
        return create_engine(url, connect_args={"options": f"-csearch_path={schema}"})

    def test_input_admission_lock_serializes_cleanup(self):
        identity = self.artifact()
        with self.sessions() as db, ThreadPoolExecutor(max_workers=1) as pool:
            create_job(db, capability="image.generate", input_data={"references": [{"artifact_id": identity}]},
                       tenant_id="tenant-one", user_id="user-one", defer_dispatch=True)
            started = Event()
            def concurrent_cleanup():
                with self.sessions() as worker:
                    started.set()
                    return cleanup(worker, now=self.now, store=self.store)
            future = pool.submit(concurrent_cleanup)
            self.assertTrue(started.wait(5))
            db.commit()
            self.assertEqual(future.result(timeout=10)["deleted"], 0)
        with self.sessions() as db:
            self.assertIsNone(db.get(Artifact, identity).cleanup_started_at)

    def test_committed_cleanup_fence_rejects_concurrent_input(self):
        identity = self.artifact()
        deleting, finish = Event(), Event()
        original_delete = self.store.delete
        def slow_delete(uri):
            deleting.set()
            if not finish.wait(10): raise TimeoutError()
            original_delete(uri)
        with patch.object(self.store, "delete", side_effect=slow_delete), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.run_cleanup)
            try:
                self.assertTrue(deleting.wait(5))
                with self.sessions() as db:
                    with self.assertRaisesRegex(ValueError, "unavailable"):
                        create_job(db, capability="image.generate", input_data={"references": [{"artifact_id": identity}]},
                                   tenant_id="tenant-one", user_id="user-one", defer_dispatch=True)
                    db.rollback()
            finally:
                finish.set()
            self.assertEqual(future.result(timeout=10)["deleted"], 1)

    def test_concurrent_admissions_with_existing_foreign_key_locks(self):
        barrier = Barrier(2)
        def submit(index):
            with self.sessions() as db:
                # Real image submission can flush an ImageRequest before create_job.
                # This insert holds the same tenant FK key-share lock.
                db.add(Job(job_id=f"prior-{index}", tenant_id="tenant-one", user_id="user-one",
                           capability="image.generate", status="completed", input_json={}))
                db.flush()
                barrier.wait(timeout=5)
                job = create_job(db, capability="image.generate", input_data={}, tenant_id="tenant-one",
                                 user_id="user-one", defer_dispatch=True)
                identity = job.job_id
                db.commit()
                return identity
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, index) for index in range(2)]
            self.assertEqual(len({future.result(timeout=10) for future in futures}), 2)
