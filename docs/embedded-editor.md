# Advanced Generation Editor — trusted application integration (IG#0010)

**Sandcastle owns scene-to-media reasoning. IG owns advanced generation/editing
and media execution.** Supply the prepared prompt/brief and IG Artifact references;
IG does not receive story data for another LLM transformation. This implementation
does not change Sandcastle.

## Shared architecture

`GenerationLab` is the shared editor core used by normal IG and the dedicated
embed entry. Prompt editing, `@artifact` autocomplete, references, picker/uploads,
model discovery, controls, result cards and active-only polling are reused.
Normal IG retains its IS login, hostname tenancy, navigation and legacy history.
The embed shell supplies a prepared draft, policy, idempotent submission and
message lifecycle, without mounting login, administration or normal navigation.

## Operator setup and deployment

1. Apply Alembic migration `1a0010000010`, after verifying the intended database.
   It adds `service_credentials.editor_origins`, `editor_sessions` and
   `editor_attempts`. Existing credentials receive an empty origin allowlist.
2. Set **`IG_EDITOR_BASE_URL`** to the public IG origin, without path or trailing
   slash: for example `https://ig.example.com`. For local development use the
   frontend origin such as `http://127.0.0.1:5181`. This is an application origin,
   not a provider credential. Only HTTPS or loopback HTTP is accepted.
3. Register/update the calling service public key with `editor:launch` and exact
   approved parent origins. Keep existing scopes/grants if direct API use needs
   them. The new scope grants editor operations through bounded sessions; it does
   **not** grant service uploads, administrative APIs or direct Job cancellation.

```sh
.venv/bin/python scripts/configure-service-credential.py \
  --service-id sandcastle --key-id sandcastle-editor-1 \
  --public-key-file /secure/sandcastle-public.pem \
  --actor-issuer https://is.example.com --tenant-id IG-TENANT-ID \
  --scope editor:launch \
  --editor-origin https://sandcastle.example.com \
  --editor-origin http://localhost:5180
```

Use the script's existing database-target safeguards. Every registration update
must supply its intended origins and grants; omission clears the origin list.
No wildcard, suffix matching, scheme-less host, URL path, userinfo or arbitrary
caller-supplied origin is accepted. Origins are matched exactly, including port.
Register the browser's canonical origin (normally lowercase, no default port).
Rotation keys each need the intended origins. Disabling the issuing key, removing
the origin/scope/tenant/issuer grant, or removing active actor membership prevents
subsequent editor access. No production registration is performed by this task.

4. Deploy backend and frontend together. `vercel.json` routes
   `/embed/generation/{session_id}` to FastAPI; `/assets/embed.js`, its hashed
   shared chunks and `/assets/style.css` come from the frontend build. Stable entry
   files use revalidation; other chunks remain hashed. Retain this ingress routing
   when hosting elsewhere. The backend does not need a frontend manifest.
5. Retain the dynamic CSP response from FastAPI. Exempt only the dedicated embed
   surface from normal framing denial. Do not overwrite it with a blanket proxy
   `X-Frame-Options: DENY`. Verify actual deployment headers before enabling embeds.
6. Local Vite development proxies `/embed/generation` with the existing API proxy
   and provides development versions of the stable JS/CSS entry paths. Set
   `IG_DEV_API_TARGET` as in the existing development setup.
7. Schedule `scripts/cleanup-editor-sessions.py` daily through your existing
   maintenance runner. It uses the administrative target safeguards and removes
   at most 500 sessions expired for seven days, plus their attempt mappings. Run
   again for larger backlogs. It never deletes Jobs, Artifacts or submission keys.
   Cleanup is housekeeping only; access expiration is enforced on every request.

No new provider key, cookie secret or service private key is required in IG.

## Sandcastle backend launch

Install client 0.2.0 from a reviewed commit as described in
[the package README](../integrations/python/README.md). Keep its private signing key
and secret-source callback exclusively in Sandcastle's backend. Resolve the exact
IS issuer+subject and authorized IG tenant from its verified user session.

```python
from ig_client import DelegatedContext, EditorLaunchRequest

context = DelegatedContext(verified_is_issuer, verified_is_subject, ig_tenant_id)
launch = client.create_editor_session(context, EditorLaunchRequest(
    correlation_id=scene_media_execution_id,
    parent_origin="https://sandcastle.example.com",
    draft={
        "media_type": "video",
        "interface_level": "hard",
        "intent": {"description": prepared_scene_video_brief},
        "advanced": {"provider": selected_provider, "model": selected_model},
        "output": {"duration_seconds": 5, "aspect_ratio": "16:9",
                   "resolution": "720p", "audio": False},
        "references": [{"artifact_id": first_frame_ig_artifact_id,
                        "role": "first_frame"}],
    },
    policy={
        "allowed_media": ["video"],
        "allowed_operations": ["text_to_video", "image_to_video"],
        "reference_editing": True,
        "upload": True,
        "model_switching": True,
    },
    callback_context={"scene_id": local_scene_id},
))
# Return only these launch fields to the authorized Sandcastle browser:
browser_response = {
    "session_id": launch.session_id,
    "correlation_id": launch.correlation_id,
    "launch_url": launch.launch_url,
    "launch_expires_at": launch.launch_expires_at,
    "expires_at": launch.expires_at,
}
```

This calls `POST /v2/editor/sessions` with the existing RS256 delegated service
contract and **only `editor:launch`** in the token. The HTTP response is 201.
Treat the one-time launch URL as sensitive. Do not log it. `EditorSession.__repr__`
omits it. No service JWT is returned to the browser.

For images use `media_type: image`, the prepared brief, image controls, and
structured references such as `{artifact_id, role: character, strength: normal}`.
Omit references for text-only generation. Preferences use normal hard-mode
`advanced.provider/model`. Do not include identity fields in the draft.
`callback_context` is a bounded string map retained in session bootstrap; it is
not an executable callback URL and is not echoed in result messages. Keep its
application mapping in Sandcastle using the correlation ID.

## Draft validation and generic policy

The draft uses the existing `MediaGenerationRequest` vocabulary. This editor
currently supports the Generation Lab's single strategy/layout, image or video.
References must already be IG Artifacts owned by the delegated actor in the
selected tenant. Unavailable, removed or inaccessible references reject launch
with `EDITOR_REFERENCE_UNAVAILABLE`; storage URLs are rejected.

Compatibility is checked using existing model declarations. A well-formed draft
whose model/controls/reference compatibility is no longer available is preserved
with `draft_errors`. The UI preserves invalid values, displays validation state
and blocks generation until corrected; it does not silently choose a replacement
model or clamp an explicitly supplied control. Invalid transport shapes remain
422. IG revalidates the normal request at submission/execution.

Policy defaults allow reference editing, uploads and model switching, with media
restricted to the draft's media type. Specify `allowed_media` to allow both.
An empty `allowed_operations` allows the editor's normal supported operations;
otherwise the primary operation must be explicitly listed:

| Draft inputs | Primary operation |
|---|---|
| Image, no references | `text_to_image` |
| Image with references | `reference_image_conditioning` |
| Video, no references | `text_to_video` |
| Video with first frame | `image_to_video` |

Media/operation, reference immutability and provider/model switching restrictions
are enforced server-side, independently of disabled UI controls. Pin an explicit
provider/model in the draft when disabling switching; an Auto preference retains
normal server routing and does not freeze later operator configuration changes.
If a locked preference becomes unavailable, request a corrected launch.

Uploads use IG's existing validated image upload workflow under the editor actor
and tenant, only when both upload and reference editing are allowed. This does
not permit Sandcastle's service credential to upload external files. Importing
Sandcastle media into IG outside the embedded UI remains a separate contract.
The same picker and `@artifact` components are reused; plain typed names remain
plain prompt text until an Artifact suggestion is selected.

## Browser iframe and strict parent listener

Install the listener **before** setting iframe `src`. Retain the expected session
and correlation values from the authenticated backend response.

```javascript
const igOrigin = new URL(launch.launch_url).origin;
const iframe = document.createElement("iframe");
iframe.title = "Advanced Generation Editor";
iframe.referrerPolicy = "no-referrer";
iframe.style = "width:100%;height:850px;border:0";

window.addEventListener("message", event => {
  if (event.origin !== igOrigin || event.source !== iframe.contentWindow) return;
  const message = event.data;
  if (!message || message.version !== 1 || message.session_id !== launch.session_id) return;
  if (message.type === "ig.editor.handshake") {
    if (typeof message.nonce !== "string") return;
    iframe.contentWindow.postMessage({
      type: "ig.editor.authorize", version: 1,
      session_id: launch.session_id, nonce: message.nonce,
    }, igOrigin);
    return;
  }
  if (message.correlation_id !== launch.correlation_id) return;
  if (message.type === "ig.editor.completed") {
    // Validate the documented ID/media shapes, persist mappings, then fetch
    // authoritative metadata through the server-side IG client if needed.
    saveGenerationIdentities(message);
  }
  if (message.type === "ig.editor.closed") {
    // running_job_ids may still be active; closing did not cancel them.
    iframe.remove();
  }
});

iframe.src = launch.launch_url;
document.querySelector("#advanced-editor").append(iframe);
```

Do not use a sandbox that turns the iframe into an opaque origin. If adding a
sandbox, the application must deliberately allow the scripts/same-origin/file
picker/popups required by the selected workflow and test its resulting behavior.
Do not set wildcard postMessage targets, forward cookies, or pass service tokens.

The shell gets the approved parent origin from its server-rendered HTML and
requires a nonce handshake with that exact parent/opener before redemption. The
parent has ten seconds to authorize it. Both incoming and outgoing messages check
the expected Window/source and exact origin; incoming lifecycle commands also
check session ID/version. No caller can change actor/tenant by message.

To request close from the parent:

```javascript
iframe.contentWindow.postMessage({
  type: "ig.editor.close", version: 1, session_id: launch.session_id,
}, igOrigin);
```

Keep the iframe until its `closed` acknowledgment if revocation/event delivery is
required. Destroying a frame or navigating away is not a reliable close signal
and does not cancel generation; the credential still expires normally.

## Popup fallback

The same URL, UI, handshake and event schema support a popup. Open a blank window
from an explicit user gesture, install the listener with `event.source === popup`,
then navigate it to `launch.launch_url`. Keep the opener relationship for the
handshake; do not use `noopener` for the editor popup itself. A blocked popup must
show a retry/iframe choice. No third-party cookie or IG login is needed in either
mode. Direct top-level launch without an approved parent/opener is rejected by
the browser bootstrap. Popup automation has not been separately exercised by the
iframe test; verify any additional hosting COOP policy before using this fallback.

## Exact version 1 event contract

Every lifecycle event has:

```json
{"type":"ig.editor.ready","version":1,"session_id":"SESSION-ID","correlation_id":"LOCAL-EXECUTION-ID"}
```

Additional event fields:

| Type | Additional fields |
|---|---|
| `ig.editor.ready` | none |
| `ig.editor.submitted` | `attempt_id`, `job_id`, `media_type` |
| `ig.editor.completed` | `attempt_id`, `job_id`, `execution_ids`, `media_type`, `artifacts` |
| `ig.editor.canceled` | `attempt_id`, `job_id` |
| `ig.editor.error` | `attempt_id`, `job_id`, `code` for terminal generation failures |
| `ig.editor.closed` | `running_job_ids` |

Completion example:

```json
{
  "type": "ig.editor.completed", "version": 1,
  "session_id": "SESSION-ID", "correlation_id": "SCENE-MEDIA-EXECUTION-ID",
  "attempt_id": "BROWSER-GENERATION-ATTEMPT-ID", "job_id": "IG-JOB-ID",
  "execution_ids": ["IG-EXECUTION-ID"], "media_type": "video",
  "artifacts": [{"artifact_id": "IG-ARTIFACT-ID", "media_type": "video"}]
}
```

Bootstrap handshake messages have `type`, `version`, `session_id`, `nonce` and no
generation data. The reply changes only type to `ig.editor.authorize` and must
target the exact IG origin. Message bodies never contain credentials, storage
locators, raw provider responses or internal user/tenant database fields.
Treat lifecycle notifications as at-least-once UI signals; deduplicate by session,
attempt and event type. They are not durable webhooks. Verify authoritative result
ownership with backend reads when consuming notifications.

## Correlation, retries, closing and cancellation

```text
Sandcastle scene/media execution correlation
  → editor session
  → distinct intentional generation attempt UUIDs
  → normal idempotent submission keys (session + attempt)
  → Job → Execution(s) → Artifact(s)
```

Attempt mapping is committed atomically with the normal submission identity and
Job before queue delivery. A lost acknowledgement leaves that Job visible in the
editor. Retrying the unchanged pending draft uses the same attempt key. A confirmed
submission clears it; another click intentionally creates another attempt, even
with identical material. A changed draft creates a new intentional attempt.
Changed material under a reused raw attempt key returns normal 409 conflict.

Session correlation never serves as one generation key for all revisions. Jobs
and the existing submission records remain durable after editor closure/expiry.
Editor Job lists and Job/Execution reads are restricted to that session's attempts;
the Artifact picker can browse the actor's existing tenant-owned Artifacts.

Close revokes editor access and sends `closed`, including currently active Job IDs.
It does not cancel queued/running/provider work. Explicit Cancel generation uses
the existing cooperative Job cancellation API under the editor actor. Provider
work may continue externally; IG fences result publication according to IG#0002.
The close control waits for an in-flight submission acknowledgement before
closing, so accepted work can be identified. Manual tab/frame destruction can
still interrupt acknowledgement; keep the original launch/attempt correlation.

## Credential lifecycle and framing

- Launch: 60 seconds from issuance, one successful redemption. Secret is in the
  URL fragment, removed immediately by the embed bootstrap; it is not sent in
  the page URL, referrer or server access log.
- Redemption: same-IG-origin POST with the one-time secret; PostgreSQL row locking
  permits one winner. Only its hash is stored; the launch hash is cleared.
- Editor: expires 30 minutes after original issuance, with no refresh or permanent
  browser login. The opaque credential is stored only in page memory and sent in
  editor-specific headers. No local/session storage or cookies are used.
- Each API request rechecks session expiry/revocation, issuing key grants, current
  tenant/membership and identity binding. Administrative/general service routes
  remain forbidden. Browser identity cookies do not replace the editor actor.
- The page's CSP `frame-ancestors` is exactly the registered parent origin. Normal
  IG keeps framing denial and refuses to mount its login/workspace in an iframe.
- Responses use `no-store` and `no-referrer`. Protect launch URL delivery and
  error/request tracing in both applications. Like any bearer bootstrap secret,
  a stolen unredeemed secret is sensitive; origin checks do not make it public.
- Expiry unmounts the editor and stops polling. Request a fresh backend launch to
  continue editing; do not replay the old URL. Existing generation remains in IG.

## Errors and verification

Launch retains existing credential/scope/actor/tenant errors. Additional codes:
`EDITOR_NOT_CONFIGURED` (503), `EDITOR_ORIGIN_NOT_AUTHORIZED` (403),
`EDITOR_REFERENCE_UNAVAILABLE` (400), `EDITOR_LAUNCH_EXPIRED` (410),
`EDITOR_SESSION_INVALID` (401), `EDITOR_ROUTE_FORBIDDEN` and
`EDITOR_POLICY_FORBIDDEN` (403), and `EDITOR_JOB_NOT_FOUND` (404).
Normal media validation, idempotency conflicts and terminal video error codes
remain unchanged. Error messages and metadata are separate from paid retry policy.

Focused tests: `python -m unittest tests.test_editor tests.test_integration_client`.
Disposable PostgreSQL migration/replay tests: `python -m tests.editor_integration_check`.
Real Chromium tests: run the frontend Node suite with WebSocket support and the
browser's required shared libraries installed/available. The iframe fixture uses
a fake cross-origin parent and mocked generation responses; no paid provider call.
Deployment CSP, reverse-proxy routing, real IS login and production key policy
must be checked on the intended host before enabling Sandcastle production.
