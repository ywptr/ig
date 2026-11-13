# Sandcastle → IG integration (IG#0011)

This is the supported internal server-side integration helper, not a public SDK.
Sandcastle owns story/episode/scene context, LLM transformation, generation briefs,
prompts and reference selection. IG owns capability validation, provider execution,
Jobs, Executions and Artifacts. No Sandcastle changes are included here.

## Package and installation

The standalone Python 3.11+ package lives in `integrations/python`. It depends only
on Requests and PyJWT/cryptography, with no IG application, ORM, database or queue
imports. Install from a reviewed checkout; no package has been publicly published:

```sh
python -m pip install /path/to/ig/integrations/python
```

Pin the IG commit used for installation in your deployment/build manifest. For a
vendored installation, copy the complete `integrations/python` directory and
install it. Do not copy IG's `app` package or backend environment configuration.

The wire protocol remains [IG#0001 service access](service-access.md). Endpoint and
claim semantics are unchanged. `GET /openapi.json` describes the server's request
schemas, but is not itself a service-allowlisted route; obtain it separately from
the deployment operator when needed.

## Setup and credential registration

An IG operator must register an RSA public key (at least 2048 bits) with the
existing `scripts/configure-service-credential.py` administrative helper. For
example, against an explicitly selected development database:

```sh
.venv/bin/python scripts/configure-service-credential.py \
  --service-id sandcastle --key-id sandcastle-dev-1 \
  --public-key-file /secure/path/sandcastle-public.pem \
  --actor-issuer https://is.example.com \
  --tenant-id EXISTING-IG-TENANT-ID \
  --scope media:discover --scope media:generate \
  --scope jobs:read --scope artifacts:read
```

Retain the existing script's database-target safeguards. This task does not
register credentials, change production infrastructure or provision users.

The actor must already have an IG `UserIdentity` for the exact IS issuer+subject
and an active membership in the selected active tenant. Service access does not
perform JIT provisioning. The registration must grant both the issuer and tenant.
Email is not identity, and an application's local user ID is not an IG actor ID.

Keep the private key in Sandcastle's server-side secret store. Never send it, a
service JWT, or the credentials object to browser JavaScript. Never forward browser
cookies to IG. Disable HTTP tracing that records Authorization or private key data.

```python
import os
from pathlib import Path
from ig_client import DelegatedContext, IGClient, ServiceCredentials

credentials = ServiceCredentials(
    service_id=os.environ["IG_SERVICE_ID"],
    key_id=os.environ["IG_SERVICE_KEY_ID"],
    private_key_source=lambda: Path(os.environ["IG_PRIVATE_KEY_FILE"]).read_bytes(),
    audience="ig-api",
    expiry_seconds=300,
)
client = IGClient(
    os.environ["IG_BASE_URL"],
    credentials,
    default_timeout=30,
    max_attempts=3,
)
```

These environment names are a calling-application example, not new IG backend
settings. A secret-manager callback can replace the file source. The helper does
not cache or log private keys or tokens. It signs a fresh token on every HTTP
attempt, using only that route's required scope. A source callback must be safe
for the calling application's concurrency and must not log its return value.

`create_service_token(...)` is also available for callers needing only signing.
It accepts service ID, key ID, private key, delegated context, scopes, audience
and `expiry_seconds` (1–300). It produces the existing RS256 `ig-service+jwt`
header, `iss=sub=service_id`, `act={iss,sub}`, `tenant_id`, string `scope`, and
integer issuance/expiry claims. The current IG server accepts audience `ig-api`;
the configuration option is for an explicitly coordinated future contract change.
Clocks must be synchronized; IG grants no clock-skew allowance.

Rotate by registering another public key/kid for the same service, replacing the
credentials object with the new kid/source, then disabling the old registration
after transition. Keep grants consistent. Idempotency is scoped to service ID,
not kid, so rotation does not create replacement Jobs.

Use an HTTPS **origin**, without `/v2`, credentials, query or fragment. Loopback
HTTP origins such as `http://127.0.0.1:8011` are allowed for development. The
default transport does not follow redirects, retain cookies, use netrc credentials
or inherit proxy/CA environment settings. A custom `Transport` can provide an
organization's trusted HTTP configuration while preserving these protections.

## Per-request actor and tenant

Construct the context from Sandcastle's verified server-side IS session and its
authorized IG tenant mapping, not arbitrary browser input:

```python
context = DelegatedContext(
    actor_issuer=verified_is_issuer,
    actor_subject=verified_is_subject,
    tenant_id=authorized_ig_tenant_id,
)
```

Context is explicit on every call. No email or IG user-ID override is supported.
IG independently checks key grants, identity mapping, membership and resource
ownership. One long-lived client can be used for different contexts without a
shared cookie jar or cached actor token. Do not cache actor-specific discovery or
results across tenants/users.

| Method | Scope |
|---|---|
| `discover_capabilities` | `media:discover` |
| `submit_generation` | `media:generate` |
| `get_job`, `wait_for_job`, `get_job_executions`, `get_execution` | `jobs:read` |
| `get_artifact`, `get_artifact_content`, `get_artifact_preview` | `artifacts:read` |
| `generate_and_wait` | generate + jobs + artifacts |

## Discover capabilities

```python
capabilities = client.discover_capabilities(context)
for media in capabilities["media_types"]:
    if not media["available"]:
        continue
    for provider in media["providers"]:
        for model in provider["models"]:
            if model["usable"]:
                operations = (model["technical"] or {}).get("operations", [])
                reference_slots = (model["technical"] or {}).get("reference_inputs", [])
                controls = model["capabilities"]
                # Build application choices from this declaration.
```

The result is a typed dictionary matching discovery schema version 1, including
media availability, providers/models, operations, reference slots, execution
behavior, MIME/byte limits and generation controls. It preserves additive fields;
it does not implement another capability validator. IG validates each submission
and revalidates execution. Availability can change after discovery.

## Submit images, videos and references

```python
image_request = {
    "media_type": "image",
    "intent": {"description": generation_brief},
    "output": {"aspect_ratio": "16:9"},
    "references": [
        {"artifact_id": character_artifact_id, "role": "character", "strength": "normal"},
    ],
}
submission = client.submit_generation(
    context,
    correlation_id=sandcastle_execution_id,
    request=image_request,
)
# Persist submission.correlation_id, job_id, request_id, media_type and status.
```

```python
video_request = {
    "media_type": "video",
    "intent": {"description": video_brief},
    "output": {
        "duration_seconds": 5, "aspect_ratio": "16:9",
        "resolution": "720p", "audio": False,
    },
    "references": [{"artifact_id": first_frame_artifact_id, "role": "first_frame"}],
}
submission = client.submit_generation(
    context, correlation_id=sandcastle_video_execution_id, request=video_request,
)
```

Use only controls and references supported by current discovery. Omit references
for text-only generation. Video references currently do not accept `strength`;
image references support declared strengths. The helper forwards structured
references and never parses UI `@artifact` syntax or submits storage URLs.

References must already exist in IG and be accessible to the delegated actor and
tenant. Service scopes do **not** authorize the upload endpoint. Bringing external
Sandcastle files into IG is a separate integration dependency; this helper does
not bypass it. Explicit provider/model selection uses the existing hard-mode
request contract if Sandcastle needs it; no provider choices are hard-coded here.

## Correlation and safe retry

Persist this mapping in Sandcastle's own execution record:

```text
Sandcastle execution/correlation ID
  → Idempotency-Key (same value)
  → submission.job_id
  → get_job_executions(...): execution_id(s)
  → completed Execution artifact_id(s)
  → artifacts[] public metadata
```

For images, `request_id` identifies the image projection and can differ from
`job_id`; for video it equals `job_id`. Always poll `job_id`. There is no lookup by
raw correlation key: replaying the same submission is the recovery operation.

Before the first call, persist the immutable request body and correlation ID.
Keys must match `[A-Za-z0-9._:-]{1,128}`. Do not include secrets or prompt text.
Do not reuse a scene ID for revised generation content: use a stable ID for each
intentional generation attempt. IG's scope also includes service, actor and tenant.

```python
from ig_client import IGHTTPError, IGTransportError

try:
    submission = client.submit_generation(
        context, correlation_id=saved_execution_id, request=saved_request,
    )
except (IGTransportError, IGHTTPError) as error:
    if error.retryable:
        # After your application's bounded backoff, replay this same invocation.
        # Keep context, service ID, saved_request and saved_execution_id unchanged.
        schedule_same_submission_recovery(saved_execution_id)
    else:
        raise
```

The helper performs at most three HTTP attempts by default, within the request
budget. Transport failures and HTTP 408/429/500/502/503/504 can retry; numeric
Retry-After is respected up to 30 seconds when budget permits. Every submit retry
uses the exact serialized body and same key. It never invents a replacement key.
HTTP 409 is surfaced immediately. Same-key retries of failed/canceled/uncertain
Jobs return the original terminal Job; they do not launch more paid work.

## Polling and completed results

```python
from threading import Event

stop_waiting = Event()
job = client.wait_for_job(
    context, submission.job_id,
    timeout=900, poll_interval=3, cancel=stop_waiting,
)
if job.status == "completed":
    executions = client.get_job_executions(context, job.job_id)
    artifacts = [
        client.get_artifact(context, artifact_id)
        for execution in executions if execution.status == "completed"
        for artifact_id in execution.artifact_ids
    ]
else:
    # canceled/failed are terminal values, including error_code when present.
    record_terminal_job(job)
```

Waiting understands queued, running, waiting, polling and cancel_requested, and
stops at completed, failed or canceled. `VIDEO_PENDING_TIMEOUT` is a failed Job,
not a distinct status. Reads can retry within the bounded wait; provider polling
and delivery timing remain IG's responsibility.

`IGWaitTimeout` describes the caller's local wait budget and retains job ID and
last observed Job. `IGWaitCancelled` stops local waiting only. Neither cancels
remote generation; the existing service contract does not allow Job cancellation.
Resume `wait_for_job` with the saved Job ID. Requests in flight are not forcibly
interrupted; cancellation is checked between requests/backoffs and sleeps are
interruptible. Socket timeouts/read checks bound HTTP activity, but are not a
hard real-time process deadline. Use this synchronous client in a worker thread
or background worker when the application uses an async event loop.

Optional combined flow:

```python
result = client.generate_and_wait(
    context, correlation_id=saved_execution_id, request=saved_request,
    timeout=900, poll_interval=3,
)
for artifact in result.artifacts:
    persist_result_mapping(
        result.submission.correlation_id, result.job.job_id,
        artifact.execution_id, artifact.artifact_id, artifact.media_type,
    )
```

The convenience flow shares one orchestration budget across submit, wait and
metadata resolution. It raises `IGGenerationError` for terminal canceled/failed
Jobs; the exception retains `job`, `code`, category and correlation-bearing result.
It never automatically replaces failed/uncertain provider work. On success,
`MediaResult` contains submission, terminal Job, Executions and `artifacts`.
Current IG generation emits one Artifact per successful Execution; the helper
supports collections across completed Executions and additive `artifact_ids`.

Core result types are media-neutral: **use `artifacts[]` and `media_type`, not
`result.images` or `GeneratedImage`.** Each Artifact exposes ID, media/MIME type,
filename, byte size, Job/Execution IDs, preview state, dimensions/duration/fps and
content availability. Arbitrary provider metadata and storage locators are omitted.

## Artifact content and previews

```python
artifact = client.get_artifact(context, artifact_id)
content = client.get_artifact_content(context, artifact.artifact_id)
preview = client.get_artifact_preview(context, artifact.artifact_id,
                                      version=(artifact.preview or {}).get("version", 1))
if preview.status == 200:
    webp_bytes = preview.body
elif preview.status == 202:
    # Processing JSON, not image bytes; retry later if the user still needs it.
    pass
```

Metadata/content/preview always use authenticated IG routes and Artifact IDs.
The helper intentionally drops preview URLs; it does not return physical storage
URIs, signed provider URLs or Blob locators. Content is buffered with a default
128 MiB response limit. Configure a `RequestsTransport(max_response_bytes=...)`
if the deployment's output policy requires a different bound.

Serve bytes through a Sandcastle endpoint that independently authorizes its user;
do not put an IG bearer token into a browser URL or frontend bundle. HTTP 410
`ARTIFACT_CONTENT_REMOVED` means retention removed the content. Preview 422/404
errors are separate from successful original generation. Only Content-Type,
Retry-After and ETag headers are returned by the preview helper.

## Error decisions

`IGHTTPError` preserves HTTP `status`, original `detail` and `detail.code` as
`code`, plus `category` and `retryable`. Its message omits raw response text.
Do not indiscriminately log `detail`, request bodies, tokens or exception locals.
String/list FastAPI details (including validation 422 and ordinary 404) are
preserved, with `code=None`. Unknown future codes remain available unchanged.

| Failure | Decision |
|---|---|
| `IGTransportError`, transient HTTP/503 | Bounded retry; submission retains exact context/body/key |
| `INVALID_SERVICE_CREDENTIAL` (401) | Check expiry/clocks, kid, public-key registration, grants, audience; no blind auth retry |
| `INSUFFICIENT_SCOPE` | Correct registration/token grants |
| `SERVICE_ROUTE_FORBIDDEN` | Unsupported service operation; do not bypass it |
| `ACTOR_NOT_AUTHORIZED` | Check established IS mapping and trusted issuer |
| `TENANT_NOT_AUTHORIZED` | Check tenant grant, status and actor membership |
| `INVALID_IDEMPOTENCY_KEY` | Correct key format; helper rejects malformed keys locally |
| `IDEMPOTENCY_CONFLICT` (409) | Investigate changed material request; never silently change key |
| 404 | Unknown/inaccessible resource; ownership is intentionally indistinguishable |
| 400/422 media validation | Correct request using current capabilities |
| `VIDEO_PENDING_TIMEOUT` | Terminal IG deadline; no automatic new generation |
| `VIDEO_SUBMISSION_UNCERTAIN` | Possibly paid external work; operator/provider inspection before any intentional replacement |
| `VIDEO_CHECKPOINT_INVALID` | Operator inspection; no automatic paid retry |
| `VIDEO_PREPARATION_RETRIES_EXHAUSTED` | Terminal preparation failure; investigate |
| `VIDEO_PROVIDER_FAILED` | Terminal provider rejection/termination |
| `IGWaitTimeout` / `IGWaitCancelled` | Local wait ended; retain and resume existing Job |
| `IGProtocolError` | Unexpected wire contract/response limit; investigate, no automatic replacement |

Generation errors are found in successful Job reads, not necessarily HTTP error
responses. Lower-level waiting returns them as Job values. Combined orchestration
raises `IGGenerationError` with categories generation_timeout, provider_uncertainty,
generation_failure or canceled. These exceptions are never automatically retryable.

## IG#0010 boundary

`EditorLaunchRequest(correlation_id, artifact_ids=(), return_url=None)` and
`client.create_editor_session(context, request)` reserve an explicit server-side
editor launch boundary. The method raises `NotImplementedError` before signing
or making HTTP calls. No endpoint, embed session, browser credential, launch URL
or return-URL trust policy is implemented or promised. IG#0010 will define the
response and backend authorization/expiry/origin rules behind this boundary.

## Adoption checklist and verification

1. Install/vendor this package at a reviewed IG commit in Sandcastle's backend.
2. Have an IG operator register the public key, issuer, tenants and required scopes.
3. Store the private key server-side and configure the origin/service/kid/source.
4. Resolve verified IS issuer+subject and authorized IG tenant for each operation.
5. Discover capabilities; persist an immutable generation request and local execution ID.
6. Submit using that ID, persist IG Job/request IDs, then poll from a worker.
7. Save Execution/Artifact mappings and media type; proxy authorized bytes when needed.
8. Implement the error table, same-key recovery and intentional user-driven regeneration.
9. Keep embedded-editor integration disabled until IG#0010 implements the reserved boundary.

Focused tests: `python -m unittest tests.test_integration_client` from IG's checkout.
Disposable PostgreSQL + Redis/RQ path: `python -m tests.client_integration_check`.
The latter owns temporary infrastructure and uses mocked providers. Vercel Queue
neutrality is tested separately through real IG service handling with queue
contract mocks. The helper has no knowledge of either queue backend.
