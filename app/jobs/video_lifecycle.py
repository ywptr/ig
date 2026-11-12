"""Provider-neutral durable video submission, deadlines and safe diagnostics."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging
import os

from sqlalchemy import select
from sqlalchemy.orm import object_session
from app.core.time import as_utc, utc_now, utc_iso
from app.db.models import Execution, Job
from app.executions.context import current_execution
from app.jobs.cancellation import JobCancelled, acknowledge_cancellation

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "canceled"}


def setting(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
        if not minimum <= value <= maximum:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ValueError(f"Invalid {name}") from None


def poll_interval():
    return setting("VIDEO_POLL_INTERVAL_SECONDS", 30, 5, 300)


def submission_lease():
    return setting("VIDEO_SUBMISSION_LEASE_SECONDS", 300, 120, 900)


def instant(value):
    return as_utc(datetime.fromisoformat(value))


def audit(job, execution, action, now=None):
    metadata = execution.metadata_json if isinstance(execution.metadata_json, dict) else {}
    lifecycle = metadata.get("video_lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    now = now or utc_now()
    started = lifecycle.get("started_at")
    try:
        elapsed = max(0, int((as_utc(now) - instant(started)).total_seconds())) if started else None
    except (ValueError, TypeError):
        elapsed = None
    checkpoint = metadata.get("provider_operation")
    operation_present = isinstance(checkpoint, dict) and bool(checkpoint.get("operation_id"))
    logger.info("video.lifecycle %s", json.dumps({
        "job_id": job.job_id, "execution_id": execution.execution_id,
        "tenant_id": job.tenant_id, "provider": execution.provider, "model": execution.model,
        "provider_operation_present": operation_present,
        "action": action, "job_status": job.status, "execution_status": execution.status,
        "submission_phase": lifecycle.get("phase"), "poll_count": lifecycle.get("poll_count", 0),
        "elapsed_pending_seconds": elapsed,
        "error_code": metadata.get("error_code"), "cancel_requested": job.cancel_requested_at is not None,
    }, sort_keys=True))


def initial_metadata(now):
    lifetime = setting("VIDEO_MAX_PENDING_SECONDS", 7200, 60, 82800)
    return {"phase": "prepared", "started_at": utc_iso(now),
            "deadline_at": utc_iso(now + timedelta(seconds=lifetime)),
            "submission_lease_expires_at": utc_iso(now + timedelta(seconds=submission_lease())), "poll_count": 0}


def ensure_lifecycle(execution):
    if execution.metadata_json is not None and not isinstance(execution.metadata_json, dict):
        raise ValueError("Invalid video execution metadata")
    metadata = deepcopy(execution.metadata_json or {})
    if "video_lifecycle" in metadata and not isinstance(metadata["video_lifecycle"], dict):
        raise ValueError("Invalid video lifecycle metadata")
    if "video_lifecycle" not in metadata:
        metadata["video_lifecycle"] = initial_metadata(execution.started_at or execution.created_at)
        metadata["video_lifecycle"]["phase"] = "checkpointed" if metadata.get("provider_operation") else "unknown"
        execution.metadata_json = metadata
    return metadata["video_lifecycle"]


def expired(execution, now):
    return as_utc(now) >= instant(ensure_lifecycle(execution)["deadline_at"])


def terminal_failure(job, execution, code, now):
    if job.status in TERMINAL or execution.status in TERMINAL:
        return False
    if job.status == "cancel_requested":
        acknowledge_cancellation(object_session(execution), job.job_id)
        return False
    metadata = deepcopy(execution.metadata_json) if isinstance(execution.metadata_json, dict) else {}
    metadata["error_code"] = code
    continuation = metadata.get("continuation", {})
    continuation = dict(continuation) if isinstance(continuation, dict) else {}
    continuation.pop("claim_id", None)
    continuation.pop("lease_expires_at", None)
    metadata["continuation"] = continuation
    execution.metadata_json = metadata
    execution.status = job.status = "failed"
    execution.completed_at = job.completed_at = now
    messages = {"VIDEO_PENDING_TIMEOUT": "Video operation exceeded its overall deadline; provider cancellation was not performed",
                "VIDEO_SUBMISSION_UNCERTAIN": "Video submission outcome is uncertain; automatic resubmission is disabled",
                "VIDEO_CHECKPOINT_INVALID": "Video execution checkpoint is inconsistent; operator inspection required",
                "VIDEO_PREPARATION_RETRIES_EXHAUSTED": "Video preparation repeatedly interrupted before provider submission",
                "VIDEO_PROVIDER_FAILED": "Video provider permanently rejected or terminated the operation"}
    execution.error_message = job.error_message = f"{code}: {messages[code]}"
    audit(job, execution, code, now)
    return True


def begin_submission(sessions):
    """Commit the non-retryable boundary immediately before any paid submit call."""
    identity = current_execution()
    if identity is None:
        return  # Direct adapter/legacy unit callers have no orchestration identity.
    with sessions() as db:
        job = db.scalar(select(Job).where(Job.job_id == identity.job_id).with_for_update())
        execution = db.get(Execution, identity.execution_id)
        if (not job or not execution or job.status != "running" or execution.status != "running"
            or execution.job_id != job.job_id or execution.tenant_id != job.tenant_id or execution.user_id != job.user_id):
            raise JobCancelled("Video submission is no longer owned")
        lifecycle = ensure_lifecycle(execution)
        now = utc_now()
        if expired(execution, now):
            terminal_failure(job, execution, "VIDEO_PENDING_TIMEOUT", now)
            db.commit()
            raise JobCancelled("Video deadline expired before submission")
        if lifecycle["phase"] != "prepared":
            raise JobCancelled("Video submission is not safely retryable")
        metadata = deepcopy(execution.metadata_json)
        metadata["video_lifecycle"].update(phase="submitting", submission_started_at=utc_iso(now),
            submission_lease_expires_at=utc_iso(now + timedelta(seconds=submission_lease())))
        execution.metadata_json = metadata
        audit(job, execution, "submission_started", now)
        db.commit()


def checkpoint_submission(sessions, result):
    """Persist an obtained operation before returning to the general worker.

    Retry only the DB publication, never the provider call. A hard crash before
    publication remains ambiguous without an upstream idempotency contract.
    """
    identity = current_execution()
    if identity is None:
        return
    from app.executions.service import apply_execution_result, wait_execution
    for attempt in range(3):
        try:
            with sessions() as db:
                job = db.scalar(select(Job).where(Job.job_id == identity.job_id).with_for_update())
                execution = db.get(Execution, identity.execution_id)
                if not job or not execution:
                    raise RuntimeError("Video publication identity missing")
                if (execution.job_id != job.job_id or execution.tenant_id != job.tenant_id or execution.user_id != job.user_id
                    or (execution.provider, execution.model) != (result.provider, result.model)):
                    raise RuntimeError("Video checkpoint identity mismatch")
                stored = (execution.metadata_json or {}).get("provider_operation") or {}
                if stored.get("operation_id") and stored["operation_id"] != result.operation_id:
                    raise RuntimeError("Video operation identity cannot be replaced")
                if job.status == "cancel_requested":
                    acknowledge_cancellation(db, job.job_id, result)
                elif job.status in TERMINAL or execution.status in TERMINAL:
                    # Late response after cancellation/recovery timeout: preserve
                    # operation identity privately without reviving the execution.
                    if not stored.get("operation_id"):
                        execution.provider, execution.model = result.provider, result.model
                        execution.metadata_json = {**(execution.metadata_json or {}), "provider_operation": {
                            "operation_id": result.operation_id, "metadata": result.operation_metadata}}
                elif job.status == "waiting" and execution.status == "waiting" and (execution.metadata_json or {}).get("provider_operation", {}).get("operation_id") == result.operation_id:
                    return  # A previous commit succeeded despite a lost acknowledgement.
                elif job.status == "running" and execution.status == "running":
                    apply_execution_result(execution, result)
                    wait_execution(db, execution)
                    job.status = "waiting"
                else:
                    raise RuntimeError("Unexpected video submission state")
                audit(job, execution, "submission_checkpointed")
                db.commit()
                return
        except Exception:
            if attempt == 2:
                raise RuntimeError("Video operation checkpoint publication failed") from None


def error_code(message):
    code = (message or "").split(":", 1)[0]
    return code if code in {"VIDEO_PENDING_TIMEOUT", "VIDEO_SUBMISSION_UNCERTAIN", "VIDEO_CHECKPOINT_INVALID", "VIDEO_PREPARATION_RETRIES_EXHAUSTED", "VIDEO_PROVIDER_FAILED"} else None
