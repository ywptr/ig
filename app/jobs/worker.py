from copy import deepcopy
from dataclasses import replace
import logging
from sqlalchemy import select, update

from app.core.time import utc_now
from app.db.database import SessionLocal
from app.db.models import Job, Execution
from app.jobs.handlers import get_handler
from ig_core.execution.result import Completed, Pending
from ig_core.artifacts.media_metadata import inspect_media
from app.artifacts.service import artifact_store, persist_media_result, stage_media_result
from app.artifacts.storage.selection import select_write_store
from app.artifacts.orphans import discard_unpublished
from app.jobs.cancellation import (
    JobCancelled, cancellation_scope, check_cancellation, acknowledge_cancellation,
)
from app.jobs.continuation import schedule_continuation
from app.executions.context import execution_scope
from app.executions.service import (
    apply_execution_result, complete_execution, create_execution, fail_execution, wait_execution,
)
from app.core.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


def _own_running(db, job_id, execution_id):
    return db.execute(update(Job).where(
        Job.job_id == job_id, Job.status == "running",
        select(Execution.execution_id).where(Execution.execution_id == execution_id,
            Execution.job_id == job_id, Execution.status == "running").exists(),
    ).values(status="running").execution_options(synchronize_session=False)).rowcount == 1


def execute_job(job_id: str) -> None:
    """Claim once, execute cooperatively, and fence completion against cancellation."""
    from app.workflows.service import WORK_PREFIX, advance_workflow
    if job_id.startswith(WORK_PREFIX):
        advance_workflow(job_id[len(WORK_PREFIX):])
        return
    logger.info("job.worker.start job_id=%s", job_id)
    db = SessionLocal()
    claimed = False
    result = None
    staged = None
    published = False
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
        if capability in {"video.generate", "audio.generate"}:
            selection = input_data.get("selection") or {}
            execution.provider, execution.model = selection.get("provider"), selection.get("model")
        if capability == "video.generate":
            from app.jobs.video_lifecycle import initial_metadata, audit
            execution.metadata_json = {**execution.metadata_json, "video_lifecycle": initial_metadata(utc_now())}
            audit(job, execution, "prepared")
        db.commit()
        claimed = True
        with cancellation_scope(job_id, SessionLocal), execution_scope(job_id, execution_id):
            check_cancellation()
            result = get_handler(capability)(input_data, tenant_id=tenant_id, user_id=user_id)
            if isinstance(result, Completed) and result.artifact is not None:
                check_cancellation()
                properties = inspect_media(result.artifact.data, result.artifact.media_type)
                writer = select_write_store(db, tenant_id=tenant_id, user_id=user_id, managed_store=artifact_store)
                db.rollback()
                staged = stage_media_result(result.artifact, writer=writer)
        if capability == "video.generate" and isinstance(result, Pending):
            # The video handler already commits the first checkpoint immediately
            # after submission, closing the extra worker-return crash window.
            state = db.get(Execution, execution_id, populate_existing=True)
            if state.status == "waiting" and (state.metadata_json or {}).get("provider_operation", {}).get("operation_id") == result.operation_id:
                db.rollback()
                schedule_continuation(job_id)
                return
        if not _own_running(db, job_id, execution_id):
            acknowledge_cancellation(db, job_id, result)
            db.commit()
            return
        job = db.scalar(select(Job).where(Job.job_id == job_id).execution_options(populate_existing=True))
        execution = db.get(Execution, execution_id, populate_existing=True)
        if staged is not None:
            artifact = persist_media_result(
                db, payload=result.artifact, storage_uri=staged[0], filename=staged[1],
                tenant_id=tenant_id, user_id=user_id, provider=result.provider, model=result.model,
                job_id=job_id, execution_id=execution_id,
                inputs=(input_data.get("request") or input_data).get("references") or [],
                media_properties=properties,
            )
            result = replace(result, metadata={**result.metadata, "artifact_id": artifact.artifact_id})
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
        published = staged is not None
    except JobCancelled:
        db.rollback()
        acknowledge_cancellation(db, job_id)
        db.commit()
        return
    except Exception as exc:
        db.rollback()
        if claimed:
            if not _own_running(db, job_id, execution_id):
                canceled = acknowledge_cancellation(db, job_id)
                db.commit()
                if canceled:
                    return
            else:
                job = db.scalar(select(Job).where(Job.job_id == job_id).execution_options(populate_existing=True))
                execution = db.get(Execution, execution_id, populate_existing=True)
                if capability == "video.generate":
                    from app.jobs.video_lifecycle import terminal_failure
                    phase = (execution.metadata_json or {}).get("video_lifecycle", {}).get("phase")
                    from ig_core.providers.video import VideoProviderTerminalError
                    code = "VIDEO_PROVIDER_FAILED" if isinstance(exc, VideoProviderTerminalError) else (
                        "VIDEO_SUBMISSION_UNCERTAIN" if phase == "submitting" else "VIDEO_CHECKPOINT_INVALID")
                    terminal_failure(job, execution, code, utc_now())
                else:
                    fail_execution(db, execution, error_message=str(exc))
                    job.status = "failed"
                    job.error_message = str(exc)
                    job.completed_at = utc_now()
                db.commit()
        raise
    finally:
        db.close()
        if staged is not None and not published:
            discard_unpublished(staged[0], artifact_store, sessions=SessionLocal)
    if isinstance(result, Pending):
        schedule_continuation(job_id)
