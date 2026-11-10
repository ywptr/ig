from copy import deepcopy
import logging
from sqlalchemy import select, update

from app.core.time import utc_now
from app.db.database import SessionLocal
from app.db.models import Job, Execution
from app.jobs.handlers import get_handler
from app.jobs.result import Pending
from app.jobs.cancellation import (
    JobCancelled, cancellation_scope, check_cancellation, acknowledge_cancellation,
)
from app.jobs.continuation import schedule_continuation
from app.executions.service import (
    apply_execution_result, complete_execution, create_execution, fail_execution, wait_execution,
)
from app.core.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


def _own_running(db, job_id):
    return db.execute(update(Job).where(
        Job.job_id == job_id, Job.status == "running",
    ).values(status="running").execution_options(synchronize_session=False)).rowcount == 1


def execute_job(job_id: str) -> None:
    """Claim once, execute cooperatively, and fence completion against cancellation."""
    logger.info("job.worker.start job_id=%s", job_id)
    db = SessionLocal()
    claimed = False
    result = None
    try:
        claim = db.execute(update(Job).where(
            Job.job_id == job_id, Job.status == "queued",
        ).values(status="running", started_at=utc_now()))
        if claim.rowcount != 1:
            db.rollback()
            if db.scalar(select(Job.job_id).where(Job.job_id == job_id)) is None:
                raise RuntimeError(f"Job not found: {job_id}")
            db.rollback()
            schedule_continuation(job_id)
            return
        job = db.scalar(select(Job).where(Job.job_id == job_id))
        execution = create_execution(db, job_id=job_id, tenant_id=job.tenant_id,
            user_id=job.user_id, metadata={"capability": job.capability})
        capability, tenant_id, user_id = job.capability, job.tenant_id, job.user_id
        input_data, execution_id = deepcopy(job.input_json), execution.execution_id
        db.commit()
        claimed = True
        with cancellation_scope(job_id, SessionLocal):
            check_cancellation()
            result = get_handler(capability)(input_data, tenant_id=tenant_id, user_id=user_id)
        if not _own_running(db, job_id):
            acknowledge_cancellation(db, job_id, result)
            db.commit()
            return
        job = db.scalar(select(Job).where(Job.job_id == job_id).execution_options(populate_existing=True))
        execution = db.get(Execution, execution_id, populate_existing=True)
        apply_execution_result(execution, result)
        if isinstance(result, Pending):
            wait_execution(db, execution)
            job.status = "waiting"
            job.completed_at = None
        else:
            complete_execution(db, execution)
            job.status = "completed"
            job.completed_at = utc_now()
        db.commit()
    except JobCancelled:
        db.rollback()
        acknowledge_cancellation(db, job_id)
        db.commit()
        return
    except Exception as exc:
        db.rollback()
        if claimed:
            if not _own_running(db, job_id):
                canceled = acknowledge_cancellation(db, job_id)
                db.commit()
                if canceled:
                    return
            else:
                job = db.scalar(select(Job).where(Job.job_id == job_id).execution_options(populate_existing=True))
                execution = db.get(Execution, execution_id, populate_existing=True)
                fail_execution(db, execution, error_message=str(exc))
                job.status = "failed"
                job.error_message = str(exc)
                job.completed_at = utc_now()
                db.commit()
        raise
    finally:
        db.close()
    if isinstance(result, Pending):
        schedule_continuation(job_id)
