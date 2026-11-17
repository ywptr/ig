"""Browser-owned storage settings and separate Drive OAuth consent."""
import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from app.auth.dependencies import get_db
from app.tenancy.dependencies import get_authenticated_tenant_context, get_admin_tenant_context
from app.db.models import User, TenantSettings, DriveConnection, DriveOAuthState
from app.core.time import utc_now, utc_iso
from app.security.secrets import encrypt_secret, decrypt_secret
from app.storage_integrations.connections import active_connection, require_capability, require_write_owner
from app.storage_integrations.google_drive import DriveClient, DriveError, configuration, token_request, SCOPE

router = APIRouter(tags=["storage-integrations"])
COOKIE = "ig_drive_connect"
CALLBACK = "/v2/user/storage/google-drive/callback"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def browser_context(request: Request, context=Depends(get_authenticated_tenant_context)):
    if getattr(context, "caller_type", "browser") != "browser":
        raise HTTPException(403, "Browser session required")
    if request.method != "GET":
        origin = request.headers.get("origin")
        if (origin and origin != str(request.base_url).rstrip("/")) or request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Cross-origin storage change denied")
    return context


def storage_status(db, context):
    user = db.get(User, context.membership.user_id)
    settings = db.get(TenantSettings, context.tenant.tenant_id)
    connection = active_connection(db, user.user_id)
    return {"google_drive_enabled": bool(settings and settings.google_drive_enabled),
        "artifact_storage_preference": user.artifact_storage_preference,
        "connection": None if connection is None else {
            "status": connection.status, "account_email": connection.account_email,
            "account_name": connection.account_name, "target_folder": "IG Artifacts",
            "checked_at": utc_iso(connection.checked_at), "updated_at": utc_iso(connection.updated_at)}}


@router.get("/user/storage")
def read_storage(context=Depends(browser_context), db: Session = Depends(get_db)):
    return storage_status(db, context)


class Preference(BaseModel):
    artifact_storage_preference: Literal["ig_managed", "google_drive"]


@router.patch("/user/storage")
def update_storage(body: Preference, context=Depends(browser_context), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.user_id == context.membership.user_id).with_for_update())
    if body.artifact_storage_preference == "google_drive":
        require_write_owner(db, context.tenant.tenant_id, user.user_id)
        connection = active_connection(db, user.user_id)
        if not connection or connection.status != "healthy" or not connection.encrypted_refresh_token:
            raise DriveError("connection_unhealthy")
    user.artifact_storage_preference = body.artifact_storage_preference
    db.commit()
    return storage_status(db, context)


class Capability(BaseModel):
    google_drive_enabled: bool = Field(strict=True)


@router.get("/tenant/settings/storage")
def read_capability(context=Depends(get_admin_tenant_context), db: Session = Depends(get_db)):
    settings = db.get(TenantSettings, context.tenant.tenant_id)
    return {"google_drive_enabled": bool(settings and settings.google_drive_enabled)}


@router.patch("/tenant/settings/storage")
def update_capability(body: Capability, request: Request, context=Depends(get_admin_tenant_context), db: Session = Depends(get_db)):
    # The same origin guard as user settings, independent of admin authorization.
    browser_context(request, context)
    settings = db.get(TenantSettings, context.tenant.tenant_id)
    if settings is None:
        settings = TenantSettings(tenant_id=context.tenant.tenant_id)
        db.add(settings)
    settings.google_drive_enabled = body.google_drive_enabled
    db.commit()
    return {"google_drive_enabled": settings.google_drive_enabled}


@router.post("/user/storage/google-drive/connect")
def connect(request: Request, response: Response, context=Depends(browser_context), db: Session = Depends(get_db)):
    require_capability(db, context.tenant.tenant_id)
    config = configuration()
    state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    # One pending intent per user. Expired intents are reclaimed during connect.
    db.execute(delete(DriveOAuthState).where((DriveOAuthState.user_id == context.membership.user_id)
                                           | (DriveOAuthState.expires_at < utc_now())))
    db.add(DriveOAuthState(state_hash=digest(state), user_id=context.membership.user_id,
        tenant_id=context.tenant.tenant_id, session_hash=digest(request.cookies.get("ig_session", "")),
        browser_hash=digest(browser), encrypted_verifier=encrypt_secret(verifier),
        intent="connect_drive", expires_at=utc_now() + timedelta(minutes=10)))
    db.commit()
    response.set_cookie(COOKIE, browser, max_age=600, httponly=True, secure=request.url.scheme == "https",
                        samesite="lax", path=CALLBACK)
    response.headers["Cache-Control"] = "no-store"
    return {"authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": config["CLIENT_ID"], "redirect_uri": config["REDIRECT_URI"], "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent select_account", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})}


@router.get("/user/storage/google-drive/callback")
def callback(request: Request, state: str = "", code: str = "", error: str = "",
             context=Depends(browser_context), db: Session = Depends(get_db)):
    if not 1 <= len(state) <= 256 or not 1 <= len(code) <= 4096 or error:
        raise HTTPException(400, "Drive authorization was not completed")
    identity = digest(state)
    flow = db.get(DriveOAuthState, identity)
    if (not flow or flow.intent != "connect_drive" or flow.expires_at <= utc_now()
        or flow.user_id != context.membership.user_id or flow.tenant_id != context.tenant.tenant_id
        or not hmac.compare_digest(flow.session_hash, digest(request.cookies.get("ig_session", "")))
        or not hmac.compare_digest(flow.browser_hash, digest(request.cookies.get(COOKIE, "")))):
        raise HTTPException(400, "Invalid or expired Drive connection state")
    require_capability(db, flow.tenant_id)
    verifier = decrypt_secret(flow.encrypted_verifier)
    # Atomically claim before the external exchange; replay cannot exchange twice.
    claimed = db.execute(update(DriveOAuthState).where(DriveOAuthState.state_hash == identity,
        DriveOAuthState.intent == "connect_drive").values(intent="exchanging").execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        raise HTTPException(400, "Drive connection state already used")
    db.commit()
    token = token_request({"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                           "redirect_uri": configuration()["REDIRECT_URI"]})
    refresh = token.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise DriveError("offline_authorization_required")
    client = DriveClient(token["access_token"])
    account = client.account()
    # Reuse only the same owner's same-account folder; never rebind an old locator.
    previous = db.scalar(select(DriveConnection).where(DriveConnection.user_id == context.membership.user_id,
        DriveConnection.account_id == account["permissionId"]).order_by(DriveConnection.created_at.desc()))
    previous_id = previous.connection_id if previous else None
    folder = previous.folder_id if previous else None
    db.commit()
    if folder:
        client.check_folder(folder)
    else:
        folder = client.create_folder()
    # Serialize account replacement/disconnect. A disconnect deletes even claimed intents.
    db.scalar(select(User).where(User.user_id == context.membership.user_id).with_for_update())
    flow = db.get(DriveOAuthState, identity, populate_existing=True)
    if not flow or flow.intent != "exchanging" or flow.expires_at <= utc_now():
        raise HTTPException(400, "Drive connection intent cancelled or expired")
    require_write_owner(db, context.tenant.tenant_id, context.membership.user_id)
    current = active_connection(db, context.membership.user_id)
    if current and current.connection_id != previous_id:
        current.active = False
        current.status = "disconnected"
        current.encrypted_refresh_token = None
        db.flush()
    connection = db.get(DriveConnection, previous_id) if previous_id else None
    if connection is None:
        connection = DriveConnection(connection_id=str(uuid4()), user_id=context.membership.user_id,
                                     account_id=account["permissionId"], folder_id=folder)
        db.add(connection)
    connection.active = True
    connection.encrypted_refresh_token = encrypt_secret(refresh)
    connection.account_email = account.get("emailAddress")
    connection.account_name = account.get("displayName")
    connection.status, connection.checked_at = "healthy", utc_now()
    db.delete(flow)
    db.commit()
    response = RedirectResponse("/?storage=connected", status_code=303)
    response.delete_cookie(COOKIE, path=CALLBACK)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/user/storage/google-drive/disconnect")
def disconnect(context=Depends(browser_context), db: Session = Depends(get_db)):
    db.scalar(select(User).where(User.user_id == context.membership.user_id).with_for_update())
    connection = active_connection(db, context.membership.user_id)
    if connection:
        connection.active, connection.status, connection.encrypted_refresh_token = False, "disconnected", None
    db.execute(delete(DriveOAuthState).where(DriveOAuthState.user_id == context.membership.user_id))
    # Keep a selected Drive preference invalid: switching back requires an explicit user choice.
    db.commit()
    return storage_status(db, context)


@router.post("/user/storage/google-drive/health")
def check_health(context=Depends(browser_context), db: Session = Depends(get_db)):
    from app.storage_integrations.connections import resolve_connection
    connection = active_connection(db, context.membership.user_id)
    if not connection:
        raise DriveError("disconnected")
    identity, encrypted = connection.connection_id, connection.encrypted_refresh_token
    db.commit()
    status = "healthy"
    try:
        client, folder = resolve_connection(identity, user_id=context.membership.user_id)
        client.check_folder(folder)
    except DriveError as exc:
        status = exc.code if exc.code in {"revoked", "permission_lost", "folder_unavailable"} else "unknown_error"
    db.execute(update(DriveConnection).where(DriveConnection.connection_id == identity,
        DriveConnection.active.is_(True), DriveConnection.encrypted_refresh_token == encrypted)
        .values(status=status, checked_at=utc_now()))
    db.commit()
    return storage_status(db, context)
