"""Explicit local-only integration run: python -m tests.service_integration_check.

Starts its own PostgreSQL 17 cluster and Redis over private temporary Unix sockets.
Never reads deployment connection strings or operates on existing databases/queues.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import json
import sys
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from threading import Barrier, local
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema
from redis import Redis
from rq import SimpleWorker

from tests import test_service_access as access
from tests import test_rq_cancellation_integration as redis_tests
from app.db.models import MediaSubmissionKey
from app.jobs.rq_queue import RQJobQueue
from app.jobs.worker import execute_job

POSTGRES_URL = None
REDIS_URL = None


class PostgresServiceTests(access.ServiceAccessTests):
    def create_test_engine(self):
        engine = create_engine(POSTGRES_URL)
        schema = "service_test_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
        return engine.execution_options(schema_translate_map={None: schema})

    def test_concurrent_identical_submissions_share_one_committed_job(self):
        barrier = Barrier(2)
        state = local()
        original = Session.flush

        def flush(db, *args, **kwargs):
            if not getattr(state, "reserved", False) and any(isinstance(row, MediaSubmissionKey) for row in db.new):
                state.reserved = True
                barrier.wait(timeout=15)
            return original(db, *args, **kwargs)

        with patch.object(Session, "flush", new=flush):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: self.submit(), range(2)))
        for response in responses:
            self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(responses[0].json()["job_id"], responses[1].json()["job_id"])
        self.assertEqual(self.job_count(), 1)
        execute_job(responses[0].json()["job_id"])
        self.provider.generate.assert_called_once()

    def test_real_rq_submission_and_client_retry_execute_provider_once(self):
        with patch.dict(os.environ, {"REDIS_URL": REDIS_URL}):
            queue = RQJobQueue()
            self.addCleanup(queue.redis.close)
            with patch("app.media.submissions.get_job_queue", return_value=queue):
                first, retry = self.submit(), self.submit()
            self.assertEqual(first.json()["job_id"], retry.json()["job_id"])
            self.assertEqual(queue.queue.count, 1)
            SimpleWorker([queue.queue], connection=queue.redis).work(burst=True, logging_level="WARNING")
        self.provider.generate.assert_called_once()
        self.assertEqual(self.submit().json()["status"], "completed")

    def test_independent_api_process_recovers_committed_submission(self):
        first = self.submit().json()
        schema = self.engine.get_execution_options()["schema_translate_map"][None]
        code = """
import json, sys
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
request = json.load(sys.stdin)
with patch('app.media.submissions.get_job_queue', side_effect=AssertionError('Duplicate must not enqueue')):
    with TestClient(app) as client:
        response = client.post('/v2/media/generations', headers=request['headers'], json=request['body'])
print(json.dumps({'status': response.status_code, 'response': response.json()}))
"""
        env = {**os.environ, "DATABASE_URL": POSTGRES_URL, "PGOPTIONS": f"-c search_path={schema}"}
        result = subprocess.run([sys.executable, "-B", "-c", code], env=env,
            input=json.dumps({"headers": {**self.headers(), "Idempotency-Key": "execution-1"},
                              "body": {"media_type": "image", "intent": {"description": "A castle"}}}),
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        recovered = json.loads(result.stdout)
        self.assertEqual(recovered["status"], 202, recovered)
        self.assertEqual(recovered["response"]["job_id"], first["job_id"])
        self.assertEqual(self.job_count(), 1)

    def test_new_migration_runs_on_postgresql(self):
        import importlib.util
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        engine = self.create_test_engine()
        self.addCleanup(engine.dispose)
        excluded = {"service_credentials", "media_submission_keys"}
        tables = [table for table in access.contracts.Base.metadata.sorted_tables if table.name not in excluded]
        access.contracts.Base.metadata.create_all(engine, tables=tables)
        spec = importlib.util.spec_from_file_location("service_migration", access.ROOT / "alembic/versions/1a0001000001_service_access_and_submission_keys.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Alembic creates unqualified tables; pin this disposable test schema.
        schema = engine.get_execution_options()["schema_translate_map"][None]
        with engine.begin() as connection:
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                self.assertTrue(excluded <= set(inspect(connection).get_table_names(schema=schema)))
                module.downgrade()
                self.assertTrue({table.name for table in tables} <= set(inspect(connection).get_table_names(schema=schema)))


def main():
    global POSTGRES_URL, REDIS_URL
    binaries = Path("/usr/lib/postgresql/17/bin")
    if not (binaries / "initdb").is_file():
        raise SystemExit("This explicit check requires local PostgreSQL 17 binaries and redis-server")
    with TemporaryDirectory(prefix="ig-s2s-") as directory:
        root = Path(directory)
        data = root / "pgdata"
        subprocess.run([str(binaries / "initdb"), "-D", str(data), "-U", "igtest", "-A", "trust", "--no-locale", "--encoding=UTF8"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run([str(binaries / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"),
                        "-o", f"-h '' -k {root} -p 55441 -F", "-w", "start"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        redis = None
        try:
            POSTGRES_URL = f"postgresql+psycopg://igtest@/postgres?host={root}&port=55441"
            REDIS_URL = f"unix://{root}/redis.sock"
            redis = subprocess.Popen(["redis-server", "--port", "0", "--unixsocket", str(root / "redis.sock"),
                "--save", "", "--appendonly", "no"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            connection = Redis.from_url(REDIS_URL)
            try:
                for _ in range(100):
                    try:
                        if connection.ping():
                            break
                    except Exception:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("Disposable Redis did not start")
            finally:
                connection.close()
            with patch.dict(os.environ, {"IG_TEST_REDIS_URL": REDIS_URL}):
                # Decorator was evaluated before this owned Redis existed.
                redis_tests.RQCancellationIntegrationTests.__unittest_skip__ = False
                suite = unittest.TestSuite([
                    unittest.defaultTestLoader.loadTestsFromTestCase(PostgresServiceTests),
                    unittest.defaultTestLoader.loadTestsFromTestCase(redis_tests.RQCancellationIntegrationTests),
                ])
                result = unittest.TextTestRunner(verbosity=1).run(suite)
        finally:
            if redis is not None:
                redis.terminate()
                redis.wait(timeout=10)
            subprocess.run([str(binaries / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not result.wasSuccessful():
            raise SystemExit(1)


if __name__ == "__main__":
    main()
