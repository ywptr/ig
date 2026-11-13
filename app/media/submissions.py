"""Atomic client idempotency, independent of queue delivery deduplication."""
from hashlib import sha256
import json
import re

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.principal import RequestPrincipal
from app.auth.service_access import audit, fail
from app.core.time import utc_now
from app.db.models import Job, MediaSubmissionKey
from app.jobs.provider import get_job_queue
from app.media.schemas import MediaGenerationRequest
from app.media.service import MediaSubmission, submit_media_generation


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def submission_identity(principal, idempotency_key):
    scope = {"service": principal.service_id, "tenant": principal.tenant.tenant_id,
             "issuer": principal.actor_issuer, "subject": principal.actor_subject,
             "operation": "media.generate"}
    return digest({**scope, "key": idempotency_key}), scope


def submit_delegated_media(db: Session, request: MediaGenerationRequest, *,
                           principal: RequestPrincipal, idempotency_key: str, on_created=None) -> MediaSubmission:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", idempotency_key):
        fail(400, "INVALID_IDEMPOTENCY_KEY")
    tenant_id, user_id = principal.tenant.tenant_id, principal.membership.user_id
    submission_id, scope = submission_identity(principal, idempotency_key)
    try:
        fingerprint = digest({**scope, "user_id": user_id, "request": request.model_dump(mode="json")})
    except (TypeError, ValueError):
        fail(400, "INVALID_MEDIA_REQUEST")
    record = db.get(MediaSubmissionKey, submission_id)
    if record is None:
        record = MediaSubmissionKey(submission_id=submission_id, service_id=principal.service_id,
            tenant_id=tenant_id, user_id=user_id, identity_id=principal.identity_id,
            media_type=request.media_type, fingerprint=fingerprint)
        db.add(record)
        try:
            # Unique insert serializes identical concurrent clients in Postgres.
            # No reservation can commit without its Job and response identities.
            db.flush()
        except IntegrityError:
            db.rollback()
            record = db.get(MediaSubmissionKey, submission_id)
            if record is None:
                fail(503, "SUBMISSION_UNAVAILABLE")
        else:
            try:
                result = submit_media_generation(request, db=db, tenant_id=tenant_id,
                                                user_id=user_id, defer_dispatch=True)
                if not result.job_id:
                    raise RuntimeError("Submission did not create a Job")
                record.job_id, record.request_id = result.job_id, result.request_id
                if on_created is not None:
                    on_created(db, result)
                db.commit()
            except Exception:
                db.rollback()
                raise
    if record.fingerprint != fingerprint:
        fail(409, "IDEMPOTENCY_CONFLICT")
    job = db.scalar(select(Job).where(Job.job_id == record.job_id,
                                     Job.tenant_id == tenant_id, Job.user_id == user_id))
    if job is None or record.request_id is None:
        fail(503, "SUBMISSION_UNAVAILABLE")
    result = MediaSubmission(record.media_type, record.request_id, job.status, job.job_id)
    dispatch = job.status == "queued" and record.dispatched_at is None
    db.rollback()  # No DB transaction/row lock spans queue network I/O.
    if dispatch:
        try:
            get_job_queue().enqueue(result.job_id)
        except Exception:
            # Delivery may have been accepted. Retrying republishes the SAME Job;
            # existing worker claims prevent another paid execution.
            audit(principal, "media.generate.dispatch_unconfirmed", submission_id=submission_id,
                  job_id=result.job_id)
            fail(503, "SUBMISSION_DISPATCH_UNCONFIRMED")
        db.execute(update(MediaSubmissionKey).where(
            MediaSubmissionKey.submission_id == submission_id,
            MediaSubmissionKey.dispatched_at.is_(None),
        ).values(dispatched_at=utc_now()))
        db.commit()
    audit(principal, "media.generate.accepted", submission_id=submission_id, job_id=result.job_id)
    return result
