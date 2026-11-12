"""Optional concurrency checks, random schemas on an explicitly disposable local DB."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import os
from threading import Barrier, Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from tests import test_video_resiliency as resiliency
from app.core.time import utc_now, utc_iso
from app.db.models import Execution
from app.executions.context import execution_scope
from app.jobs.cancellation import JobCancelled
from app.jobs.video_lifecycle import begin_submission, checkpoint_submission
from app.jobs.worker import execute_job
from app.jobs.result import Pending, Completed
from tests.video_support import TASK_ID

URL = os.getenv("IG_TEST_VIDEO_POSTGRES_URL")


@unittest.skipUnless(URL, "Set explicit disposable local PostgreSQL test URL")
class VideoRecoveryPostgresTests(resiliency.VideoResiliencyTests):
    def create_test_engine(self):
        url = make_url(URL)
        if url.host not in {"localhost", "127.0.0.1"} or url.get_backend_name() != "postgresql":
            raise ValueError("Video tests require disposable localhost PostgreSQL")
        schema = "video_" + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def cleanup():
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
        self.addCleanup(cleanup)
        return create_engine(url, connect_args={"options": f"-csearch_path={schema}"})

    def test_submission_boundary_and_recovery_serialize(self):
        job_id, execution_id = self.crash_prepared()
        now, barrier = utc_now() + timedelta(seconds=301), Barrier(2)
        def submit():
            barrier.wait(timeout=5)
            try:
                with execution_scope(job_id, execution_id): begin_submission(self.sessions)
                return True
            except JobCancelled:
                return False
        def recover():
            barrier.wait(timeout=5)
            return self.recover(now=now)
        with patch("app.jobs.video_lifecycle.utc_now", return_value=now), ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(submit), pool.submit(recover)
            allowed, recovered = first.result(timeout=10), second.result(timeout=10)
        self.assertEqual((allowed, recovered["reset"]), (True, 0) if allowed else (False, 1))
        self.http.assert_not_called()

    def test_checkpoint_and_ambiguous_recovery_keep_operation_identity(self):
        job_id, execution_id = self.crash_prepared()
        with execution_scope(job_id, execution_id): begin_submission(self.sessions)
        with self.sessions() as db:
            execution = db.get(Execution, execution_id)
            metadata = deepcopy(execution.metadata_json)
            metadata["video_lifecycle"]["submission_lease_expires_at"] = utc_iso(utc_now() - timedelta(seconds=1))
            execution.metadata_json = metadata
            db.commit()
        barrier = Barrier(2)
        def checkpoint():
            barrier.wait(timeout=5)
            with execution_scope(job_id, execution_id):
                checkpoint_submission(self.sessions, Pending("alibaba", "wan3.0-video", TASK_ID))
        def recover():
            barrier.wait(timeout=5)
            return self.recover()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(checkpoint), pool.submit(recover)]
            for future in futures: future.result(timeout=10)
        status, execution_status, metadata = self.state(job_id)
        self.assertIn(status, {"waiting", "failed"})
        self.assertEqual(status, execution_status)
        self.assertEqual(metadata["provider_operation"]["operation_id"], TASK_ID)
        self.http.assert_not_called()

    def test_old_worker_cannot_complete_replacement_execution(self):
        job_id = self.submit_video().json()["job_id"]
        entered, release = Event(), Event()
        from app.jobs.handlers import get_handler
        original = get_handler("video.generate")
        count = 0
        def initial(*args, **kwargs):
            entered.set()
            if not release.wait(10): raise TimeoutError()
            return Completed(provider="alibaba", model="wan3.0-video")
        def handler(capability):
            nonlocal count
            count += 1
            return initial if count == 1 else original
        with patch("app.jobs.worker.get_handler", side_effect=handler), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(execute_job, job_id)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.recover(now=utc_now() + timedelta(seconds=301))["reset"], 1)
                execute_job(job_id)
            finally:
                release.set()
            future.result(timeout=10)
        self.assertEqual(self.state(job_id)[:2], ("waiting", "waiting"))
        self.assertEqual(self.http.call_count, 1)
