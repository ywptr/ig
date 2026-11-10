"""Cancellation uses real HTTP/ORM/workers and synthetic providers only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock, get_ident
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event, func, select
from tests import test_media_job_contracts as contracts
from app.db.models import Job, Execution, Artifact, ImageRequest
from app.jobs.handlers import HANDLERS, RESUME_HANDLERS
from app.jobs.worker import execute_job
from app.jobs.continuation import continue_job, _claim_operation, _finalize_claim, schedule_continuation
from app.jobs.queue import QueueCancellation
from app.jobs.result import Completed, Pending
from app.jobs.service import create_job
from app.artifacts.types import ArtifactPayload
from app.auth.dependencies import get_current_user
from app.main import app


class _CancellationFixture(contracts.MediaJobTestCase):
    def create_test_engine(self):
        directory=self.enterContext(TemporaryDirectory())
        return create_engine(f"sqlite:///{Path(directory)/'jobs.sqlite'}",connect_args={"check_same_thread":False,"timeout":10})

    def setUp(self):
        super().setUp()
        self.queue.cancel=Mock(return_value=QueueCancellation(True,1,"Pending delivery removed; no upstream cancellation"))
        self.enterContext(patch('app.jobs.worker.SessionLocal',self.sessions))
        self.handler=Mock(return_value=Completed())
        self.poll=Mock(return_value=Completed())
        self.enterContext(patch.dict(HANDLERS,{'test.cancel':self.handler}))
        self.enterContext(patch.dict(RESUME_HANDLERS,{'test.cancel':self.poll}))

    def job(self):
        with self.sessions() as db:
            return create_job(db,'test.cancel',{},'tenant-one','user-one').job_id

    def cancel(self,job_id,**kwargs):
        return self.client.post(f'/v2/jobs/{job_id}/cancel',**kwargs)

    def status(self,job_id):
        with self.sessions() as db:
            return db.scalar(select(Job.status).where(Job.job_id==job_id))

    def assert_canceled(self,job_id):
        with self.sessions() as db:
            job=db.scalar(select(Job).where(Job.job_id==job_id))
            self.assertEqual(job.status,'canceled')
            self.assertIsNotNone(job.cancel_requested_at)
            self.assertIsNotNone(job.canceled_at)
            self.assertEqual(job.completed_at,job.canceled_at)
            for execution in db.scalars(select(Execution).where(Execution.job_id==job_id)):
                self.assertEqual(execution.status,'canceled')
                self.assertIsNotNone(execution.completed_at)

    def waiting(self):
        self.handler.return_value=Pending('fake','model','operation',{})
        job_id=self.job(); execute_job(job_id)
        with self.sessions() as db:
            execution=db.scalar(select(Execution).where(Execution.job_id==job_id))
            return job_id,execution.metadata_json['continuation']['id']

class CancellationTests(_CancellationFixture):
    def test_cancel_queued_prevents_dispatch_even_on_redelivery(self):
        job_id=self.job()
        response=self.cancel(job_id)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'canceled')
        self.assertFalse(response.json()['cancellation']['provider_cancel_supported'])
        self.queue.cancel.assert_called_once_with(job_id)
        execute_job(job_id); execute_job(job_id)
        self.handler.assert_not_called()
        self.assert_canceled(job_id)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Execution)),0)

    def test_running_request_is_cooperative_and_repeated_request_idempotent(self):
        job_id=self.job()
        def during_call(*args,**kwargs):
            for _ in range(2):
                response=self.cancel(job_id)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['status'],'cancel_requested')
                self.assertIsNone(response.json()['canceled_at'])
            return Completed()
        self.handler.side_effect=during_call
        execute_job(job_id)
        self.handler.assert_called_once()
        self.assert_canceled(job_id)
        self.assertEqual(self.cancel(job_id).status_code,409)

    def test_provider_error_after_cancellation_does_not_reclassify_as_failed_or_retry(self):
        job_id=self.job()
        def error(*args,**kwargs):
            self.assertEqual(self.cancel(job_id).status_code,200)
            raise RuntimeError('provider timed out after cancellation')
        self.handler.side_effect=error
        execute_job(job_id); execute_job(job_id)
        self.handler.assert_called_once()
        self.assert_canceled(job_id)

    def test_cancel_before_provider_dispatch_checkpoint_skips_handler(self):
        job_id=self.job()
        def resolve(capability):
            self.assertEqual(self.cancel(job_id).status_code,200)
            from app.jobs.cancellation import check_cancellation
            check_cancellation()
            return self.handler
        with patch('app.jobs.worker.get_handler',side_effect=resolve): execute_job(job_id)
        self.handler.assert_not_called()
        self.assert_canceled(job_id)

    def test_terminal_jobs_and_repeated_terminal_cancellation_conflict(self):
        for status in ('completed','failed','canceled'):
            job_id=self.job()
            with self.sessions() as db:
                job=db.scalar(select(Job).where(Job.job_id==job_id)); job.status=status; db.commit()
            self.assertEqual(self.cancel(job_id).status_code,409)
            self.assertEqual(self.status(job_id),status)

    def test_isolation_and_authentication(self):
        job_id=self.job()
        self.assertEqual(self.cancel(job_id,headers={'host':'other.test'}).status_code,404)
        self.user_id='user-two'
        self.assertEqual(self.cancel(job_id).status_code,404)
        self.user_id='user-one'
        del app.dependency_overrides[get_current_user]
        self.assertEqual(self.cancel(job_id).status_code,401)
        self.assertEqual(self.status(job_id),'queued')
        self.queue.cancel.assert_not_called()

    def test_missing_job_not_found(self):
        self.assertEqual(self.cancel('missing').status_code,404)

    def test_queue_retraction_failure_does_not_undo_cancellation(self):
        job_id=self.job(); self.queue.cancel.side_effect=RuntimeError('transport unavailable')
        response=self.cancel(job_id)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['cancellation']['queue']['supported'])
        execute_job(job_id)
        self.handler.assert_not_called()
        self.assert_canceled(job_id)

    def test_vercel_reports_unsupported_retraction_but_database_fences_work(self):
        from app.jobs.vercel_queue import VercelJobQueue
        self.queue.cancel.side_effect=VercelJobQueue().cancel
        job_id=self.job()
        response=self.cancel(job_id)
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['cancellation']['queue']['supported'])
        execute_job(job_id); self.handler.assert_not_called()

    def test_waiting_operation_cancellation_prevents_poll_and_rescheduling(self):
        job_id,token=self.waiting()
        self.assertEqual(self.cancel(job_id).json()['status'],'canceled')
        continue_job(job_id,token)
        self.assertFalse(schedule_continuation(job_id))
        self.poll.assert_not_called()
        self.assert_canceled(job_id)
        with self.sessions() as db:
            metadata=db.scalar(select(Execution.metadata_json).where(Execution.job_id==job_id))
            self.assertEqual(metadata['provider_operation']['operation_id'],'operation')

    def test_cancel_during_poll_skips_artifact_staging_and_completion(self):
        job_id,token=self.waiting()
        def polling(*args,**kwargs):
            response=self.cancel(job_id)
            self.assertEqual(response.json()['status'],'cancel_requested')
            return Completed(artifact=ArtifactPayload('video','video/mp4','mp4',b'fake',{}))
        self.poll.side_effect=polling
        with patch('app.jobs.continuation.stage_media_result') as stage:
            continue_job(job_id,token)
        stage.assert_not_called()
        self.assert_canceled(job_id)

    def test_cancel_after_poll_claim_before_call_skips_provider(self):
        job_id,token=self.waiting()
        def get_handler(capability):
            self.cancel(job_id)
            return self.poll
        with patch('app.jobs.continuation.get_resume_handler',side_effect=get_handler):
            continue_job(job_id,token)
        self.poll.assert_not_called()
        self.assert_canceled(job_id)

    def test_cancel_during_storage_fences_artifact_row(self):
        job_id,token=self.waiting(); claim=_claim_operation(job_id,token)
        def stage(payload):
            self.cancel(job_id)
            return 'file:///staged-result.mp4','staged-result.mp4'
        with patch('app.jobs.continuation.stage_media_result',side_effect=stage):
            self.assertFalse(_finalize_claim(claim,Completed(artifact=ArtifactPayload('video','video/mp4','mp4',b'fake',{}))))
        self.assert_canceled(job_id)
        with self.sessions() as db: self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)),0)

    def test_canceled_submission_keeps_external_operation_id_without_polling(self):
        job_id=self.job()
        def submit(*args,**kwargs):
            self.cancel(job_id)
            return Pending('fake','model','external-operation',{})
        self.handler.side_effect=submit
        execute_job(job_id)
        self.assert_canceled(job_id)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Execution.metadata_json))['provider_operation']['operation_id'],'external-operation')

    def test_completion_wins_before_late_cancel(self):
        job_id=self.job(); execute_job(job_id)
        self.assertEqual(self.cancel(job_id).status_code,409)
        self.assertEqual(self.status(job_id),'completed')

    def test_queued_cancel_and_worker_reservation_race_has_one_outcome(self):
        job_id=self.job(); barrier=Barrier(2); lock=Lock(); arrived=set()
        def before(conn,cursor,statement,params,context,many):
            writes=statement.partition(' WHERE ')[0]
            if statement.startswith('UPDATE jobs SET') and ('started_at=' in writes or 'cancel_requested_at=' in writes):
                with lock:
                    first=get_ident() not in arrived; arrived.add(get_ident())
                if first: barrier.wait(timeout=10)
        event.listen(self.engine,'before_cursor_execute',before)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                worker=pool.submit(execute_job,job_id); request=pool.submit(self.cancel,job_id)
                worker.result(timeout=20); response=request.result(timeout=20)
        finally: event.remove(self.engine,'before_cursor_execute',before)
        self.assertIn(response.status_code,(200,409))
        self.assertEqual(self.status(job_id),'canceled' if response.status_code==200 else 'completed')
        if response.status_code==200:self.assert_canceled(job_id)

    def test_enqueue_error_does_not_overwrite_already_canceled_job(self):
        def enqueue(job_id):
            self.cancel(job_id)
            raise RuntimeError('enqueue acknowledgment lost')
        self.queue.enqueue.side_effect=enqueue
        with self.assertRaises(RuntimeError):self.job()
        with self.sessions() as db:self.assertEqual(db.scalar(select(Job.status)),'canceled')


class ImageCancellationTests(contracts.ImageExecutionTestCase):
    def test_inflight_image_response_is_discarded_before_storage(self):
        self.queue.cancel=Mock(return_value=QueueCancellation(True,0,'no queued deliveries'))
        response=self.submit_image()
        self.assertEqual(response.status_code,202)
        with self.sessions() as db: job_id=db.scalar(select(Job.job_id))
        result=self.provider.generate.return_value
        def generate(request):
            response=self.client.post(f'/v2/jobs/{job_id}/cancel')
            self.assertEqual(response.json()['status'],'cancel_requested')
            return result
        self.provider.generate.side_effect=generate
        execute_job(job_id)
        self.provider.generate.assert_called_once()
        self.store.put_bytes.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(Job.status)),'canceled')
            self.assertEqual(db.scalar(select(ImageRequest.status)),'canceled')
            self.assertEqual(db.scalar(select(func.count()).select_from(Artifact)),0)


class JobTimingTests(_CancellationFixture):
    def timing(self,failed=False):
        start=datetime(2026,11,9,tzinfo=timezone.utc); end=start+timedelta(seconds=5)
        job_id=self.job()
        if failed:self.handler.side_effect=RuntimeError('synthetic failure')
        with patch('app.jobs.worker.utc_now',side_effect=[start,end]), patch('app.executions.service.utc_now',side_effect=[start,end]):
            if failed:
                with self.assertRaises(RuntimeError):execute_job(job_id)
            else:execute_job(job_id)
        detail=self.client.get(f'/v2/jobs/{job_id}').json()
        self.assertEqual((datetime.fromisoformat(detail['completed_at'])-datetime.fromisoformat(detail['started_at'])).total_seconds(),5)
        self.assertTrue(detail['started_at'].endswith('+00:00'))
        summary=next(j for j in self.client.get('/v2/jobs').json() if j['job_id']==job_id)
        self.assertEqual(summary['started_at'],detail['started_at'])
        execution=self.client.get(f'/v2/jobs/{job_id}/executions').json()[0]
        self.assertEqual((datetime.fromisoformat(execution['completed_at'])-datetime.fromisoformat(execution['started_at'])).total_seconds(),5)

    def test_queued_to_completed_duration(self):self.timing()
    def test_failed_duration(self):self.timing(failed=True)

    def test_running_to_completed_duration(self):
        def observe_running(*args,**kwargs):
            with self.sessions() as db:
                self.assertEqual(db.scalar(select(Job.status)), 'running')
            return Completed()
        self.handler.side_effect=observe_running
        self.timing()

    def test_aware_local_inputs_are_normalized_without_changing_instants(self):
        job_id=self.job()
        with self.sessions() as db:
            job=db.scalar(select(Job).where(Job.job_id==job_id))
            job.started_at=datetime(2026,11,9,8,tzinfo=timezone(timedelta(hours=8)))
            job.completed_at=job.started_at+timedelta(seconds=5); job.status='completed'; db.commit()
            db.refresh(job)
            self.assertEqual(job.started_at.hour,0)
            self.assertEqual(job.started_at.utcoffset(),timedelta(0))
        detail=self.client.get(f'/v2/jobs/{job_id}').json()
        self.assertEqual(detail['started_at'],'2026-11-09T00:00:00+00:00')

    def test_running_api_timestamps_have_an_explicit_offset(self):
        job_id=self.job()
        with self.sessions() as db:
            job=db.scalar(select(Job).where(Job.job_id==job_id))
            job.started_at=datetime(2026,11,9); job.status='running'; db.commit()
        detail=self.client.get(f'/v2/jobs/{job_id}').json()
        start=datetime.fromisoformat(detail['started_at'])
        now=datetime(2026,11,9,0,0,5,tzinfo=timezone.utc)
        self.assertEqual((now-start).total_seconds(),5)
        self.assertIsNone(detail['completed_at'])
