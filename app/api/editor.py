"""Service launch and isolated, one-time editor bootstrap."""
from html import escape
import hmac
import secrets
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from app.auth.dependencies import get_db
from app.auth.service_access import fail
from app.core.time import utc_now, utc_iso
from app.db.models import EditorSession
from app.editor import EditorLaunch, create_launch, digest, editor_base_url, resolve_binding, validate_draft
from app.tenancy.dependencies import get_authenticated_tenant_context

router = APIRouter()


@router.post("/v2/editor/sessions", status_code=201)
def create_editor_session(payload: EditorLaunch, context=Depends(get_authenticated_tenant_context), db=Depends(get_db)):
    return create_launch(db, context, payload)


@router.get("/embed/generation/{session_id}", response_class=HTMLResponse)
def editor_surface(session_id: str, db=Depends(get_db)):
    session = db.get(EditorSession, session_id)
    if not session or session.revoked_at or session.launch_expires_at <= utc_now() or session.redeemed_at:
        fail(410, "EDITOR_LAUNCH_EXPIRED")
    resolve_binding(db, session)
    html = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>IG Advanced Generation Editor</title><link rel="stylesheet" href="/assets/style.css"></head>
<body><div id="root" data-parent-origin="PARENT_ORIGIN"></div><script type="module" src="/assets/embed.js"></script></body></html>"""
    return HTMLResponse(html.replace("PARENT_ORIGIN", escape(session.parent_origin, quote=True)),
        headers={"Content-Security-Policy": f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors {session.parent_origin}",
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})


class Redemption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    launch_token: str = Field(min_length=40, max_length=100, repr=False)


@router.post("/v2/editor/sessions/{session_id}/redeem")
def redeem_editor_session(session_id: str, payload: Redemption, request: Request, db=Depends(get_db)):
    if request.headers.get("authorization") or request.headers.get("origin") != editor_base_url():
        fail(403, "EDITOR_ORIGIN_NOT_AUTHORIZED")
    session = db.scalar(select(EditorSession).where(EditorSession.session_id == session_id).with_for_update())
    if (not session or session.revoked_at or session.redeemed_at or session.launch_expires_at <= utc_now()
        or not session.launch_hash or not hmac.compare_digest(session.launch_hash, digest(payload.launch_token))):
        fail(410, "EDITOR_LAUNCH_EXPIRED")
    principal = resolve_binding(db, session)
    token = secrets.token_urlsafe(32)
    session.launch_hash, session.access_hash, session.redeemed_at = None, digest(token), utc_now()
    db.commit()
    return {**session_view(db, session, principal), "editor_token": token}


def session_view(db, session, principal):
    return {"session_id": session.session_id, "correlation_id": session.correlation_id,
        "parent_origin": session.parent_origin, "expires_at": utc_iso(session.expires_at),
        "draft": session.draft, "policy": session.policy, "callback_context": session.callback_context,
        "draft_errors": validate_draft(db, session.draft, principal)}


@router.get("/v2/editor/session")
def read_editor_session(request: Request, context=Depends(get_authenticated_tenant_context), db=Depends(get_db)):
    session = getattr(request.state, "editor_session", None)
    if session is None:
        fail(403, "EDITOR_SESSION_REQUIRED")
    return session_view(db, session, context)


@router.post("/v2/editor/session/close")
def close_editor_session(request: Request, context=Depends(get_authenticated_tenant_context), db=Depends(get_db)):
    session = getattr(request.state, "editor_session", None)
    if session is None:
        fail(403, "EDITOR_SESSION_REQUIRED")
    session.revoked_at = utc_now()
    db.commit()
    return {"status": "closed"}
