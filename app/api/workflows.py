import hmac
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_db
from app.tenancy.dependencies import get_authenticated_tenant_context
from app.workflows.presets import WorkflowSubmission, preset_catalog
from app.workflows.service import (
    WorkflowConflict, create_workflow, publish_wake, recover_workflows, request_cancel,
    request_retry, scoped_workflow, workflow_view,
)

router = APIRouter(tags=["workflows"])


def workflow_owner(context=Depends(get_authenticated_tenant_context)):
    if getattr(context, "caller_type", "browser") != "browser" or not context.membership:
        raise HTTPException(403, "Workflow access requires an interactive tenant membership")
    return context.tenant.tenant_id, context.membership.user_id


def owned_workflow(workflow_id, owner, db, *, lock=False):
    row = scoped_workflow(db, workflow_id, *owner, lock=lock)
    if row is None:
        raise HTTPException(404, "Workflow not found")
    return row


@router.get("/workflows/presets")
def list_presets(owner=Depends(workflow_owner), db: Session = Depends(get_db)):
    return preset_catalog(db, owner[0])


@router.post("/workflows", status_code=202)
def submit_workflow(body: WorkflowSubmission, request: Request,
                    owner=Depends(workflow_owner), db: Session = Depends(get_db)):
    keys = request.headers.getlist("idempotency-key")
    if keys and (len(keys) != 1 or not keys[0].strip() or len(keys[0]) > 128):
        raise HTTPException(400, "Invalid Idempotency-Key")
    try:
        row = create_workflow(db, *owner, body, keys[0] if keys else None)
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    view = workflow_view(row)
    db.rollback()
    if view["status"] == "queued":
        publish_wake(view["workflow_id"])
    return view


@router.get("/workflows/{workflow_id}")
def inspect_workflow(workflow_id: str, owner=Depends(workflow_owner), db: Session = Depends(get_db)):
    return workflow_view(owned_workflow(workflow_id, owner, db))


@router.post("/workflows/{workflow_id}/cancel", status_code=202)
def cancel_workflow(workflow_id: str, owner=Depends(workflow_owner), db: Session = Depends(get_db)):
    try:
        request_cancel(db, owned_workflow(workflow_id, owner, db, lock=True))
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    publish_wake(workflow_id)
    return workflow_view(owned_workflow(workflow_id, owner, db))


@router.post("/workflows/{workflow_id}/retry", status_code=202)
def retry_workflow(workflow_id: str, owner=Depends(workflow_owner), db: Session = Depends(get_db)):
    try:
        request_retry(db, owned_workflow(workflow_id, owner, db, lock=True))
    except WorkflowConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    publish_wake(workflow_id)
    return workflow_view(owned_workflow(workflow_id, owner, db))


@router.get("/internal/workflows/recover", include_in_schema=False)
def scheduled_recovery(request: Request, db: Session = Depends(get_db)):
    secret = os.getenv("CRON_SECRET")
    if not secret or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {secret}"):
        raise HTTPException(401, "Unauthorized")
    return recover_workflows(db)
