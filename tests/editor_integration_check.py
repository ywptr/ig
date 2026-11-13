"""Disposable PostgreSQL editor races/migration and RQ regression checks."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from sqlalchemy import inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests import service_integration_check as infrastructure
from tests.test_editor import EditorTests

create_engine = infrastructure.PostgresServiceTests.create_test_engine


class PostgresEditorTests(EditorTests):
    create_test_engine = create_engine

    def test_concurrent_redemption_has_exactly_one_winner(self):
        launch, barrier = self.launch().json(), Barrier(2)
        def redeem():
            barrier.wait(timeout=5)
            return self.redeem(launch).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: redeem(), range(2)))
        self.assertEqual(sorted(results), [200, 410])

    def test_concurrent_attempt_retries_share_one_durable_job(self):
        headers, barrier = self.editor_headers(), Barrier(2)
        def submit():
            barrier.wait(timeout=5)
            response = self.submit_editor(headers)
            self.assertEqual(response.status_code, 202, response.text)
            return response.json()["job_id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(jobs[0], jobs[1])
        self.assertEqual(len(self.client.get("/v2/jobs", headers=headers).json()), 1)

    def test_editor_migration_round_trip_preserves_existing_rows(self):
        spec = importlib.util.spec_from_file_location("editor_migration", Path(__file__).resolve().parents[1] / "alembic/versions/1a0010000010_editor_sessions.py")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        schema = self.engine.get_execution_options()["schema_translate_map"][None]
        with self.engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                before = connection.scalar(text("SELECT count(*) FROM service_credentials"))
                migration.upgrade()
                self.assertEqual(connection.scalar(text("SELECT count(*) FROM service_credentials")), before)
                self.assertIn("editor_sessions", inspect(connection).get_table_names(schema=schema))
                self.assertEqual(connection.scalar(text("SELECT editor_origins::text FROM service_credentials LIMIT 1")), "[]")


if __name__ == "__main__":
    with patch.object(infrastructure, "PostgresServiceTests", PostgresEditorTests):
        infrastructure.main()
