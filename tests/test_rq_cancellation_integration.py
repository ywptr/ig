"""Opt-in real Redis/RQ tests. Only UUID-namespaced owned keys are cleaned up."""
import os
import unittest
from uuid import uuid4
from unittest.mock import patch

# Ensure import-time DB setup is isolated even when this file is run alone.
from tests import test_media_job_contracts as contracts
from redis import Redis
from rq import Queue, SimpleWorker
from rq.job import JobStatus
from app.jobs.rq_queue import RQJobQueue


@unittest.skipUnless(os.getenv('IG_TEST_REDIS_URL'), 'requires explicitly selected test Redis URL')
class RQCancellationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.connection=Redis.from_url(os.environ['IG_TEST_REDIS_URL'])
        self.connection.ping()
        self.name='ig-cancellation-test-'+str(uuid4())
        self.queue=Queue(self.name,connection=self.connection)
        self.job_id='ig-test-'+str(uuid4())
        with patch.dict(os.environ,{'REDIS_URL':os.environ['IG_TEST_REDIS_URL']}):
            self.adapter=RQJobQueue()
        self.adapter.queue=self.queue
        self.deliveries=[]
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for delivery in self.deliveries:
            delivery.delete()
        self.queue.delete(delete_jobs=True)
        self.connection.delete('ig:deliveries:'+self.job_id)
        self.connection.close()

    def test_pending_delivery_removed_and_worker_has_nothing_to_dispatch(self):
        self.adapter.enqueue(self.job_id)
        self.deliveries=self.queue.get_jobs()
        self.assertEqual(self.queue.count,1)
        result=self.adapter.cancel(self.job_id)
        self.assertEqual(result.removed,1)
        self.assertEqual(self.queue.count,0)
        with patch('app.jobs.worker.execute_job') as provider_dispatch:
            SimpleWorker([self.queue],connection=self.connection).work(burst=True,logging_level='WARNING')
        provider_dispatch.assert_not_called()

    def test_scheduled_continuation_removed(self):
        self.adapter.enqueue(self.job_id,kind='continue',continuation_id='checkpoint',delay_seconds=30)
        ids=self.queue.scheduled_job_registry.get_job_ids()
        self.assertEqual(len(ids),1)
        self.deliveries=[self.queue.fetch_job(i) for i in ids]
        result=self.adapter.cancel(self.job_id)
        self.assertEqual(result.removed,1)
        self.assertEqual(self.queue.scheduled_job_registry.count,0)

    def test_running_transport_job_is_not_stopped_or_marked_canceled(self):
        self.adapter.enqueue(self.job_id)
        self.deliveries=self.queue.get_jobs()
        delivery=self.deliveries[0]
        delivery.set_status(JobStatus.STARTED)
        result=self.adapter.cancel(self.job_id)
        self.assertEqual(result.removed,0)
        self.assertEqual(delivery.get_status(refresh=True),JobStatus.STARTED)

    def test_reservation_race_does_not_label_started_transport_canceled(self):
        from rq.job import Job as RQJob
        self.adapter.enqueue(self.job_id)
        self.deliveries=self.queue.get_jobs()
        original=RQJob.cancel
        def reserve(delivery, *args, **kwargs):
            delivery.set_status(JobStatus.STARTED)
            return original(delivery,*args,**kwargs)
        with patch.object(RQJob,'cancel',new=reserve):
            result=self.adapter.cancel(self.job_id)
        self.assertEqual(result.removed,0)
        self.assertEqual(self.deliveries[0].get_status(refresh=True),JobStatus.STARTED)
