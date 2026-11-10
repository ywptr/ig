"""DB-revocable service JWTs with signed actor delegation and tenant binding."""
import json
import logging
import re
import time

from fastapi import HTTPException, Request
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.principal import RequestPrincipal
from app.db.models import ServiceCredential, Tenant, User, UserIdentity
from app.tenancy.governance import get_tenancy_governance

from app.auth.service_contract import AUDIENCE, TOKEN_TYPE, MAX_TOKEN_SECONDS, SCOPES

# Default deny: actor admin membership never grants a service administrative access.
ROUTE_SCOPES = {
    ("GET", "app.api.media.get_media_capabilities"): ("media:discover", "/v2/media/capabilities"),
    ("POST", "app.api.media.generate_media"): ("media:generate", "/v2/media/generations"),
    ("GET", "app.api.jobs.list_jobs"): ("jobs:read", "/v2/jobs"),
    ("GET", "app.api.jobs.read_job"): ("jobs:read", "/v2/jobs/{job_id}"),
    ("GET", "app.api.jobs.list_job_executions"): ("jobs:read", "/v2/jobs/{job_id}/executions"),
    ("GET", "app.api.executions.list_executions"): ("jobs:read", "/v2/executions"),
    ("GET", "app.api.executions.get_execution"): ("jobs:read", "/v2/executions/{execution_id}"),
    ("GET", "app.api.artifacts.read_artifacts"): ("artifacts:read", "/v2/artifacts"),
    ("GET", "app.api.artifacts.read_artifact"): ("artifacts:read", "/v2/artifacts/{artifact_id}"),
    ("GET", "app.api.artifacts.read_artifact_preview"): ("artifacts:read", "/v2/artifacts/{artifact_id}/preview"),
    ("GET", "app.api.artifacts.read_artifact_content"): ("artifacts:read", "/v2/artifacts/{artifact_id}/content"),
}
logger = logging.getLogger(__name__)


def fail(status: int, code: str):
    raise HTTPException(status, detail={"code": code},
                        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None)


def audit(principal: RequestPrincipal, operation: str, **identifiers):
    logger.info("service.access %s", json.dumps({
        "service_id": principal.service_id, "key_id": principal.credential_id,
        "actor_identity_id": principal.identity_id, "actor_user_id": principal.actor_user_id,
        "tenant_id": principal.tenant_id, "operation": operation, **identifiers,
    }, sort_keys=True, ensure_ascii=True))


def service_principal(request: Request, db: Session) -> RequestPrincipal | None:
    authorization = request.headers.getlist("authorization")
    if not authorization:
        return None
    if "ig_session" in request.cookies:
        fail(400, "AMBIGUOUS_AUTHENTICATION")
    cached = getattr(request.state, "service_principal", None)
    if cached is not None:
        return cached
    if len(authorization) != 1 or len(authorization[0]) > 16384:
        fail(401, "INVALID_SERVICE_CREDENTIAL")
    scheme, _, token = authorization[0].partition(" ")
    if scheme.lower() != "bearer" or not token or " " in token:
        fail(401, "INVALID_SERVICE_CREDENTIAL")
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if (header.get("alg") != "RS256" or header.get("typ") != TOKEN_TYPE
            or not isinstance(kid, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", kid)):
            raise ValueError
        credential = db.get(ServiceCredential, kid)
        if credential is None or not credential.enabled:
            raise ValueError
        claims = jwt.decode(token, credential.public_key, algorithms=["RS256"],
            audience=AUDIENCE, issuer=credential.service_id,
            options={"require": ["iss", "sub", "aud", "iat", "exp", "scope", "act", "tenant_id"],
                     "strict_aud": True})
        if (claims["sub"] != credential.service_id
            or type(claims["iat"]) is not int or type(claims["exp"]) is not int
            or not 0 < claims["exp"] - claims["iat"] <= MAX_TOKEN_SECONDS
            or claims["iat"] > time.time()):
            raise ValueError
        scope = claims["scope"]
        if not isinstance(scope, str):
            raise ValueError
        scopes = frozenset(scope.split())
        if not scopes or not scopes <= SCOPES or not scopes <= set(credential.scopes):
            raise ValueError
    except (jwt.PyJWTError, ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        # Expiry, unknown/revoked key, wrong audience and bad signatures share a
        # credential error. Never echo the token, key or library exception.
        fail(401, "INVALID_SERVICE_CREDENTIAL")

    route = request.scope.get("route")
    endpoint = getattr(route, "endpoint", None)
    # Match the resolved handler, not attacker input or router-relative paths.
    name = f"{getattr(endpoint, '__module__', '')}.{getattr(endpoint, '__name__', '')}"
    policy = ROUTE_SCOPES.get((request.method, name))
    if policy is None:
        fail(403, "SERVICE_ROUTE_FORBIDDEN")
    required, path = policy
    if required not in scopes:
        fail(403, "INSUFFICIENT_SCOPE")
    actor = claims["act"]
    if (not isinstance(actor, dict) or set(actor) != {"iss", "sub"}
        or any(not isinstance(actor[k], str) or not 0 < len(actor[k]) <= 512 for k in ("iss", "sub"))
        or actor["iss"] not in credential.actor_issuers):
        fail(403, "ACTOR_NOT_AUTHORIZED")
    row = db.execute(select(UserIdentity, User).join(User, User.user_id == UserIdentity.user_id).where(
        UserIdentity.issuer == actor["iss"], UserIdentity.subject == actor["sub"],
    )).first()
    if row is None:
        fail(403, "ACTOR_NOT_AUTHORIZED")
    identity, user = row
    tenant_id = claims["tenant_id"]
    if not isinstance(tenant_id, str) or tenant_id not in credential.tenant_ids:
        fail(403, "TENANT_NOT_AUTHORIZED")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.status != "active":
        fail(403, "TENANT_NOT_AUTHORIZED")
    membership = get_tenancy_governance().get_active_membership(db, tenant_id, user.user_id)
    if membership is None:
        fail(403, "TENANT_NOT_AUTHORIZED")
    principal = RequestPrincipal(tenant=tenant, membership=membership, caller_type="service",
        actor_user=user, actor_user_id=user.user_id, tenant_id=tenant_id, service_id=credential.service_id, credential_id=kid,
        identity_id=identity.identity_id, actor_issuer=actor["iss"], actor_subject=actor["sub"], scopes=scopes)
    request.state.service_principal = principal
    resources = {k: str(v)[:128] for k, v in request.path_params.items()
                 if k in {"job_id", "execution_id", "artifact_id"}}
    audit(principal, f"{request.method} {path}", **resources)
    return principal
