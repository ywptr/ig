"""Resume external operations with committed leases and fenced finalization.

No database session or transaction survives across the provider status read.
Status reads may repeat after lease expiry; they must never submit generation.
"""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import and_, or_, select, update

from app.db.database import SessionLocal
from app.db.models import Execution, Job
from app.executions.service import apply_execution_result, complete_execution, wait_execution
from app.jobs.handlers import get_resume_handler
from app.jobs.provider import get_job_queue
from app.jobs.result import Completed, HandlerResult, Pending, ProviderOperation

CONTINUATION_DELAY_SECONDS = 30
# Internal lease policy. Provider status-read timeouts must be shorter than this.
POLL_LEASE_SECONDS = 120


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _lease_time(value: datetime) -> str:
    # Fixed-width UTC strings sort chronologically in PostgreSQL and SQLite JSON.
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class _PollClaim:
    job_id: str
    execution_id: str
    continuation_id: str
    claim_id: str
    tenant_id: str
    user_id: str
    capability: str
    operation: ProviderOperation


def schedule_continuation(job_id: str) -> bool:
    """Republish waiting work or an expired polling lease; never reset a Job.

    Internal repair entry point, not an HTTP API. Active leases are left alone.
    """
    with SessionLocal() as db:
        execution = db.scalar(
            select(Execution).join(Job, Job.job_id == Execution.job_id).where(
                Job.job_id == job_id, Job.status == Execution.status,
                or_(
                    Execution.status == "waiting",
                    and_(
                        Execution.status == "polling",
                        Execution.metadata_json["continuation"]["lease_expires_at"].as_string()
                        <= _lease_time(_utc_now()),
                    ),
                ),
            )
        )
        if execution is None:
            return False
        continuation_id = (execution.metadata_json or {}).get("continuation", {}).get("id")
        if not continuation_id:
            raise RuntimeError("Execution has no continuation checkpoint token")

    get_job_queue().enqueue(
        job_id, kind="continue", continuation_id=continuation_id,
        delay_seconds=CONTINUATION_DELAY_SECONDS,
    )
    return True


def _claim_operation(job_id: str, continuation_id: str) -> _PollClaim | None:
    with SessionLocal() as db:
        row = db.execute(select(Execution, Job).join(Job, Job.job_id == Execution.job_id).where(
            Job.job_id == job_id, Job.status == Execution.status,
            Execution.status.in_(("waiting", "polling")),
            Execution.metadata_json["continuation"]["id"].as_string() == continuation_id,
        )).first()
        if row is None:
            return None
        execution, job = row
        if (
            not job.user_id or not job.tenant_id
            or execution.user_id != job.user_id or execution.tenant_id != job.tenant_id
        ):
            raise RuntimeError("Continuation ownership does not match trusted Job ownership")

        metadata = deepcopy(execution.metadata_json)
        continuation = metadata["continuation"]
        checkpoint = metadata.get("provider_operation") or {}
        saved = Pending(
            provider=execution.provider, model=execution.model,
            operation_id=checkpoint.get("operation_id"),
            operation_metadata=deepcopy(checkpoint.get("metadata", {})),
        )
        operation = ProviderOperation(
            saved.provider, saved.model, saved.operation_id, saved.operation_metadata,
        )
        now = _utc_now()
        previous_claim_id = continuation.get("claim_id")
        claim_id = str(uuid4())
        continuation.update(
            claim_id=claim_id,
            lease_expires_at=_lease_time(now + timedelta(seconds=POLL_LEASE_SECONDS)),
        )

        # The read only supplies a snapshot. This conditional write atomically
        # claims waiting work OR reclaims an expired lease. The claim-ID guard
        # rejects a competing owner even when the checkpoint token is unchanged.
        claimed = db.execute(update(Execution).where(
            Execution.execution_id == execution.execution_id,
            Execution.tenant_id == job.tenant_id, Execution.user_id == job.user_id,
            Execution.provider == operation.provider, Execution.model == operation.model,
            Execution.metadata_json["provider_operation"]["operation_id"].as_string() == operation.operation_id,
            Execution.metadata_json["continuation"]["id"].as_string() == continuation_id,
            Execution.metadata_json["continuation"]["claim_id"].as_string() == previous_claim_id,
            or_(
                Execution.status == "waiting",
                and_(
                    Execution.status == "polling",
                    Execution.metadata_json["continuation"]["lease_expires_at"].as_string()
                    <= _lease_time(now),
                ),
            ),
        ).values(status="polling", metadata_json=metadata)
          .execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            db.rollback()
            return None
        job_claimed = db.execute(update(Job).where(
            Job.job_id == job_id, Job.status == execution.status,
            Job.tenant_id == job.tenant_id, Job.user_id == job.user_id,
            Job.capability == job.capability,
        ).values(status="polling").execution_options(synchronize_session=False))
        if job_claimed.rowcount != 1:
            db.rollback()
            return None

        # Capture plain data BEFORE commit expires ORM attributes. Do not return
        # ORM objects that could lazily open another transaction during polling.
        claim = _PollClaim(
            job_id, execution.execution_id, continuation_id, claim_id,
            job.tenant_id, job.user_id, job.capability, operation,
        )
        db.commit()
        return claim


def _finalize_claim(claim: _PollClaim, result: HandlerResult) -> bool:
    operation = claim.operation
    if isinstance(result, Pending):
        if (result.provider, result.model, result.operation_id) != (
            operation.provider, operation.model, operation.operation_id,
        ):
            raise ValueError("Continuation cannot replace the persisted provider operation")
    elif isinstance(result, Completed):
        if result.provider not in (None, operation.provider) or result.model not in (None, operation.model):
            raise ValueError("Continuation cannot replace the persisted provider/model")
        result = replace(result, provider=operation.provider, model=operation.model)
    else:
        raise TypeError("Handler must return Completed or Pending")

    with SessionLocal() as db:
        # A conditional no-op update both fences stale workers and locks the row
        # for this short finalization transaction. Lease expiry alone does not
        # invalidate an owner; a replacement claim does.
        owned = db.execute(update(Execution).where(
            Execution.execution_id == claim.execution_id,
            Execution.job_id == claim.job_id, Execution.status == "polling",
            Execution.tenant_id == claim.tenant_id, Execution.user_id == claim.user_id,
            Execution.metadata_json["continuation"]["id"].as_string() == claim.continuation_id,
            Execution.metadata_json["continuation"]["claim_id"].as_string() == claim.claim_id,
            Execution.provider == operation.provider, Execution.model == operation.model,
            Execution.metadata_json["provider_operation"]["operation_id"].as_string() == operation.operation_id,
        ).values(status="polling").execution_options(synchronize_session=False))
        if owned.rowcount != 1:
            db.rollback()
            return False
        job_owned = db.execute(update(Job).where(
            Job.job_id == claim.job_id, Job.status == "polling",
            Job.tenant_id == claim.tenant_id, Job.user_id == claim.user_id,
            Job.capability == claim.capability,
        ).values(status="polling").execution_options(synchronize_session=False))
        if job_owned.rowcount != 1:
            db.rollback()
            return False

        execution = db.get(Execution, claim.execution_id)
        job = db.scalar(select(Job).where(Job.job_id == claim.job_id))
        prior_operation = deepcopy(execution.metadata_json["provider_operation"])
        prior_continuation = dict(execution.metadata_json["continuation"])
        apply_execution_result(execution, result)
        if isinstance(result, Pending):
            # apply_execution_result rotates the token and drops the old lease.
            wait_execution(db, execution)
            job.status = "waiting"
        else:
            prior_continuation.pop("claim_id", None)
            prior_continuation.pop("lease_expires_at", None)
            execution.metadata_json = {
                **execution.metadata_json,
                "provider_operation": prior_operation,
                "continuation": prior_continuation,
            }
            complete_execution(db, execution)
            job.status = "completed"
            job.completed_at = datetime.utcnow()
        db.commit()
        return True


def continue_job(job_id: str, continuation_id: str) -> None:
    """Claim/commit, poll without a DB session, then finalize under the fence."""
    if not isinstance(continuation_id, str) or not continuation_id:
        raise ValueError("Continuation requires a checkpoint token")
    claim = _claim_operation(job_id, continuation_id)
    if claim is None:
        # Also repairs scheduling when an older delivery committed a successor
        # but failed to enqueue it. Active polling leases are not republished.
        schedule_continuation(job_id)
        return

    handler = get_resume_handler(claim.capability)
    result = handler(
        claim.operation, tenant_id=claim.tenant_id, user_id=claim.user_id,
    )
    if not _finalize_claim(claim, result):
        return  # A newer owner/checkpoint superseded this worker.
    if isinstance(result, Pending):
        schedule_continuation(job_id)
