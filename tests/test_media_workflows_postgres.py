"""Opt-in concurrency coverage on an explicitly disposable localhost PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from tests import test_media_job_contracts as fixtures
from tests.test_media_workflows import CHOICE
from app.db.models import Job
from app.jobs.service import create_job
from app.workflows import service
from app.workflows.presets import WorkflowSubmission

URL = os.getenv("IG_TEST_WORKFLOW_POSTGRES_URL")


@skipUnless(URL, "Requires explicit disposable local PostgreSQL")
class WorkflowPostgresTests(fixtures.MediaJobTestCase):
    def create_test_engine(self):
        url = make_url(URL)
        if url.host not in {"localhost", "127.0.0.1"} or url.get_backend_name() != "postgresql":
            raise ValueError("Workflow tests require disposable localhost PostgreSQL")
        schema = "workflow_" + uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        def cleanup():
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()

        self.addCleanup(cleanup)
        return create_engine(url, connect_args={"options": f"-csearch_path={schema}"})

    def test_concurrent_wakes_create_one_job(self):
        request = WorkflowSubmission(preset="single_generation", operation="text_to_image", prompt="A cloud")
        with self.sessions() as db, patch("app.workflows.presets.model_choices", return_value={"text_to_image": [CHOICE]}):
            run_id = service.create_workflow(db, "tenant-one", "user-one", request, "concurrent").workflow_id

        def submit_step(db, row, step, references):
            return create_job(db, "test.workflow", {}, row.tenant_id, row.user_id, defer_dispatch=True).job_id

        barrier = Barrier(2)

        def wake():
            barrier.wait(timeout=5)
            service.advance_workflow(run_id)

        with (patch.object(service, "SessionLocal", self.sessions),
              patch.object(service, "get_job_queue", return_value=self.queue),
              patch.object(service, "submit_step", side_effect=submit_step) as submit,
              ThreadPoolExecutor(max_workers=2) as pool):
            futures = [pool.submit(wake) for _ in range(2)]
            for future in futures:
                future.result(timeout=10)
        submit.assert_called_once()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Job)), 1)
