"""Advance ordered workflows using committed Job state and ordinary queue delivery."""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import logging
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.time import utc_now
from app.db.database import SessionLocal
from app.db.models import Artifact, ArtifactGeneration, Execution, ImageRequest, Job, MediaWorkflow
from app.jobs.cancellation import acknowledge_cancellation, cancel_executions
from app.jobs.provider import get_job_queue
from app.jobs.service import cancellation_values
from app.media.selection import ModelSelectionError
from app.media.service import submit_media_generation
from app.tenancy.provider_configs import ProviderNotConfiguredError
from ig_core.media.schemas import MediaGenerationRequest
from ig_core.media.selection import ModelCandidate, ModelSelectionPolicy
from ig_core.workflows import ArtifactInput, GenerationWorkflow, StepOutput

from .presets import build_workflow

logger = logging.getLogger(__name__)
WORK_PREFIX = "workflow:"
CHECK_INTERVAL = 30
ACTIVE = {"queued", "running", "cancel_requested"}
TERMINAL = {"completed", "failed", "canceled"}
INTERRUPTED = "WORKFLOW_JOB_OUTCOME_UNCERTAIN"


def wake_workflow(workflow_id, delay=0):
    get_job_queue().enqueue(f"{WORK_PREFIX}{workflow_id}", kind="recover", delay_seconds=delay)


def publish_wake(workflow_id):
    try:
        wake_workflow(workflow_id)
    except Exception:
        logger.warning("Workflow wake could not be published for %s", workflow_id)


def scoped_workflow(db, workflow_id, tenant_id, user_id, *, lock=False):
    query = select(MediaWorkflow).where(MediaWorkflow.workflow_id == workflow_id,
                                       MediaWorkflow.tenant_id == tenant_id,
                                       MediaWorkflow.user_id == user_id)
    return db.scalar(query.with_for_update() if lock else query)


def owned_artifact(db, workflow, artifact_id, media_type):
    artifact = db.get(Artifact, artifact_id)
    if (artifact is None or artifact.tenant_id != workflow.tenant_id or artifact.user_id != workflow.user_id
            or artifact.media_type != media_type or artifact.content_removed_at or artifact.cleanup_started_at):
        raise ValueError(f"Required {media_type} Artifact is unavailable")
    return artifact


def create_workflow(db, tenant_id, user_id, request, key):
    digest = sha256(request.model_dump_json().encode()).hexdigest()
    lookup = select(MediaWorkflow).where(
        MediaWorkflow.tenant_id == tenant_id, MediaWorkflow.user_id == user_id,
        MediaWorkflow.submission_key == key)
    existing = db.scalar(lookup) if key else None
    if existing is None:
        plan = build_workflow(db, tenant_id, request)
        row = MediaWorkflow(workflow_id=str(uuid4()), tenant_id=tenant_id, user_id=user_id,
                            submission_key=key, submission_hash=digest, status="queued", next_check_at=utc_now(),
                            definition={"plan": plan.model_dump(mode="json"), "preset": request.preset,
                                        "correlation_id": request.correlation_id},
                            steps={step.step_id: {"status": "pending", "job_ids": [], "outputs": []}
                                   for step in plan.steps})
        for step in plan.steps:
            for binding in step.inputs.values():
                if isinstance(binding, ArtifactInput):
                    owned_artifact(db, row, binding.artifact_id, binding.media_type)
        db.add(row)
        try:
            db.commit()
            return row
        except IntegrityError:
            db.rollback()
            existing = db.scalar(lookup) if key else None
            if existing is None:
                raise
    if existing.submission_hash != digest:
        raise WorkflowConflict("Idempotency key is already used for a different workflow")
    return existing


class WorkflowConflict(ValueError):
    pass


def submit_step(db, row, step, references, *, accepted_candidate=None):
    """Translate workflow bindings to the existing media submission contract."""
    selection = step.selection.model_dump() if step.selection else None
    request = MediaGenerationRequest.model_validate({
        "media_type": step.output,
        "interface_level": "hard" if selection or step.selection_policy else "easy",
        "intent": {"description": step.inputs["prompt"].value},
        "advanced": selection,
        "selection_policy": step.selection_policy,
        "references": references,
        **({"operation": step.operation, "output": step.speech.model_dump() if step.speech else {}}
           if step.operation == "text_to_speech" else {}),
    })
    return submit_media_generation(request, db=db, tenant_id=row.tenant_id,
                                   user_id=row.user_id, defer_dispatch=True,
                                   accepted_candidate=accepted_candidate).job_id


def resolve_inputs(db, row, step, states):
    references = []
    for binding in step.inputs.values():
        if isinstance(binding, StepOutput):
            artifact_id = states[binding.step_id]["outputs"][0]["artifact_id"]
        elif isinstance(binding, ArtifactInput):
            artifact_id = binding.artifact_id
        else:
            continue
        owned_artifact(db, row, artifact_id, binding.media_type)
        references.append({"artifact_id": artifact_id, "role": binding.role})
    return references


def collect_outputs(db, row, job_id, media_type):
    artifacts = db.scalars(select(Artifact).join(
        ArtifactGeneration, ArtifactGeneration.artifact_id == Artifact.artifact_id
    ).where(ArtifactGeneration.job_id == job_id, Artifact.tenant_id == row.tenant_id,
            Artifact.user_id == row.user_id, Artifact.media_type == media_type)
      .order_by(Artifact.created_at, Artifact.artifact_id))
    return [{"artifact_id": artifact.artifact_id, "media_type": artifact.media_type,
             "filename": artifact.filename, "mime_type": artifact.mime_type} for artifact in artifacts]


def fail_step(row, state, error, *, retryable=True):
    row.status = state["status"] = "failed"
    state.update(error=error, retryable=retryable)


def reconcile_step(db, row, step, state, now):
    job_id = state["job_ids"][-1]
    job = db.scalar(select(Job).where(Job.job_id == job_id, Job.tenant_id == row.tenant_id,
                                      Job.user_id == row.user_id).with_for_update())
    if job is None:
        fail_step(row, state, "Step Job is unavailable", retryable=False)
        return None
    if job.status == "completed":
        state["outputs"] = collect_outputs(db, row, job_id, step.output)
        if not state["outputs"]:
            fail_step(row, state, "Job produced no Artifact matching the step output", retryable=False)
        else:
            state["status"] = "completed"
        return None
    if job.status == "failed":
        state["outputs"] = collect_outputs(db, row, job_id, step.output)
        code = (job.error_message or "").split(":", 1)[0]
        uncertain = code in {
            INTERRUPTED, "VIDEO_SUBMISSION_UNCERTAIN", "VIDEO_CHECKPOINT_INVALID", "VIDEO_PENDING_TIMEOUT",
            "VIDEO_PROVIDER_CONFIGURATION_UNAVAILABLE", "SPEECH_PROVIDER_UNCERTAIN",
        }
        fail_step(row, state, code if uncertain else "Step Job failed. Inspect the Job for details.",
                  retryable=not uncertain and not state["outputs"])
        return None
    if job.status == "canceled":
        state["status"] = "canceled"
        row.status = "canceled"
        return None
    started = job.cancel_requested_at or job.started_at
    if started and now - started >= timedelta(minutes=15):
        if job.status == "cancel_requested":
            acknowledge_cancellation(db, job_id)
            state["status"] = row.status = "canceled"
            return None
        if job.status == "running" and job.capability != "video.generate":
            job.status, job.error_message, job.completed_at = "failed", INTERRUPTED, now
            for execution in db.scalars(select(Execution).where(Execution.job_id == job_id,
                                                               Execution.status == "running")):
                execution.status, execution.error_message, execution.completed_at = "failed", INTERRUPTED, now
            if job.capability == "image.generate":
                db.execute(update(ImageRequest).where(
                    ImageRequest.request_id == job.input_json.get("request_id"),
                    ImageRequest.tenant_id == row.tenant_id, ImageRequest.user_id == row.user_id,
                    ImageRequest.status == "generating",
                ).values(status="failed"))
            state["outputs"] = collect_outputs(db, row, job_id, step.output)
            fail_step(row, state, INTERRUPTED, retryable=False)
            return None
    state["status"] = job.status
    if row.status == "cancel_requested":
        return None
    if job.status == "queued":
        return ("recover", job_id)
    return None


def advance_workflow(workflow_id):
    now = utc_now()
    dispatch = None
    with SessionLocal() as db:
        row = db.scalar(select(MediaWorkflow).where(
            MediaWorkflow.workflow_id == workflow_id).with_for_update())
        if row is None or row.status not in ACTIVE or row.next_check_at > now:
            return
        was_canceling = row.status == "cancel_requested"
        states = deepcopy(row.steps)
        plan = GenerationWorkflow.model_validate(row.definition["plan"])
        for step in plan.steps:
            state = states[step.step_id]
            if state["status"] == "completed":
                continue
            if state["status"] != "pending":
                dispatch = reconcile_step(db, row, step, state, now)
                if state["status"] != "completed":
                    if was_canceling and state["status"] in TERMINAL:
                        row.status = "canceled"
                    break
                if row.status == "cancel_requested":
                    row.status = "canceled"
                    break
                continue
            if row.status == "cancel_requested":
                row.status = "canceled"
                break
            try:
                with db.begin_nested():
                    references = resolve_inputs(db, row, step, states)
                    previous = state.get("selection_resolution") or {}
                    pinned = previous.get("selected")
                    if pinned is None and state["job_ids"]:
                        prior = db.scalar(select(Job).where(
                            Job.job_id == state["job_ids"][-1], Job.tenant_id == row.tenant_id,
                            Job.user_id == row.user_id,
                        ))
                        recorded = prior.input_json.get("selection") if prior else None
                        if recorded:
                            pinned = {"provider": recorded["provider"], "model": recorded["model"]}
                    if pinned is not None:
                        candidate = ModelCandidate.model_validate(pinned)
                        policy = ModelSelectionPolicy(mode="LOCKED", candidates=(candidate,))
                        retry_step = step.model_copy(update={"selection": None, "selection_policy": policy})
                        job_id = submit_step(db, row, retry_step, references, accepted_candidate=candidate)
                    else:
                        job_id = submit_step(db, row, step, references)
                    if not job_id:
                        raise RuntimeError("Workflow submission requires a Job")
            except ModelSelectionError as exc:
                state["selection_rejection"] = exc.result.snapshot().model_dump(mode="json")
                fail_step(row, state, "Step model selection failed")
            except (ValueError, ProviderNotConfiguredError):
                fail_step(row, state, "Step inputs or model are unavailable")
            else:
                job = db.scalar(select(Job).where(Job.job_id == job_id))
                audit = job.input_json.get("selection_audit") if job else None
                if audit:
                    state.setdefault("selection_resolution", audit)
                    state.pop("selection_rejection", None)
                state["job_ids"].append(job_id)
                state["status"] = "queued"
                dispatch = ("initial", job_id)
            break
        else:
            row.status = "canceled" if row.status == "cancel_requested" else "completed"
        if row.status == "queued":
            row.status = "running"
        if row.status in {"failed", "canceled"}:
            for state in states.values():
                if state["status"] == "pending":
                    state["status"] = "blocked" if row.status == "failed" else "canceled"
        active = row.status in ACTIVE
        row.steps = states
        row.next_check_at = utc_now() + timedelta(seconds=CHECK_INTERVAL) if active else None
        db.commit()

    # Attempt both publications even if one transport call fails.
    try:
        if dispatch:
            action, job_id = dispatch
            get_job_queue().enqueue(job_id, kind=action)
    finally:
        if active:
            wake_workflow(workflow_id, CHECK_INTERVAL)


def request_cancel(db, row):
    if row.status not in ACTIVE:
        raise WorkflowConflict("Workflow is already terminal")
    now = utc_now()
    job_id = next((state["job_ids"][-1] for state in row.steps.values()
                   if state["status"] in ACTIVE | {"waiting", "polling"} and state["job_ids"]), None)
    # Running steps may also be waiting at the provider. A planned step has no
    # active attempt; use its separate state marker when checking for a Job.
    row.status = "cancel_requested"
    row.next_check_at = now
    changed = False
    if job_id:
        job = db.scalar(select(Job).where(Job.job_id == job_id, Job.tenant_id == row.tenant_id,
                                          Job.user_id == row.user_id).with_for_update())
        if job and job.status in {"queued", "waiting", "running", "polling"}:
            for field, value in cancellation_values(job.status, now).items():
                setattr(job, field, value)
            if job.status == "canceled":
                db.flush()
                cancel_executions(db, job_id, now)
            changed = True
    db.commit()
    if changed:
        try:
            get_job_queue().cancel(job_id)
        except Exception:
            logger.warning("Workflow Job transport cancellation failed for %s", job_id)


def request_retry(db, row):
    if row.status != "failed" or not any(state.get("retryable") for state in row.steps.values()
                                        if state["status"] == "failed"):
        raise WorkflowConflict("Workflow has no safely retryable failed step")
    states = deepcopy(row.steps)
    for state in states.values():
        if state["status"] in {"failed", "blocked"}:
            state.update(status="pending", error=None, retryable=False)
    row.steps = states
    row.status = "queued"
    row.next_check_at = utc_now()
    db.commit()


def recover_workflows(db, limit=100):
    ids = list(db.scalars(select(MediaWorkflow.workflow_id).where(
        MediaWorkflow.next_check_at <= utc_now()
    ).order_by(MediaWorkflow.next_check_at, MediaWorkflow.workflow_id).limit(limit)))
    db.rollback()
    published = 0
    for workflow_id in ids:
        try:
            wake_workflow(workflow_id)
            published += 1
        except Exception:
            logger.warning("Workflow recovery publish failed for %s", workflow_id)
    return {"scanned": len(ids), "published": published}


def workflow_view(row):
    return {"workflow_id": row.workflow_id, "status": row.status,
            "preset": row.definition["preset"], "correlation_id": row.definition["correlation_id"],
            "created_at": row.created_at,
            "steps": [{**step, **row.steps[step["step_id"]]} for step in row.definition["plan"]["steps"]]}
