"""Bounded reconciliation of durable video state; never submits provider work."""
from copy import deepcopy
from datetime import timedelta
import json
import logging
from time import monotonic
from uuid import uuid4

from sqlalchemy import and_, or_, select
from app.core.time import as_utc, utc_now, utc_iso
from app.db.models import Execution, Job
from app.jobs.cancellation import acknowledge_cancellation
from app.jobs.provider import get_job_queue
from app.jobs.video_lifecycle import (
    audit, ensure_lifecycle, expired, instant, setting, submission_lease, terminal_failure,
)

logger = logging.getLogger(__name__)


def candidates(now):
    stale = as_utc(now) - timedelta(seconds=submission_lease())
    # JSON checkpoints let us skip active polling/submission/dispatch leases in
    # SQL, rather than letting a large healthy tenant consume the whole scan.
    execution = select(Execution.execution_id).where(
        Execution.job_id == Job.job_id, Execution.tenant_id == Job.tenant_id,
        Execution.user_id == Job.user_id, Execution.status.in_(("running", "waiting", "polling")),
        or_(Execution.metadata_json["video_lifecycle"]["recovery_until"].as_string().is_(None),
            Execution.metadata_json["video_lifecycle"]["recovery_until"].as_string() <= utc_iso(now)),
        or_(
            Execution.metadata_json["video_lifecycle"]["deadline_at"].as_string() <= utc_iso(now),
            and_(Job.status == "waiting", or_(
                Execution.metadata_json["video_lifecycle"]["next_poll_at"].as_string().is_(None),
                Execution.metadata_json["video_lifecycle"]["next_poll_at"].as_string() <= utc_iso(now))),
            and_(Job.status == "polling", or_(
                Execution.metadata_json["continuation"]["lease_expires_at"].as_string().is_(None),
                Execution.metadata_json["continuation"]["lease_expires_at"].as_string() <= utc_iso(now))),
            and_(Job.status == "running", or_(
                Execution.metadata_json["video_lifecycle"]["submission_lease_expires_at"].as_string() <= utc_iso(now),
                and_(Execution.metadata_json["video_lifecycle"]["submission_lease_expires_at"].as_string().is_(None),
                     Execution.started_at <= stale))),
        ),
    ).exists()
    no_active_execution = ~select(Execution.execution_id).where(
        Execution.job_id == Job.job_id, Execution.status.in_(("running", "waiting", "polling"))).exists()
    return select(Job.job_id, Job.created_at).where(Job.capability == "video.generate",
        Job.status.in_(("queued", "running", "waiting", "polling", "cancel_requested")),
        or_(Job.status == "cancel_requested", execution,
            and_(Job.status == "queued", Job.created_at <= stale),
            and_(Job.status.in_(("running", "waiting", "polling")), no_active_execution, Job.created_at <= stale)),
    ).order_by(Job.created_at, Job.job_id)


def _inspect(db, job_id, now, dry_run):
    """One short transaction; returns plain dispatch data, never a live ORM row."""
    job = db.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
    if not job or job.capability != "video.generate" or job.status not in {"queued", "running", "waiting", "polling", "cancel_requested"}:
        return "skipped", None
    if job.status == "cancel_requested":
        if not dry_run:
            acknowledge_cancellation(db, job_id)
            execution = db.scalar(select(Execution).where(Execution.job_id == job_id,
                Execution.tenant_id == job.tenant_id, Execution.user_id == job.user_id)
                .order_by(Execution.attempt.desc()).limit(1))
            if execution:
                db.refresh(job)
                audit(job, execution, "cancellation_recovered", now)
        return "canceled", None
    if job.status == "queued":
        if as_utc(job.created_at) > now - timedelta(seconds=submission_lease()):
            return "skipped", None
        # Fresh transport identity for initial repairs; DB queued->running is the
        # authoritative claim, rather than provider or transport idempotency.
        return "requeued", (job_id, "recover", None, None)
    active = list(db.scalars(select(Execution).where(Execution.job_id == job_id,
        Execution.status.in_(("running", "waiting", "polling"))).order_by(Execution.attempt.desc()).limit(2)))
    if len(active) != 1 or active[0].tenant_id != job.tenant_id or active[0].user_id != job.user_id:
        if not dry_run:
            job.status, job.completed_at = "failed", now
            job.error_message = "VIDEO_CHECKPOINT_INVALID: Video execution ownership/state requires inspection"
            for execution in active:
                # Do not mutate another tenant's inconsistent execution.
                if execution.tenant_id == job.tenant_id and execution.user_id == job.user_id:
                    execution.status, execution.completed_at = "failed", now
                    execution.error_message = job.error_message
        return "invalid", None
    execution = active[0]
    try:
        lifecycle = ensure_lifecycle(execution)
        if expired(execution, now):
            if not dry_run:
                terminal_failure(job, execution, "VIDEO_PENDING_TIMEOUT", now)
            return "timed_out", None
        if lifecycle.get("recovery_until") and instant(lifecycle["recovery_until"]) > now:
            return "skipped", None
        checkpoint = (execution.metadata_json or {}).get("provider_operation") or {}
        if job.status == "running" and not checkpoint.get("operation_id"):
            lease = lifecycle.get("submission_lease_expires_at")
            if lease and instant(lease) > now:
                return "skipped", None
            if lifecycle.get("phase") == "prepared" and execution.attempt < 3:
                if not dry_run:
                    execution.status, execution.completed_at = "failed", now
                    execution.error_message = "VIDEO_PREPARATION_INTERRUPTED: Safe pre-submission retry"
                    job.status, job.started_at, job.error_message = "queued", None, None
                    audit(job, execution, "prepared_requeued", now)
                return "reset", (job_id, "recover", None, None)
            code = "VIDEO_PREPARATION_RETRIES_EXHAUSTED" if lifecycle.get("phase") == "prepared" else "VIDEO_SUBMISSION_UNCERTAIN"
            if not dry_run:
                terminal_failure(job, execution, code, now)
            return "invalid" if code == "VIDEO_PREPARATION_RETRIES_EXHAUSTED" else "uncertain", None
        continuation = (execution.metadata_json or {}).get("continuation") or {}
        if (not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("metadata", {}), dict)
            or not all(isinstance(value, str) and value for value in
                       (checkpoint.get("operation_id"), execution.provider, execution.model))):
            raise ValueError("Invalid checkpoint")
        if job.status == "polling" and not continuation.get("lease_expires_at"):
            if not dry_run:
                job.status = execution.status = "waiting"
            continuation = {}
        if job.status == "polling":
            if continuation.get("lease_expires_at") and instant(continuation["lease_expires_at"]) > now:
                return "skipped", None
        if job.status == "waiting" and lifecycle.get("next_poll_at") and instant(lifecycle["next_poll_at"]) > now:
            return "skipped", None
        if job.status != execution.status or job.status == "running":
            # A durable operation is authoritative: recover polling, never submit.
            if not dry_run:
                job.status = execution.status = "waiting"
        token = continuation.get("id") or str(uuid4())
        if not dry_run:
            metadata = deepcopy(execution.metadata_json)
            metadata["continuation"] = {**continuation, "id": token}
            metadata["video_lifecycle"]["recovery_until"] = utc_iso(now + timedelta(seconds=60))
            execution.metadata_json = metadata
            audit(job, execution, "continuation_requeued", now)
        return "requeued", (job_id, "continue", token, execution.execution_id)
    except (ValueError, TypeError, KeyError, AttributeError):
        if not dry_run:
            terminal_failure(job, execution, "VIDEO_CHECKPOINT_INVALID", now)
        return "invalid", None


def recover_video_jobs(db, *, batch_size=None, max_items=None, max_seconds=None,
                       now=None, dry_run=False, queue=None):
    batch_size = setting("VIDEO_RECOVERY_BATCH_SIZE", 50, 1, 500) if batch_size is None else batch_size
    max_items = setting("VIDEO_RECOVERY_MAX_ITEMS_PER_RUN", 500, 1, 10000) if max_items is None else max_items
    max_seconds = setting("VIDEO_RECOVERY_MAX_SECONDS", 240, 1, 600) if max_seconds is None else max_seconds
    if not 1 <= batch_size <= 500 or not 1 <= max_items <= 10000 or not 1 <= max_seconds <= 600:
        raise ValueError("Invalid video recovery bounds")
    now = as_utc(now or utc_now())
    deadline = monotonic() + max_seconds
    summary = dict(scanned=0, processed=0, requeued=0, reset=0, canceled=0, timed_out=0,
                   uncertain=0, invalid=0, skipped=0, failed=0, batches_processed=0,
                   limit_reached=False, remaining_work_possible=False, dry_run=dry_run,
                   batch_size=batch_size, max_items_per_run=max_items, stop_reason="exhausted")
    cursor = None
    while summary["scanned"] < max_items and monotonic() < deadline:
        query = candidates(now)
        if cursor:
            created_at, job_id = cursor
            query = query.where(or_(Job.created_at > created_at, and_(Job.created_at == created_at, Job.job_id > job_id)))
        page = list(db.execute(query.limit(min(batch_size, max_items - summary["scanned"]))))
        db.rollback()
        if not page:
            break
        summary["batches_processed"] += 1
        for row in page:
            if monotonic() >= deadline:
                break
            cursor = row.created_at, row.job_id
            summary["scanned"] += 1
            try:
                action, dispatch = _inspect(db, row.job_id, now, dry_run)
                if dry_run:
                    db.rollback()
                else:
                    db.commit()
                summary[action] += 1
                if action != "skipped":
                    summary["processed"] += 1
                if dispatch and not dry_run:
                    job_id, kind, token, execution_id = dispatch
                    (queue or get_job_queue()).enqueue(job_id, kind=kind,
                        **({"continuation_id": token} if kind == "continue" else {}))
            except Exception as exc:
                db.rollback()
                summary["failed"] += 1
                logger.warning("video.recovery job_id=%s error_type=%s", row.job_id, type(exc).__name__)
    if summary["scanned"] >= max_items or monotonic() >= deadline:
        summary["limit_reached"] = True
        summary["stop_reason"] = "item_limit" if summary["scanned"] >= max_items else "time_limit"
        query = candidates(now)
        if cursor:
            created_at, job_id = cursor
            query = query.where(or_(Job.created_at > created_at, and_(Job.created_at == created_at, Job.job_id > job_id)))
        summary["remaining_work_possible"] = db.execute(query.limit(1)).first() is not None
        db.rollback()
    logger.info("video.recovery.summary %s", json.dumps(summary, sort_keys=True))
    return summary
