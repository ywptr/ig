"""Explicit disposable PostgreSQL + real RQ check: python -m tests.client_integration_check.

Reuses the service-access harness lifecycle. No deployment URLs or paid providers.
"""
import os
from unittest.mock import patch

from rq import SimpleWorker

from tests import service_integration_check as infrastructure
from tests import test_integration_client as helpers
from app.jobs.rq_queue import RQJobQueue

create_engine = infrastructure.PostgresServiceTests.create_test_engine


class PostgresClientTests(helpers.ClientServerTests):
    create_test_engine = create_engine

    def test_helper_real_rq_job_result_and_same_key_recovery(self):
        with patch.dict(os.environ, {"REDIS_URL": infrastructure.REDIS_URL}):
            queue = RQJobQueue()
            self.addCleanup(queue.redis.close)
            with patch("app.media.submissions.get_job_queue", return_value=queue):
                first = self.helper.submit_generation(helpers.CONTEXT, correlation_id="rq-scene", request=helpers.IMAGE)
                retry = self.helper.submit_generation(helpers.CONTEXT, correlation_id="rq-scene", request=helpers.IMAGE)
            self.assertEqual(first.job_id, retry.job_id)
            self.assertEqual(queue.queue.count, 1)
            SimpleWorker([queue.queue], connection=queue.redis).work(burst=True, logging_level="WARNING")
        result = self.helper.generate_and_wait(helpers.CONTEXT, correlation_id="rq-scene", request=helpers.IMAGE, timeout=5)
        self.assertEqual(result.job.status, "completed")
        self.assertEqual(result.artifacts[0].media_type, "image")
        self.provider.generate.assert_called_once()


if __name__ == "__main__":
    with patch.object(infrastructure, "PostgresServiceTests", PostgresClientTests):
        infrastructure.main()
