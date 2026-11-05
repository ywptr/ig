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
then fetch that Artifact. Image and video previews use only
`/v2/artifacts/{artifact_id}/content`. No provider temporary URL or physical storage
URI is used. Video uses native browser controls. Missing links and failed preview
loads show useful errors/manual refresh rather than guessing an Artifact.

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
- No reference/first-frame selector, Artifact ID input, panel-layout UI, request
  replay, raw JSON inspector, pricing or provider-health checks are included.
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
