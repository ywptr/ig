# Generation Lab

IG V4.6 replaces the primary image-only generator with a capability-driven Lab.
Sign in through the existing IG session, then open **Generate**. **Jobs** and
**Executions** remain separate views. No provider/model catalog is maintained in
React: the Lab reads `GET /v2/media/capabilities` on authenticated load and offers
a capability refresh button. A discovery failure does not disable the other views.

## Controls and submission

- Only available image/video text-generation capabilities can be used.
- Easy offers prompt, media, and image purpose/aspect ratio. Medium adds image
  quality/style; Hard additionally permits optional provider/model selection.
- Video exposes discovered aspect ratios, resolutions, duration limits and optional
  audio. Initial duration is the discovered minimum, resolution the first discovery
  option (currently the lowest tier), and audio off.
- Auto sends no `advanced` object. Hard can use Auto, a provider's usable default,
  or an available model. Choices display names but submit stable IDs.
- Auto shows controls common to available models rather than predicting IG routing.
  Selecting a hard-mode model uses its controls. Invalid stale choices are reset.
- Image purpose comes from discovery; it controls backend routing, not quality or
  style. One output is the current default; a count control appears in richer modes
  only when discovery exposes a count range.

The Lab submits generic requests to `POST /v2/media/generations`. It never sends
provider-native parameters, contacts provider APIs or schedules provider polls.
Validation/configuration messages appear inline. Network/server submission errors
prompt checking Jobs before another submission because acceptance may be ambiguous.
No automatic replay or billable retry is performed.

## Correlation and polling

The backend currently returns an image `request_id`, and video also returns a
`job_id`. Local receipts retain whichever IDs exist. The signed-in workspace loads
current user's Jobs and hydrates their individual input documents. Image receipts
match the exact `Job.input.request_id`; video uses the explicit Job ID. Prompt or
timestamp matching is never used.

Completed Jobs resolve their latest Execution's explicit `metadata.artifact_id`,
then fetch that Artifact. Image and video browsing uses IG#0009 previews; original
content is opened only by an explicit user action. No provider temporary URL or
physical storage URI is used. Missing links and failed previews show fallbacks.

Jobs in `queued`, `running`, `waiting` or `polling` are active. A shared Job feed
refreshes about every three seconds after the previous request completes. It stops
when no active Jobs remain; Executions use the same rule while that view is open.
Navigating to Jobs or pressing Refresh fetches new work without idle polling.
Reload recovers active work from IG, not browser storage. Client caches are bounded
and scoped to the authenticated workspace; logout cancels reads and discards them.
Accepted image receipts missing from the recent list stop automatic recovery after
60 seconds and ask the user to check Jobs. They are not marked failed or replayed.

## Transitional limits

- The current Jobs API returns at most 100 recent user/tenant Jobs. The Lab shows
  active generation Jobs within that window plus 20 recent terminal generations.
  It is not a complete generic history system.
- Collapsed **Legacy image history** retains V1 records, including compatibility
  records for new images. It loads/refreshes manually and may duplicate recent Lab
  images. It has no separate active-image polling loop.
- No panel-layout UI, request replay, raw JSON inspector, pricing or provider-health
  checks are included.
- Auto uses conservative shared controls. Provider/model routing stays entirely
  with the backend, including its existing configuration and compatibility limits.
- Existing Artifact delivery has no byte-range support; playback/seeking of larger
  videos can depend on browser buffering. Storage and continuation behavior are
  unchanged.
- Task 11 can build diagnostics from the existing IDs and metadata; the Lab only
  displays prompt, media/status, provider/model, image purpose and timestamps.

## Checks (no new dependencies)

From `frontend/`:

```bash
node tests/generation.test.mjs
node tests/api.test.mjs
node tests/preview.test.mjs
npm run build
npm run lint
```

Tests use existing TypeScript, Node's test runner and React's server renderer.
HTTP is mocked; previews do not download media and no provider calls are made.

## Manual acceptance — live generation is billable

Use an authorized test tenant with the desired providers enabled. These live
steps are a manual procedure, not an automated smoke test:

1. Easy → Image → General, enter a prompt and Generate. Confirm an automatic route
   completes and renders an image through IG content.
2. Easy → Image → Thumbnail. Confirm the selected backend route appears on the
   completed card without the UI predicting its provider.
3. Hard → Image, select an available provider/model from discovery and Generate.
   Confirm the explicit model appears on the result; switch back to Auto and
   confirm the request omits `advanced` in browser network tools.
4. Easy → Video, choose portrait if available, the lowest discovered resolution,
   minimum duration and audio off. Confirm queued/running/waiting/polling remains
   active and completion yields a native playable video.
5. Hard → Video, explicitly select a discovered provider/model and repeat once.
6. Reload during active work and switch between views. Confirm recovery, stopping
   polls on terminal states, image-only legacy history, and narrower-width layout.
7. Test an unconfigured tenant or a failed capability request: the Lab explains the
   problem while Jobs and Executions remain usable. Verify expired-session and
   failed-Job messages without exposing raw JSON, HTML or credentials.

No live provider generation was executed during implementation.


## Reference attachments (IG#0004)

In Generation Lab, choose **Add reference** and either select a previous Artifact
or upload a new image. Choose its role in the picker; after attachment, compact
cards let you edit the role, choose a supported strength, or remove it:

```text
previous Artifact or image upload → role / strength → structured generation reference
```

The picker browses `GET /v2/artifacts/page`, newest first, 20 rows at a time.
Search filename explicitly with Search or Enter; filter by generated, uploaded,
or legacy/unknown provenance. Load more continues the cursor with the same
filters. Incompatible media/MIME/known sizes and already attached IDs are disabled.
An empty eligible page can still have more pages. Browsing does not poll.

Eligibility, input counts, roles, strengths and byte limits come from
`technical.reference_inputs`. Explicit model selection uses that model; an
explicit provider with Auto model uses its default. Fully automatic selection
uses conservative shared reference constraints across eligible models. A model
without a declared reference contract disables attachments in that shared view;
choose a model in Hard mode for its full supported inputs.

Uploads use multipart `POST /v2/artifacts/uploads`: PNG, JPEG, or WebP only, up to
10 MiB and no larger than the selected input allows. The picker shows uploading
state and safe inline errors. Successful upload immediately attaches the returned
Artifact, without another list lookup. Closing during upload cancels the client
request; a server-completed upload may remain in the private library.

Cards keep stable Artifact IDs plus display/preview metadata. Submission sends
`references: [{artifact_id, role, strength?}]` separately from `intent.description`.
Filenames and preview URLs never act as identities. Slots without strength support
omit strength; a one-image `first_frame` slot naturally exposes only that role.

Changing media/provider/model revalidates the retained cards. Invalid roles,
strengths, media, sizes or counts show messages and block Generate until corrected
or removed. Roles are never silently translated. The backend remains authoritative
for ownership/access, actual byte limits and current capabilities; an Artifact
that becomes inaccessible is rejected with the existing inline submission error.

Picker/cards load only the authenticated preview endpoint, including video contact
sheets. They never render original content or preload video. Processing/failed
previews have a lightweight fallback and do not block compatible selection. The
native modal dialog confines keyboard focus, supports Escape, and returns focus
to Add reference; selection uses ordinary buttons and labeled selects.

IG#0005 can reuse attachment state and structured references for `@artifact`
mentions. Mention parsing/token reconciliation, Google Drive/Photos, preview
retry/admin actions and storage lifecycle changes are not part of IG#0004.

Additional checks:

```bash
node --test tests/*.test.mjs
# With Chromium and its system libraries installed (Node 20):
node --experimental-websocket --test tests/*.test.mjs
```

`references-browser.test.mjs` drives the real Lab with mocked APIs: selection,
search/pagination, role/strength edits, removal, model-change validation, first
frame, upload rejection/success, focus return and preview-only network traffic.
It skips when Chromium or WebSocket support is absent. No live generation is billed.
