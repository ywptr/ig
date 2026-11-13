"""Editor-specific delegation and policy; generation stays on the normal pipeline."""
from datetime import timedelta
from hashlib import sha256
import hmac
import json
import os
import secrets
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from app.auth.principal import RequestPrincipal
from app.auth.service_access import fail
from app.core.time import utc_now, utc_iso
from app.db.models import EditorSession, EditorAttempt, ServiceCredential, UserIdentity, User, Tenant, Execution
from app.media.schemas import MediaGenerationRequest
from app.tenancy.governance import get_tenancy_governance


def origin(value):
    import re
    try:
        parsed = urlsplit(value)
        valid = (len(value) <= 512 and re.fullmatch(r"https?://[A-Za-z0-9.\[\]:-]+", value) and parsed.hostname and parsed.path == "" and not
            (parsed.username or parsed.password or parsed.query or parsed.fragment)
            and "*" not in value and value == f"{parsed.scheme}://{parsed.netloc}"
            and (parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})))
        if not valid or parsed.port == 0:
            raise ValueError
        return value
    except (ValueError, TypeError, AttributeError):
        raise ValueError("An exact HTTPS origin or loopback HTTP origin is required") from None


class EditorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_media: list[str] = Field(default_factory=list, max_length=2)
    allowed_operations: list[str] = Field(default_factory=list, max_length=20)
    reference_editing: bool = True
    upload: bool = True
    model_switching: bool = True

    @model_validator(mode="after")
    def valid_media(self):
        if set(self.allowed_media) - {"image", "video"}:
            raise ValueError("Unsupported editor media")
        return self


class EditorLaunch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correlation_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    parent_origin: str
    draft: dict
    policy: EditorPolicy = Field(default_factory=EditorPolicy)
    callback_context: dict[str, str] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def valid_draft(self):
        origin(self.parent_origin)
        if set(self.draft) - set(MediaGenerationRequest.model_fields):
            raise ValueError("Draft contains unsupported or identity fields")
        if (not isinstance(self.draft.get("intent", {}), dict) or not isinstance(self.draft.get("output", {}), dict)
            or not isinstance(self.draft.get("references", []), list)
            or any(not isinstance(ref, dict) for ref in self.draft.get("references", []))):
            raise ValueError("Draft fields must use the media request structure")
        if set(self.draft.get("intent", {})) - {"description"} or any(
            set(ref) - {"artifact_id", "role", "strength"} for ref in self.draft.get("references", [])):
            raise ValueError("Draft uses unsupported identity or reference fields")
        request = MediaGenerationRequest.model_validate(self.draft)
        if (set(self.draft.get("output", {})) - set(type(request.output).model_fields)
            or set(self.draft.get("advanced") or {}) - {"provider", "model", "parameters"}):
            raise ValueError("Unsupported editor draft fields")
        if request.advanced and request.advanced.parameters:
            raise ValueError("Editor accepts declared generic controls only")
        if request.media_type not in {"image", "video"} or request.strategy != "single" or request.output.layout.type != "single":
            raise ValueError("Editor currently supports single image/video generation")
        if len(json.dumps(self.draft)) > 100000 or any(len(k) > 100 or len(v) > 512 for k, v in self.callback_context.items()):
            raise ValueError("Editor draft/context exceeds limits")
        if not self.policy.allowed_media:
            self.policy.allowed_media = [request.media_type]
        if request.media_type not in self.policy.allowed_media:
            raise ValueError("Draft media is excluded by editor policy")
        return self


def digest(value):
    return sha256(value.encode()).hexdigest()


def editor_base_url():
    try:
        return origin(os.environ["IG_EDITOR_BASE_URL"])
    except (KeyError, ValueError):
        fail(503, "EDITOR_NOT_CONFIGURED")


def resolve_binding(db, session):
    key = db.get(ServiceCredential, session.credential_id)
    identity = db.get(UserIdentity, session.identity_id)
    tenant = db.get(Tenant, session.tenant_id)
    membership = get_tenancy_governance().get_active_membership(db, session.tenant_id, session.user_id)
    if (not key or not key.enabled or key.service_id != session.service_id or "editor:launch" not in key.scopes
        or session.parent_origin not in (key.editor_origins or []) or session.tenant_id not in key.tenant_ids
        or not identity or identity.user_id != session.user_id or identity.issuer not in key.actor_issuers
        or not tenant or tenant.status != "active" or not membership):
        fail(401, "EDITOR_SESSION_INVALID")
    return RequestPrincipal(tenant=tenant, membership=membership, caller_type="editor",
        actor_user=db.get(User, session.user_id), actor_user_id=session.user_id, tenant_id=session.tenant_id,
        service_id=session.service_id, credential_id=key.key_id, identity_id=identity.identity_id,
        actor_issuer=identity.issuer, actor_subject=identity.subject, editor_session_id=session.session_id)


def validate_draft(db, draft, principal):
    """Preserve unavailable model/control errors for repair, never remove references."""
    from app.media.validation import validate_media_request, validate_model_capabilities, MediaRequestValidationError
    from app.media.video_service import resolve_video_selection, resolve_video_references
    from app.media.providers.video import VideoProviderConfigurationError
    from app.media.providers.base import ImageProviderConfigurationError
    from app.media.providers.registry import image_provider_registration
    from app.artifacts.references import resolve_references
    from app.db.models import Artifact
    request = MediaGenerationRequest.model_validate(draft)
    # Fail launch if an input cannot be accessed. Compatibility can be corrected
    # interactively, but missing/private/deleted content must never disappear.
    for ref in request.references:
        row = db.scalar(select(Artifact).where(Artifact.artifact_id == ref.artifact_id,
            Artifact.tenant_id == principal.tenant_id, Artifact.user_id == principal.actor_user_id))
        if row is None or row.cleanup_started_at or row.content_removed_at:
            fail(400, "EDITOR_REFERENCE_UNAVAILABLE")
    try:
        validate_media_request(request)
        if request.media_type == "video":
            selection = resolve_video_selection(db, request, tenant_id=principal.tenant_id)
            resolve_video_references(db, request, tenant_id=principal.tenant_id, user_id=principal.actor_user_id,
                read_bytes=False, provider=selection.provider, model=selection.model)
        else:
            from app.media.capabilities import media_capabilities
            caps = media_capabilities(db, tenant_id=principal.tenant_id)
            media = next(m for m in caps.media_types if m.id == "image")
            provider_id = request.advanced.provider if request.advanced else media.default_provider
            provider = next((p for p in media.providers if p.id == provider_id and p.available), None)
            model_id = request.advanced.model if request.advanced and request.advanced.model else provider.default_model if provider else None
            if not provider or not model_id:
                raise MediaRequestValidationError("Draft provider/model is unavailable")
            declaration = next((m for m in image_provider_registration(provider.id).CAPABILITIES.models if m.id == model_id), None)
            if declaration is None:
                raise MediaRequestValidationError("Draft model is unavailable")
            validate_model_capabilities(request, declaration)
            resolve_references(db, request.references, slots=declaration.technical.reference_inputs,
                tenant_id=principal.tenant_id, user_id=principal.actor_user_id)
        return []
    except (ValueError, VideoProviderConfigurationError, ImageProviderConfigurationError):
        return ["The draft model, controls or reference compatibility is no longer valid. Review the preserved draft before generating."]


def create_launch(db, principal, request):
    if principal.caller_type != "service":
        fail(403, "SERVICE_ROUTE_FORBIDDEN")
    key = db.get(ServiceCredential, principal.credential_id)
    if request.parent_origin not in (key.editor_origins or []):
        fail(403, "EDITOR_ORIGIN_NOT_AUTHORIZED")
    errors = validate_draft(db, request.draft, principal)
    now, secret = utc_now(), secrets.token_urlsafe(32)
    session = EditorSession(session_id=str(uuid4()), credential_id=key.key_id, service_id=principal.service_id,
        identity_id=principal.identity_id, user_id=principal.actor_user_id, tenant_id=principal.tenant_id,
        correlation_id=request.correlation_id, parent_origin=request.parent_origin, launch_hash=digest(secret),
        draft=request.draft, policy=request.policy.model_dump(), callback_context=request.callback_context,
        created_at=now, launch_expires_at=now + timedelta(seconds=60), expires_at=now + timedelta(minutes=30))
    base = editor_base_url()
    db.add(session)
    db.commit()
    return {"session_id": session.session_id, "correlation_id": session.correlation_id,
        "launch_url": f"{base}/embed/generation/{session.session_id}#launch={secret}",
        "launch_expires_at": utc_iso(session.launch_expires_at), "expires_at": utc_iso(session.expires_at), "draft_errors": errors}


def editor_principal(request: Request, db):
    token = request.headers.get("x-ig-editor-token")
    sid = request.headers.get("x-ig-editor-session")
    if token is None and sid is None:
        return None
    if (request.headers.get("authorization") or not token or len(token) > 100 or not sid
        or len(request.headers.getlist("x-ig-editor-token")) != 1 or len(request.headers.getlist("x-ig-editor-session")) != 1):
        fail(401, "EDITOR_SESSION_INVALID")
    session = db.get(EditorSession, sid)
    if (not session or session.revoked_at or not session.redeemed_at or session.expires_at <= utc_now()
        or not session.access_hash or not hmac.compare_digest(session.access_hash, digest(token))):
        fail(401, "EDITOR_SESSION_INVALID")
    if (request.headers.get("origin") not in {None, editor_base_url()}
        or (request.method != "GET" and request.headers.get("origin") != editor_base_url())):
        fail(403, "EDITOR_ORIGIN_NOT_AUTHORIZED")
    endpoint = request.scope.get("route").endpoint
    name = f"{endpoint.__module__}.{endpoint.__name__}"
    from app.auth.service_access import ROUTE_SCOPES
    allowed = set(ROUTE_SCOPES) - {("POST", "app.api.editor.create_editor_session")}
    allowed -= {("GET", "app.api.executions.list_executions")}
    allowed |= {("GET", "app.api.editor.read_editor_session"), ("POST", "app.api.editor.close_editor_session"),
        ("POST", "app.api.artifacts.upload_artifact"), ("POST", "app.api.jobs.request_job_cancellation")}
    if (request.method, name) not in allowed:
        fail(403, "EDITOR_ROUTE_FORBIDDEN")
    if name == "app.api.artifacts.upload_artifact" and (not session.policy["upload"] or not session.policy["reference_editing"]):
        fail(403, "EDITOR_POLICY_FORBIDDEN")
    job_id = request.path_params.get("job_id")
    if request.path_params.get("execution_id"):
        execution = db.get(Execution, request.path_params["execution_id"])
        job_id = execution.job_id if execution else "missing"
    if job_id and not db.scalar(select(EditorAttempt).where(EditorAttempt.session_id == sid, EditorAttempt.job_id == job_id)):
        fail(404, "EDITOR_JOB_NOT_FOUND")
    request.state.editor_session = session
    return resolve_binding(db, session)


def enforce_policy(session, request):
    original = MediaGenerationRequest.model_validate(session.draft)
    policy = session.policy
    operation = ("image_to_video" if request.references else "text_to_video") if request.media_type == "video" else (
        "reference_image_conditioning" if request.references else "text_to_image")
    if (request.media_type not in policy["allowed_media"] or
        request.strategy != "single" or request.output.layout.type != "single" or
        (policy["allowed_operations"] and operation not in policy["allowed_operations"]) or
        (not policy["model_switching"] and request.advanced != original.advanced) or
        (not policy["reference_editing"] and request.references != original.references)):
        fail(403, "EDITOR_POLICY_FORBIDDEN")


def submit_editor_generation(db, principal, session, request, attempt_id):
    import re
    from app.media.submissions import submit_delegated_media
    if not attempt_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", attempt_id):
        fail(400, "INVALID_IDEMPOTENCY_KEY")
    enforce_policy(session, request)
    key = f"editor:{session.session_id}:{attempt_id}"
    def record_attempt(transaction, result):
        # Consumer correlation is admitted atomically with Job + submission key.
        transaction.add(EditorAttempt(session_id=session.session_id, attempt_id=attempt_id,
            job_id=result.job_id, request_id=result.request_id))
    return submit_delegated_media(db, request, principal=principal, idempotency_key=key, on_created=record_attempt)
