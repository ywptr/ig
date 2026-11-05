# Generic video generation (V4.2)

`POST /v2/media/generations` accepts `media_type: "video"` using the existing
authenticated tenant context. Video creates one internal `video.generate` Job,
one Execution, and on success one Artifact. It creates no ImageRequest or
VideoRequest. Public `POST /v2/jobs` cannot dispatch video generation.

```json
{
  "media_type": "video",
  "interface_level": "easy",
  "intent": {"description": "A slow sunrise over the sea"},
  "output": {
    "duration_seconds": 5,
    "aspect_ratio": "9:16",
    "resolution": "720p",
    "audio": false
  },
  "references": []
}
```

The 202 response includes `media_type`, `status: "queued"`, `job_id`, and
`request_id` (an alias for Job ID on video submissions). Existing image response
fields/shape are unchanged. Follow `GET /v2/jobs/{job_id}` and
`GET /v2/jobs/{job_id}/executions`; completed Execution metadata contains
`artifact_id`. Read its metadata/content using the existing artifact endpoints.

## Initial contract

- One MP4, `count=1`, `strategy=single`, `layout.type=single`.
- Integer duration 2–30 seconds (default 5).
- `resolution`: `480p`, `720p`, `1080p` (default `720p`).
- `aspect_ratio`: `16:9`, `9:16`, `1:1` (default `16:9`).
- `audio`: boolean, default false.
- Prompt length 1–20,000 characters; whitespace-only prompts are rejected.
- Zero references, or one `{ "artifact_id": "...", "role": "first_frame" }`.
- First frame must be the current tenant/user's image Artifact, PNG/JPEG/WebP,
  at most 10 MiB. Ownership/type are checked before enqueue and rechecked by the
  worker. Binary reads are bounded; physical private storage URLs are not inputs.
- Image-style/quality controls are not applied: only default `style=auto` and
  `quality=standard` are accepted. Panel layouts, variations, compare, reference
  strength, and provider parameter dictionaries are not supported for video.

Image aspect ratios and reference roles retain their existing validation. Unknown
video fields are rejected rather than passed through to a provider.

## Provider/model selection

Easy and medium do not accept advanced settings. Hard optionally accepts
`advanced.provider` and `advanced.model`; it still uses the same generic output
and reference contract. There is no `impossible` interface level.

Without a provider selection, exactly one enabled, registered video-capable
tenant provider must exist. Multiple configured video providers require explicit
hard-mode selection until tenant routing preferences are introduced. A model is
resolved in this order: explicit hard-mode model, tenant
`config.default_models.video`, adapter default. Supported models are validated
before enqueue, together with enabled configuration/credentials. Account/region
availability still needs operator verification; IG does not make billable probes.

Legacy image `config.default_model` is preserved and ignored for video. Resolved
provider/model are persisted in server-created Job input; continuation uses the
persisted operation identity rather than resolving a new default.

The first provider's configuration and manual smoke test are documented in
[Alibaba Model Studio video](providers/alibaba-video.md).

## Boundaries and lifecycle

`ResolvedVideoRequest` and `VideoProviderAdapter.submit/poll` are provider-layer
interfaces. Adapters return `VideoProviderPending` or `VideoProviderCompleted`;
they do not import job types. Generic video handlers translate those outcomes to
Jobs Pending/Completed. Provider terminal errors become a generic
`TerminalOperationError` finalized under the same current-claim fence.

Initial submission commits an operation checkpoint, then existing delayed
continuations poll that operation. The leased claim, identity/ownership fencing,
and scheduling behavior are described in [async continuations](async-continuations.md).
Transient errors keep the polling lease for retry/recovery; terminal operation
errors fail the original Job/Execution without submitting a replacement.

Completed bytes travel as a generic deferred ArtifactPayload. The resume handler
does not create an Artifact row. Storage upload occurs outside a database
transaction; finalization rechecks the claim before atomically creating the
Artifact row and recording its ID alongside Job/Execution completion. Duplicate
or stale completion cannot create a second Artifact row.

## Limits and follow-up

- No frontend video flow yet. It needs media selection, optional hard-mode model
  choices using [tenant-aware discovery](media-capabilities.md), Job correlation/polling with waiting and
  polling active, and a video Artifact renderer. Easy/medium should not require
  provider/model selection.
- No last-frame, multi-reference, reference-video/audio, editing, or extension.
- Existing put_bytes storage requires bounded output assembly in memory. True
  streaming to storage and HTTP byte ranges remain future work. Blob reads also
  retain the existing buffering behavior.
- Uploads and DB commits are not atomic. A crash/stale claim after upload can
  leave an orphan blob, although it creates no stale Artifact row. Cleanup/outbox
  reconciliation is not introduced here.
- Initial provider submission before checkpoint commit remains ambiguous after a
  crash. Lost/exhausted continuation deliveries may require internal republish.
  Never reset a Job blindly or submit a replacement to recover a status read.
