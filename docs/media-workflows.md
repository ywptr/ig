# Media workflows

IG workflows run bounded, ordered media plans using the existing generation Jobs. Independent steps also run in order and the first failure stops the remaining plan.

## Contracts and ownership

`ig_core.workflows.GenerationWorkflow` is a versioned, serializable plan for independently installed consumers. A `WorkflowStep` declares its ID, operation, input map, earlier dependencies, optional provider/model constraint, and expected output media type. The core validates operation signatures, duplicate step IDs, forward or cyclic dependencies, and typed bindings. Each operation takes a text `prompt`. Additional inputs are typed Artifact IDs or references to outputs of named dependency steps. The coordinator selects the first matching output in stable Artifact creation/ID order. Presets request one output per step.

The operation vocabulary includes `text_to_speech`, `text_to_image`, `image_to_image`, `text_to_video`, `image_to_video`, `image_audio_to_video`, and `audio_to_video`. A model must advertise the operation, media types, and applicable reference slots. The application must also have an execution path for the operation. IG#0015 makes speech executable for configured tenants. The current catalog has no audio-conditioned video adapter.

IG owns the workflow ID, tenant and actor ownership, preset expansion, dispatch and credentials. The Job owns execution and provider continuation. The Artifact generation record identifies the producing Job and Execution. Standard Artifact input lineage records the roles and IDs of consumed references, including `voice_track`. Workers use the existing storage router; step bindings carry Artifact IDs.

## Scheduling, cancellation, and retry

PostgreSQL stores the immutable plan, workflow status, next reconciliation time, and bounded-order step state on a workflow row. Each step records Job attempt IDs, outputs, status, and any error. A PostgreSQL row lock serializes transitions. The workflow update and new Job commit together. IG publishes after commit, and the existing Job claim fences duplicate deliveries.

Workflow wake-ups use the existing `ig-queue` recovery envelope with an opaque workflow locator. The existing `ig-jobs` Vercel subscriber and RQ worker both dispatch it. Each active run schedules a delayed check after 30 seconds. The check advances completed steps and can republish a queued Job under a fresh delivery identity. Video provider operation polling and checkpoint recovery stay on the existing video path. RQ deployments need `rq worker --with-scheduler ig`.

`GET /v2/internal/workflows/recover` reconciles lost workflow wake-ups in bounded batches, with the existing `CRON_SECRET` convention. The configured Vercel schedule provides a daily backstop. RQ deployments can call the same endpoint from their scheduler. Run the endpoint manually in Preview to check recovery. An interrupted synchronous Job whose outcome remains uncertain is fenced after 15 minutes and flagged for inspection.

Cancel fences the active Job and prevents future steps from starting. Queued and waiting Jobs cancel immediately in the database. Running or polling work acknowledges cancellation at existing Job boundaries, and the workflow settles after reconciliation. Providers may continue work already submitted. Completed outputs remain available under standard Artifact retention.

A retry creates another Job for the failed step and keeps preceding step outputs and all attempt IDs. Ambiguous or expired video submissions and interrupted synchronous Jobs are excluded from automatic replay and workflow retry. Explicit retry of an ordinary failure can start another billable operation. A retry reuses the same plan and validates references and tenant model selection again.

## API and Workflow Lab

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/v2/workflows/presets` | Tenant capability-aware plans and model choices |
| POST | `/v2/workflows` | Submit a preset and initial inputs |
| GET | `/v2/workflows/{workflow_id}` | Inspect plan, dependencies, status, attempts, and outputs |
| POST | `/v2/workflows/{workflow_id}/cancel` | Request cancellation |
| POST | `/v2/workflows/{workflow_id}/retry` | Retry a safely retryable failed step |

The routes require an interactive tenant membership and use tenant and actor ownership. Workflow service delegation requires a future explicit route and scope policy. An optional `Idempotency-Key` is scoped to tenant and actor. Reuse with the same submission returns the original run, and a different submission returns a conflict. An optional `correlation_id` is echoed on inspection.

Workflow Lab at `/workflows` shows the planned steps, enables compatible provider/model selections, and accepts image references from the Artifact library or by ID. Automatic selection pins the first compatible configured model in catalog order. On a run, the Lab polls active status and stops on a terminal state. It shows intermediate and final Artifact metadata, requests the existing image/video preview, and opens original media on demand. Copy the run URL or inspect by workflow ID to revisit a run.

`single_generation` supports speech, image, and video operations for compatible tenant models. Speech options are model-scoped and the result has authenticated playback in the Lab. `voice_then_video` shows voice availability and remains unavailable to run while audio-conditioned video is missing. Automated tests use fixture-only execution for the combined chain and lineage. Generation Lab continues through the existing generation routes. [Voice generation](voice-generation.md)

## Sandcastle example

Sandcastle chooses the dialogue, shot prompt, and first-frame image Artifact as part of its scene production plan. Once compatible voice and audio-conditioned video models and delegation are available, the preset body is:

```json
{
  "preset": "voice_then_video",
  "dialogue": "We're ready to leave.",
  "prompt": "A wide shot of the character on the deck at dawn.",
  "image_artifact_id": "b0ce2c5d-8311-465e-87fe-7062f15dd3b1",
  "correlation_id": "scene-04-shot-01"
}
```

IG would produce audio, bind its Artifact ID as `voice_track` with the supplied `first_frame`, and produce video. Sandcastle owns scene and story reasoning. IG executes the plan, selects by capability, and records both Artifacts and their lineage. Sandcastle can also construct and inspect plans directly with the standalone core contracts.

## Migration and Preview

Alembic revision `1a0014000014` adds only `media_workflows` and its constraints and index. Existing application versions can run against the upgraded schema. Downgrade removes workflow records. Apply the revision to the Preview database before exercising the Lab. Verify package loading, tenant discovery, the existing queue subscriber, and protected workflow recovery against Preview configuration. Promote and migrate production only through the separate authorized release flow.
