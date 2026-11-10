import logging
import uuid

from sqlalchemy import select, update
from app.core.time import utc_now
from app.jobs.cancellation import cancel_executions
from app.jobs.queue import QueueCancellation
from sqlalchemy.orm import Session

from app.db.models import Job
from app.jobs.provider import get_job_queue

from app.core.logging import job_trace_enabled

logger = logging.getLogger(__name__)

def create_job(
    db: Session,
    capability: str,
    input_data: dict,
    tenant_id: str,
    user_id: str | None = None,
    *, defer_dispatch: bool = False,
) -> Job:
    job = Job(
        job_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        user_id=user_id,
        capability=capability,
        status="queued",
        input_json=input_data,
    )

    db.add(job)
    if defer_dispatch:
        # Caller atomically commits Job + client submission identity, then queues.
        db.flush()
        return job
    db.commit()
    db.refresh(job)

    if job_trace_enabled():
        logger.info(
            "job.created job_id=%s capability=%s tenant_id=%s user_id=%s",
            job.job_id,
            job.capability,
            job.tenant_id,
            job.user_id,
        )

    try:
        job_queue = get_job_queue()

        if job_trace_enabled():
            logger.info(
                "job.enqueue.start job_id=%s provider=%s",
                job.job_id,
                type(job_queue).__name__,
            )

        job_queue.enqueue(job.job_id)

        if job_trace_enabled():
            logger.info(
                "job.enqueue.complete job_id=%s",
                job.job_id,
            )

    except Exception as exc:
        logger.exception(
            "Failed to enqueue job %s",
            job.job_id,
        )

        # A reservation/cancellation may already have won while enqueue returned.
        db.execute(update(Job).where(Job.job_id == job.job_id, Job.status == "queued")
            .values(status="failed", completed_at=utc_now(), error_message=f"Failed to enqueue job: {exc}"))

        db.commit()
        raise

    return job


def get_job(
    db: Session,
    *,
    job_id: str,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> Job | None:
    statement = select(Job).where(
        Job.job_id == job_id
    )

    if user_id is not None:
        statement = statement.where(
            Job.user_id == user_id
        )

    if tenant_id is not None:
        statement = statement.where(
            Job.tenant_id == tenant_id
        )

    return db.scalar(statement)

class CancellationConflict(RuntimeError):
    pass


def cancel_job(db: Session, *, job_id: str, tenant_id: str, user_id: str):
    for _ in range(4):
        db.expire_all()
        job = get_job(db, job_id=job_id, tenant_id=tenant_id, user_id=user_id)
        if job is None:
            return None
        status = job.status
        if status in {"completed", "failed", "canceled"}:
            raise CancellationConflict(f"Job is already {status}")
        if status == "cancel_requested":
            db.rollback()
            break  # Repeated in-flight requests are idempotent.
        if status not in {"queued", "running", "waiting", "polling"}:
            raise CancellationConflict("Job cannot be canceled in its current state")
        now = utc_now()
        values = {"status": "cancel_requested", "cancel_requested_at": now}
        if status in {"queued", "waiting"}:
            values.update(status="canceled", canceled_at=now, completed_at=now)
        changed = db.execute(update(Job).where(
            Job.job_id == job_id, Job.tenant_id == tenant_id, Job.user_id == user_id,
            Job.status == status,
        ).values(**values).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            db.rollback()
            continue
        if values["status"] == "canceled":
            cancel_executions(db, job_id, now)
        db.commit()  # This database fence is authoritative before touching transport.
        break
    else:
        raise CancellationConflict("Job state changed concurrently; retry cancellation")
    try:
        transport = get_job_queue().cancel(job_id)
    except Exception as exc:
        logger.warning("job.cancel.transport_failed job_id=%s error_type=%s", job_id, type(exc).__name__)
        transport = QueueCancellation(False, 0,
            "Queue retraction unavailable; committed IG state still fences delivery. No provider call was canceled.")
    db.expire_all()
    return get_job(db, job_id=job_id, tenant_id=tenant_id, user_id=user_id), transport
