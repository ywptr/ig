# IG service access and delegated actors (IG#0001)

For the supported internal Python client and Sandcastle adoption examples, see
[Sandcastle integration helpers (IG#0011)](sandcastle-integration.md).

IG accepts a trusted application's signed assertion of an already-established IS
identity. The caller service, delegated human, and tenant are separate principals.
IS remains the human identity authority; IG authorizes membership and resources.
No browser cookies, database access, email matching, or service-call JIT provisioning
are part of this contract.

## Client contract

Base URL: the HTTPS origin of the target IG deployment, configured by its operator
as `IG_BASE_URL` in the calling application. There is no hard-coded production
hostname or IG tenant selection based on the service request's Host header.
Development can use the existing local HTTP IG origin. Paths below are relative to
that origin. Do not send an `ig_session` cookie.

Every supported request carries:

```http
Authorization: Bearer <signed-service-JWT>
```

JWT header:

```json
{"alg":"RS256","typ":"ig-service+jwt","kid":"sandcastle-dev-1"}
```

JWT claims (illustrative identifiers; use real numeric issuance/expiry timestamps):

```json
{
  "iss": "sandcastle",
  "sub": "sandcastle",
  "aud": "ig-api",
  "iat": 1900000000,
  "exp": 1900000300,
  "scope": "media:discover media:generate jobs:read artifacts:read",
  "act": {"iss":"https://your-is-issuer.example","sub":"IS-subject"},
  "tenant_id": "authorized-IG-tenant-UUID"
}
```

`iss` and `sub` identify the caller service and must match the key registration.
`act` is the IG-specific delegated-human claim in this service-token contract; it
contains exactly `iss` and `sub`. It is not an IS ID token or a token-exchange API.
`tenant_id` is IG's local `Tenant.tenant_id`, not a Nexus mapping, hostname, or email.
Actor/tenant headers and caller-supplied IG user IDs cannot override these claims.

Use RS256 with an RSA key of at least 2048 bits. IG accepts only registered public
keys, with an exact `kid`, token type, service identity, and string audience
`ig-api`. Integer `iat` and `exp` are required. Expiry must be after issuance and no
more than **300 seconds** later. Future-issued and expired tokens are rejected;
there is no configured clock-skew grace, so synchronize clocks. Optional `nbf` is
also checked by PyJWT. Tokens are bearer credentials and may be replayed within
their lifetime; submission idempotency is a separate contract.

The caller keeps its private key in its own secret store and signs short-lived
tokens after authenticating the IS user. IG stores only the public key and trust
policy. IG verifies the signed delegation and existing identity mapping; it does
not independently contact IS to prove that a Sandcastle browser session is still
live. Only services trusted to make these assertions should be registered.

## Scopes and supported routes

Requested token scopes must be a subset of the key's registered grants. The route
must also have its required scope. Everything outside this protected-route
allowlist is denied to service principals, even if the human is a tenant admin.

| Scope | Supported operations |
| --- | --- |
| `media:discover` | `GET /v2/media/capabilities` |
| `media:generate` | `POST /v2/media/generations` |
| `jobs:read` | `GET /v2/jobs`, `GET /v2/jobs/{job_id}`, `GET /v2/jobs/{job_id}/executions`, `GET /v2/executions`, `GET /v2/executions/{execution_id}` |
| `artifacts:read` | `GET /v2/artifacts`, `GET /v2/artifacts/{artifact_id}`, `GET /v2/artifacts/{artifact_id}/content`, `GET /v2/artifacts/{artifact_id}/preview` |

Service access does not include uploads, cancellation, generic Job submission,
legacy image submission, provider credentials, tenant settings, users, or membership
administration. New protected routes do not gain service access automatically.
The allowlist matches resolved Python handlers, avoiding router-prefix ambiguity.

## Actor, tenant, and ownership validation

For each request IG checks the registered key is enabled, validates the JWT, and
checks the key's allowed scopes, actor issuers, and explicit tenant allowlist.
`act.iss` must match an allowed issuer exactly, including path/trailing slash.
`act.iss + act.sub` must resolve an existing `UserIdentity` and `User`. Email and
profile data never establish this mapping. Unknown mappings are rejected; no User,
identity, session, or membership is created. The current User/UserIdentity schema
has no inactive status field; IG checks mapping existence. Tenant and membership
status must both be `active`. Service calls never trigger self-registration.

The registry tenant allowlist is independent of the actor's active membership:
both are required. Service tenancy is explicit and does not require the request
hostname to match a TenantDomain. Browser requests still require the normal
hostname/TenantDomain mapping and membership. Explicit IDs never create tenants.

Artifacts, Jobs, and Executions keep the same actor ownership rules. An admin actor
or service scope does not make another user's private resource readable. A wrong
owner/tenant produces the same 404 as an unknown resource. Artifact IDs remain the
public identifiers. Content streams pass through IG authorization; no physical
storage URI, Blob pathname, or provider download URL is returned.

## Discovery, submission, and recovery

Discovery:

```http
GET /v2/media/capabilities
Authorization: Bearer <JWT with media:discover>
```

Generation requires one additional header:

```http
POST /v2/media/generations
Authorization: Bearer <JWT with media:generate>
Idempotency-Key: sandcastle-execution-UUID
Content-Type: application/json
```

Example image body:

```json
{
  "media_type":"image",
  "intent":{"description":"A lighthouse at sunset"},
  "references":[],
  "output":{"aspect_ratio":"16:9"}
}
```

The existing media schema also supports `video`, optional image `first_frame`
references, interface levels, and explicit supported provider/model selection in
Hard mode. This milestone adds no provider behavior.

Successful service submission returns HTTP **202**, including on duplicate retries:

```json
{
  "media_type":"image",
  "request_id":"IG-image-request-UUID",
  "job_id":"IG-job-UUID",
  "status":"queued"
}
```

Service responses always include `job_id`. For images, `request_id` identifies the
image projection and differs from Job identity. For videos, `request_id` equals
`job_id`. Follow Job identity for execution/status/result recovery.

```http
GET /v2/jobs/{job_id}
Authorization: Bearer <JWT with jobs:read>

GET /v2/jobs/{job_id}/executions
Authorization: Bearer <JWT with jobs:read>
```

Job status includes queued/running/waiting/polling/cancel_requested and terminal
completed/failed/canceled states. Executions contain `execution_id`, status,
provider/model, and result metadata. A completed generation's Execution metadata
contains `artifact_id`. Do not assume the submission immediately has an Artifact.

```http
GET /v2/artifacts/{artifact_id}
Authorization: Bearer <JWT with artifacts:read>

GET /v2/artifacts/{artifact_id}/content
Authorization: Bearer <JWT with artifacts:read>
```

Metadata includes ID, media/MIME type, filename, size, timestamps and generation
metadata. Artifact metadata also includes lightweight preview state and a
versioned preview URL. Use that static derivative for browsing under the same
`artifacts:read` scope; see [preview semantics](artifact-previews.md). The content endpoint returns authorized bytes with the stored MIME type.
Service clients must use authenticated HTTP requests for content, not browser URLs
that cannot attach the service token.

### Idempotency rules

`Idempotency-Key` is required for delegated generation and must match
`[A-Za-z0-9._:-]{1,128}`. Use an opaque local execution/correlation ID, not secrets
or prompt text. It is scoped by stable service ID (not key ID), requested tenant,
canonical actor issuer/subject, and operation `media.generate`.

IG hashes that scope/key to a primary submission identity. The fingerprint hashes
the scope, resolved actor user ID, and normalized Pydantic request serialized as
sorted compact JSON. It includes prompt, media/interface/strategy, provider/model,
controls and ordered structured references, including roles/strength. Schema
defaults are normalized; arbitrary semantic equivalence is not inferred. Different
explicit defaults, routing choices, reference order or prompt whitespace can be
conservatively treated as different inputs. Transport headers, JWT issuance/expiry,
key rotation and provider availability changes are not execution inputs.

| Situation | Behavior |
| --- | --- |
| First valid submission | Commit one submission identity, Job, and image projection when applicable, then enqueue |
| Identical retry while queued/running/waiting/polling | Return the same Job/request IDs and current Job status |
| Identical retry after completion | Return the same Job; retrieve its Execution/Artifact |
| Identical retry after failure/cancellation | Return the same terminal Job; no automatic paid retry |
| Same scope/key, changed fingerprint | HTTP 409 `IDEMPOTENCY_CONFLICT`; no new Job |
| Invalid request before commit | Roll back reservation and Job/projection; no accepted submission |
| Queue send fails or acknowledgement is uncertain | HTTP 503; retain the committed queued Job; retry the same POST/key |
| Process dies before queue publication | Retry the same POST/key to enqueue the saved Job |
| Queue accepts, process dies before recording acknowledgement | Retry can republish the same Job; worker claims independently prevent repeated generation |

There is no new correlation-lookup endpoint: retrying the same POST/key is the
recovery operation after response loss. Refresh an expired JWT using the same
actor/tenant/service identity and original body/key. Do not mint a new key merely
because the response is uncertain. A changed key intentionally requests new work.

PostgreSQL's unique insert serializes concurrent requests; the reservation, Job,
and image projection commit in one transaction. Queue publication happens after
commit, with no DB transaction held over queue I/O. Idempotency records survive
process restarts and are not automatically expired or deleted. Queue redelivery
protection remains separate. There is no background dispatcher added here: a crash
before dispatch is recovered by client retry. Existing worker/provider submission
crash ambiguity and video recovery policy are unchanged.

## Errors and browser precedence

Service authorization errors use `{"detail":{"code":"..."}}`:

| HTTP | Code / meaning |
| --- | --- |
| 401 | `INVALID_SERVICE_CREDENTIAL`: malformed, invalid signature, expired/future-issued, revoked/unknown key, wrong service/audience/type, or invalid scope grant |
| 403 | `INSUFFICIENT_SCOPE`: valid credential lacks the route scope |
| 403 | `SERVICE_ROUTE_FORBIDDEN`: protected operation is outside the service allowlist |
| 403 | `ACTOR_NOT_AUTHORIZED`: malformed/untrusted/unknown issuer+subject |
| 403 | `TENANT_NOT_AUTHORIZED`: unknown/inactive/ungranted tenant or missing/inactive actor membership |
| 404 | Existing resource-not-found errors for unknown/wrong-owner/wrong-tenant resources |
| 400 | `AMBIGUOUS_AUTHENTICATION`: bearer/Authorization header plus any `ig_session` cookie |
| 400 | `INVALID_IDEMPOTENCY_KEY`: missing, duplicate or malformed submission key |
| 409 | `IDEMPOTENCY_CONFLICT`: same scope/key, different execution inputs |
| 503 | `SUBMISSION_DISPATCH_UNCONFIRMED` or `SUBMISSION_UNAVAILABLE`: retry the same POST/key |

Existing validation remains: schema errors use 422; invalid media requests use
400 `INVALID_MEDIA_REQUEST`; unavailable media can return 501. Missing credentials
retain the browser authentication error (401 `Not authenticated` on a valid tenant
hostname); an unknown browser hostname can still return 404. Invalid/revoked/etc.
credentials intentionally share an error rather than revealing registry contents.

Without Authorization, the existing `ig_session` and hostname tenancy flow applies.
Any Authorization header selects service validation; unsupported schemes are not
silently ignored. Cookie-plus-Authorization is rejected even if the cookie is
invalid or expired. Do not forward browser cookies from Sandcastle.

## Development configuration, rotation, and revocation

Apply the additive Alembic migration using the repository's normal verified target
workflow. This implementation adds `service_credentials` (public keys and grants)
and `media_submission_keys` (fingerprints and Job correlation); it changes no
existing rows. Production migration testing should use a Neon branch and direct
connection before rollout. No deployment or credential registration is performed
by this milestone itself.

Create a private development key outside the repository:

```sh
umask 077
mkdir -p /tmp/ig-service-dev
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out /tmp/ig-service-dev/private.pem
openssl pkey -in /tmp/ig-service-dev/private.pem -pubout -out /tmp/ig-service-dev/public.pem
```

Against a selected development IG database (set `DATABASE_URL` safely), register
only the public key. Replace tenant UUID and issuer with the existing IG tenant
and exact IS issuer; the user must already have completed IS login to IG.

```sh
.venv/bin/python scripts/configure-service-credential.py \
  --service-id sandcastle --key-id sandcastle-dev-1 \
  --public-key-file /tmp/ig-service-dev/public.pem \
  --tenant-id IG-TENANT-UUID --actor-issuer https://your-is-issuer.example \
  --scope media:discover --scope media:generate --scope jobs:read --scope artifacts:read
```

The registry validates RSA public keys (minimum 2048 bits), known tenants, scopes,
and issuer URLs. HTTPS issuers are accepted; HTTP is limited to loopback development
issuers. Private keys are rejected. The CLI uses existing administrative database
target checks; remote/production writes require the explicit target acknowledgment.
No administrative HTTP service registry is exposed.

The offline signing helper does not need database access and prints no token:

```sh
.venv/bin/python scripts/issue-service-token.py \
  --service-id sandcastle --key-id sandcastle-dev-1 \
  --private-key-file /tmp/ig-service-dev/private.pem \
  --actor-issuer https://your-is-issuer.example --actor-subject IS-SUBJECT \
  --tenant-id IG-TENANT-UUID --scope media:discover \
  --output /tmp/ig-service-dev/token.jwt
```

It creates a new 0600 output file and refuses to overwrite an existing file. Treat
that file as a credential. Production Sandcastle should sign in-process from its
secret store using the same contract; it should not invoke a password login or
receive an IG database connection. Use TLS in production and avoid request tracing
that records Authorization headers.

Rotation: register a new `kid` and public key for the same `service_id`, with the
intended grants. Both keys may be active. Switch the caller to the new private key,
then disable the old key:

```sh
.venv/bin/python scripts/configure-service-credential.py --key-id OLD-KEY-ID --disable
```

Every request reads its key's enabled flag/grants from the primary database; there
is no cross-request key cache and no IG restart is required. In-flight requests
already authorized are not retroactively canceled. A key ID cannot be reassigned
to a different key or service through the helper. Apply tenant/scope policy changes
to all applicable active keys. Service identity remains stable across rotation,
so idempotency keys continue resolving the same Jobs.

## Safe audit context and verification

`service.access` JSON log events record service/key IDs, internal actor identity/user
IDs, tenant ID, operation template, relevant resource IDs, and hashed submission
identity plus Job ID on submission. Tokens, raw issuer/subject, Authorization,
provider credentials, prompts, and raw idempotency keys are not logged by this code.
The idempotency key is recoverable by the client, not from an IG log. No new large
audit subsystem is introduced.

Run focused tests with `python -m unittest tests.test_service_access`. The explicit
`python -m tests.service_integration_check` harness starts disposable PostgreSQL 17
and Redis on private temporary Unix sockets, tests actual concurrent inserts,
migration round trips and RQ execution, then stops both instances. It never uses
existing deployment connection strings or Redis instances.

## Implementation file map

| Area | Files changed |
| --- | --- |
| Authentication/principal | `app/auth/principal.py`, `app/auth/service_contract.py`, `app/auth/service_access.py`, `app/auth/dependencies.py` |
| Tenant context | `app/tenancy/dependencies.py` |
| Submission | `app/api/media.py`, `app/media/service.py`, `app/media/submissions.py`, `app/images/service.py`, `app/jobs/service.py` |
| Resource isolation | `app/api/jobs.py` |
| Persistence | `app/db/models/service_access.py`, `app/db/models/__init__.py`, `alembic/versions/1a0001000001_service_access_and_submission_keys.py` |
| Operations | `app/ops/service_credentials.py`, `scripts/configure-service-credential.py`, `scripts/issue-service-token.py` |
| Tests | `tests/test_service_access.py`, `tests/service_integration_check.py`, `tests/test_media_job_contracts.py` |
| Documentation | `docs/service-access.md`, `README.md` |

### Paginated artifact browsing (IG#0003)

`GET /v2/artifacts/page` requires `artifacts:read` and uses the same delegated
actor/tenant ownership as metadata, preview and content routes. See the
[listing contract](artifact-library-foundations.md#listing-search-and-pagination).
The legacy `GET /v2/artifacts` array remains supported.
