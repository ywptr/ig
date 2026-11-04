from datetime import datetime

from sqlalchemy import select, update

from app.db.database import SessionLocal
from app.db.models import Job
from app.jobs.handlers import get_handler
from app.jobs.result import Pending
from app.jobs.continuation import schedule_continuation
from app.executions.service import (
    apply_execution_result,
    complete_execution,
    create_execution,
    fail_execution,
    wait_execution,
)

import logging
from app.core.logging import configure_logging

configure_logging()

logger = logging.getLogger(__name__)

def execute_job(job_id: str) -> None:
    """Dispatch only a queued job, once per committed claim.

    Running/waiting/terminal jobs are not reclaimed on redelivery. This prevents
    repeat provider calls but leaves crash recovery to a separate design.
    """
    logger.info(
        "job.worker.start job_id=%s",
        job_id,
    )
    db = SessionLocal()
    job = None
    execution = None
    claimed = False

    try:
        # A single conditional write decides ownership. Concurrent deliveries
        # must not dispatch based on a separate read of the queued status.
        claim = db.execute(
            update(Job)
            .where(Job.job_id == job_id, Job.status == "queued")
            .values(status="running", started_at=datetime.utcnow())
        )

        if claim.rowcount != 1:
            db.rollback()
            if db.scalar(select(Job.job_id).where(Job.job_id == job_id)) is None:
                raise RuntimeError(f"Job not found: {job_id}")
            # Running, waiting and terminal jobs are never implicitly retried.
            # A crashed executor can leave running state needing recovery.
            logger.info("job.worker.skip job_id=%s reason=not_queued", job_id)
            db.rollback()
            schedule_continuation(job_id)
            return

        job = db.scalar(select(Job).where(Job.job_id == job_id))

        execution = create_execution(
            db,
            job_id=job.job_id,
            tenant_id=job.tenant_id,
            user_id=job.user_id,
            metadata={
                "capability": job.capability,
            },
        )
        # Commit the claim and attempt together before any provider side effect.
        # If this transaction fails, no handler has run and the claim rolls back.
        db.commit()
        claimed = True

        handler = get_handler(job.capability)

        # Ownership comes from persisted Job columns, never from input_json.
        result = handler(
            job.input_json,
            tenant_id=job.tenant_id,
            user_id=job.user_id,
        )

        # Reject implicit/invalid outcomes before any completion transition.
        apply_execution_result(
            execution,
            result,
        )

        if isinstance(result, Pending):
            wait_execution(db, execution)
            job.status = "waiting"
            job.completed_at = None
        else:
            complete_execution(db, execution)
            job.status = "completed"
            job.completed_at = datetime.utcnow()

        # The operation checkpoint and both waiting statuses commit together.
        db.commit()

    except Exception as exc:
        db.rollback()
        # Only the worker with a committed claim can finalize this attempt.
        if claimed:
            fail_execution(
                db,
                execution,
                error_message=str(exc),
            )
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.utcnow()

            db.commit()

        raise

    finally:
        db.close()

    if isinstance(result, Pending):
        schedule_continuation(job_id)
