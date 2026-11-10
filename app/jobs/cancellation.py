"""Queue-neutral cooperative cancellation using trusted worker context."""
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import select, update
from app.core.time import utc_now
from app.db.models import Job, Execution, ImageRequest
from app.jobs.result import Pending


class JobCancelled(RuntimeError):
    pass


_scope = ContextVar("ig_job_cancellation", default=None)


@contextmanager
def cancellation_scope(job_id, sessions):
    token = _scope.set((job_id, sessions))
    try:
        yield
    finally:
        _scope.reset(token)


def check_cancellation():
    """Authorize a safe boundary before external work, with no open transaction.

    If cancellation wins this SQL race no call may start. If this boundary wins,
    that call is in-flight for cancellation purposes; its result remains fenced.
    Direct legacy handler callers without a worker scope retain their behavior.
    """
    scope = _scope.get()
    if scope is None:
        return
    job_id, sessions = scope
    with sessions() as db:
        owned = db.execute(update(Job).where(
            Job.job_id == job_id, Job.status.in_(("running", "polling")),
        ).values(status=Job.status))
        if owned.rowcount != 1:
            raise JobCancelled("IG job no longer permits further work")
        db.commit()


def cancel_executions(db, job_id, now):
    db.execute(update(Execution).where(
        Execution.job_id == job_id,
        Execution.status.in_(("running", "waiting", "polling")),
    ).values(status="canceled", completed_at=now).execution_options(synchronize_session=False))
    job = db.scalar(select(Job).where(Job.job_id == job_id).execution_options(populate_existing=True))
    request_id = (job.input_json or {}).get("request_id")
    if request_id and job.capability == "image.generate":
        db.execute(update(ImageRequest).where(
            ImageRequest.request_id == request_id,
            ImageRequest.tenant_id == job.tenant_id, ImageRequest.user_id == job.user_id,
        ).values(status="canceled").execution_options(synchronize_session=False))


def acknowledge_cancellation(db, job_id, pending_result=None) -> bool:
    """Commit together with the caller; always lock Job before Execution."""
    now = utc_now()
    changed = db.execute(update(Job).where(
        Job.job_id == job_id, Job.status == "cancel_requested",
    ).values(status="canceled", canceled_at=now, completed_at=now)
      .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        return False
    if isinstance(pending_result, Pending):
        from app.executions.service import apply_execution_result
        execution = db.scalar(select(Execution).where(
            Execution.job_id == job_id, Execution.status == "running",
        ).execution_options(populate_existing=True))
        if execution:
            # Keep the external operation identity for diagnosis, without polling it.
            apply_execution_result(execution, pending_result)
            db.flush()
    cancel_executions(db, job_id, now)
    return True
