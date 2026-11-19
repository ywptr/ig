from collections.abc import Callable
from datetime import timedelta
from pkgutil import resolve_name

from redis import Redis, WatchError
from rq import Queue, Retry
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job, JobStatus

from ..capabilities import QueueCapabilities
from ..config import RQConfig
from ..contracts import CancelResult, CancelStatus, DeliveryReceipt, whole_second_delay
from ..delivery import Delivery

Invocation = Callable[[Delivery], tuple[Callable, tuple]]


def dispatch_rq(handler: str, message: dict[str, object]) -> None:
    delivery = Delivery.from_message(message)
    resolve_name(handler)(delivery)


class RQBackend:
    capabilities = QueueCapabilities(
        delayed_delivery=True,
        retract_pending=True,
        terminate_running=False,
        track_by_work_id=True,
        deduplicate_delivery_id=False,
    )

    def __init__(self, config: RQConfig, *, invocation: Invocation | None = None):
        if invocation is None and not config.handler:
            raise ValueError("RQ requires an importable worker handler")
        self.config = config
        self.redis = Redis.from_url(config.redis_url)
        self.queue = Queue(config.queue_name, connection=self.redis)
        self.tracking_prefix = config.tracking_prefix or f"{config.queue_name}:deliveries:"
        self.invocation = invocation or self._envelope_invocation

    def _envelope_invocation(self, delivery: Delivery) -> tuple[Callable, tuple]:
        return dispatch_rq, (self.config.handler, delivery.to_message())

    def enqueue(self, delivery: Delivery) -> DeliveryReceipt:
        return self.schedule(delivery, delay_seconds=0)

    def schedule(self, delivery: Delivery, *, delay_seconds: float) -> DeliveryReceipt:
        delay = whole_second_delay(delay_seconds)
        function, args = self.invocation(delivery)
        options = {"job_timeout": self.config.job_timeout_seconds}
        retry = self.config.continuation_retry
        if delivery.kind == "continue" and retry is not None:
            options["retry"] = Retry(max=retry.max_retries, interval=retry.interval_seconds)

        if delay:
            job = self.queue.enqueue_in(timedelta(seconds=delay), function, *args, **options)
        else:
            job = self.queue.enqueue(function, *args, **options)
        if isinstance(job.id, str):
            key = self.tracking_prefix + delivery.work_id
            with self.redis.pipeline() as pipe:
                pipe.sadd(key, job.id)
                pipe.expire(key, self.config.tracking_ttl_seconds)
                pipe.execute()
        return DeliveryReceipt(delivery.work_id, delivery.delivery_id, job.id)

    def cancel(self, work_id: str) -> CancelResult:
        removed = 0
        running = False
        conflicted = False
        for transport_id in self.redis.smembers(self.tracking_prefix + work_id):
            if isinstance(transport_id, bytes):
                transport_id = transport_id.decode("utf-8")
            try:
                job = Job.fetch(transport_id, connection=self.redis)
                status = self._retract(job)
            except (NoSuchJobError, InvalidJobOperation):
                continue
            removed += status == CancelStatus.RETRACTED
            running |= status == CancelStatus.ALREADY_RUNNING
            conflicted |= status == CancelStatus.ERROR
        if conflicted:
            return CancelResult(CancelStatus.ERROR, removed)
        if removed:
            return CancelResult(CancelStatus.RETRACTED, removed)
        if running:
            return CancelResult(CancelStatus.ALREADY_RUNNING)
        return CancelResult(CancelStatus.NOT_FOUND)

    def _retract(self, job: Job) -> CancelStatus:
        # Watch the hash so a concurrent status update invalidates the transaction.
        for _ in range(3):
            with self.redis.pipeline() as pipe:
                try:
                    pipe.watch(job.key)
                    status = job.get_status(refresh=True)
                    if status == JobStatus.STARTED:
                        return CancelStatus.ALREADY_RUNNING
                    if status not in (JobStatus.QUEUED, JobStatus.SCHEDULED, JobStatus.DEFERRED):
                        return CancelStatus.NOT_FOUND
                    pipe.multi()
                    job.cancel(pipeline=pipe)
                    pipe.execute()
                    return CancelStatus.RETRACTED
                except WatchError:
                    continue
        return CancelStatus.ERROR
