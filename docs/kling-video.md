# Kling video generation

IG registers `kling-4.0` as its second video provider. Kling’s official developer documentation was checked on November 23, 2026. It confirms developer API access for Kling 4.0 and Kling 4.0 Flash. The capability map also lists the 3.0 family. IG registers the full 4.0 model and uses its Omni endpoint for all three supported operations. [API updates](https://kling.ai/document-api/updates/api) · [Video capability map](https://kling.ai/document-api/guides/capability-map/video)

| Setting | IG support |
| --- | --- |
| API and authentication | `https://api-singapore.klingai.com`, with the tenant’s developer API key in a Bearer header |
| Submit and query | `POST /omni-video/kling-4.0`, then `GET /tasks?task_ids=…` at the same regional origin |
| Inputs | Text, an optional first-frame JPEG/PNG, and JPEG/PNG references with character, style, or composition roles. IG supports up to 10 images total, at most one first frame, 10 MiB per image and 30 MiB combined. Images must be at least 300 pixels per side and between 2:5 and 5:2. |
| Output | One MP4 video with native synchronized audio. IG supports SDR at 720p, 1080p, or 4K. |
| Duration and ratio | Integer durations from 3 to 30 seconds. Supported fixed ratios are 16:9, 9:16, 1:1, 21:9, 4:3, and 3:4. |
| Prompt | The API documents a mixed-language limit equivalent to 26,000 English characters or 11,000 non-English characters. IG uses a conservative 11,000-character cap including translated reference instructions. |
| Additional API features | Adaptive ratios, HDR, additional keyframes, video editing, callbacks, provider elements and voice profiles are outside the IG surface. |

The Omni reference endpoint documents native audio and audio preserved from a source video. Voice references use a Kling voice-profile ID to condition speaker identity. They do not take the rendered speech bytes or preserve their timing. IG therefore requires native audio for this model, rejects an explicit request for silent output, and advertises no external-audio slot. The `voice_then_video` preset remains unavailable. Sandcastle can request a Kling clip with native dialogue via its prompt and can generate a separate speech Artifact through the existing audio path. [Authentication](https://kling.ai/document-api/api/get-started/authentication) · [Omni API](https://kling.ai/document-api/api/video/4-0) · [Kling 4.0 developer manual](https://kling.ai/document-api/api/usage-guide/kling-4-0)

## IG lifecycle and routing

Kling translates the provider task ID and state into the same pending or completed contract as Alibaba. Both use the existing Job, Execution, queue continuation, deadline, and recovery paths across RQ and Vercel Queue. Automatic routing considers configured and enabled tenant defaults in registration order, skipping models that fail request or reference validation. Alibaba keeps priority when both models are eligible. Explicit Hard-mode provider/model selection pins the Job, and queued work keeps its original selection.

A definite authentication, request, or submit-rate rejection uses sanitized IG error codes. A transient query failure, including rate limiting, can be polled again. An ambiguous submission remains uncertain and is not automatically submitted again. The verified task API does not document cancellation for this endpoint. IG cancels its own execution and fences late publication. Provider work may continue. [Kling error codes](https://kling.ai/document-api/api/get-started/error-codes) · [Concurrency limits](https://kling.ai/document-api/apiReference/rateLimits)

The adapter accepts HTTPS result locations under Kling’s CDN domain, rejects nonstandard ports and redirects, and sends no API authorization to the download host. Downloads have MIME, timeout, and 100 MiB size bounds and must contain a readable MP4 video stream. Provider result URLs stay out of Job, Execution, Artifact, and workflow metadata. Results use the existing owner storage selection, preview generation, retention, and lineage.

## Configuration and optional smoke test

Register `kling` for a development or Preview tenant using the existing provider configuration tool and enter the developer `api_key` at its secret prompt. The adapter defaults to the documented Singapore origin. The usual `enabled_models.video` and `default_models.video` policies apply. There is no new environment variable or database migration.

```sh
python scripts/configure-tenant-provider.py --tenant-slug YOUR_DEV_TENANT --provider kling
```

1. Verify the tenant’s media discovery lists `kling-4.0` as available and Workflow Lab shows native audio for it.
2. In Generation Lab, choose Kling 4.0 in Hard mode and submit one 3-second 720p text-to-video Job with a short neutral prompt.
3. Verify the Job advances through waiting to completed and returns a video Artifact with a preview and playable original. Check the selected provider and model on the Execution.
4. Check Workflow Lab’s reference-video option and confirm that Voice → Video reports no audio-input video model.

This optional smoke test incurs Kling charges. Preview needs the current `ig-core`, configured tenant credentials, the existing queue subscriber and video recovery schedule, and normal Artifact storage.
