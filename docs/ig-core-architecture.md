# IG package boundaries

## Current package APIs

| Package | Public surface and IG consumers |
| --- | --- |
| `ig-core` | Shared media request and technical capability contracts, provider catalogs, image adapter request/results, async video submission/poll contracts, execution outcomes (`Completed`, `Pending`, and `ProviderOperation`), Artifact payloads and preview helpers. IG imports these from media routing and tasks, Job/Execution workers, and Artifact services. |
| `ig-queue` | `Delivery`, `QueueBackend`, delivery receipts, cancellation results, capabilities, configs and backend factory. RQ supports delayed delivery and pending-delivery retraction. Vercel supports delayed delivery and publish-key deduplication. IG's Job transport wrappers and Vercel subscriber use these primitives. |
| `ig-storage` | `ByteStore`, `ObjectStore`, `StoredObject`, `ObjectStat`, locator parsing, router, configs and local, Blob, and Drive stores. IG's Artifact storage wrappers and Drive integration use these APIs. |

All three package implementations are independent of IG's FastAPI, SQLAlchemy and application modules. Core retains a structural Artifact byte-store protocol, while IG uses `ig-storage` for backend implementation. IG owns Artifact authorization, retention and lineage. The queue package transports opaque work IDs and leaves durable state transitions to consumers.

IG#0014 adds ordered media workflow contracts in `ig_core.workflows` and an IG coordinator over existing Jobs. Workflow reconciliation uses the extracted delayed and recovery delivery primitives. See [Media workflows](media-workflows.md).

## Historical IG#0012 extraction audit

| Class | Existing paths and dependencies | Boundary |
| --- | --- | --- |
| A | `app/media/{schemas,technical,constraints,prompts}.py` use Pydantic, literals and prompt construction. Provider model catalogs describe controls, limits, operation types and lifecycle dates. | Extract the shared request vocabulary, controls, technical metadata, catalogs and prompt translation. Existing interface levels remain part of the shared v2 request contract. |
| A | `app/artifacts/{media_metadata,preview_policy,preview_worker}.py` use bounded bytes, hashes, Pillow and a PyAV subprocess. `app/artifacts/storage/base.py` is a protocol for private byte locators. | Extract inspection, previews and the storage contract. |
| A | `app/jobs/result.py` depends only on the plain `app/artifacts/types.py` payload. Provider video submit/poll outcomes in `app/media/providers/video.py` already use plain values. | Extract payloads and operation/result contracts. |
| B | `app/media/validation.py` calls `validate_reference_shape` in `app/artifacts/references.py`, which also imports SQLAlchemy and Artifact. `capability_schemas.py` combines catalog declarations and tenant discovery responses. | Split pure slot validation and catalog types from actor-scoped lookup and discovery DTOs. |
| B | OpenAI and Google image adapters and Alibaba video translate provider options and results without a session, but base classes call tenant model governance and Alibaba calls shared validation. | Move adapter execution and validation. Keep the tenant model-selection bridge and IG provider registry in the application. |
| C | `app/media/{routing,capabilities,model_governance,service,submissions,video_service,video_tasks}.py` and `app/images/{service,tasks}.py` resolve tenant policy, read encrypted configuration through the application service, authorize inputs, and persist application records. | Keep discovery, routing, credential lookup, workers, and idempotency in IG. |
| C | `app/artifacts/{service,references,previews,library,lineage,orphans,retention,schemas}.py` handle ORM ownership, content access, retention, lineage and preview rows. Storage selection/router, local retention defaults, Vercel Blob and Drive integrations serve application storage policy. | Keep persistence and storage integrations in IG. The local byte implementation is a candidate once its retention assumptions are separated. |
| C | `app/jobs/{service,worker,continuation,cancellation,video_lifecycle,video_recovery,handlers}.py` and `app/executions/{context,service}.py` bind transitions to ORM state, transaction fencing and recovery. `app/jobs/queue.py` includes IG continuation/recovery delivery conventions. | Keep durable states and transitions, queue contract and RQ/Vercel adapters in IG. Current durable statuses are strings used in ORM operations and API representations. |
| C | FastAPI routes, authentication, IS/Nexus, tenant administration, migrations, cron endpoints and the frontend. | Application only. |
| D | Empty, unregistered xAI image/video modules and provider-specific placeholders. | Defer until a supported adapter exists. |

## Integration modes

```text
                     ig-core
            /           |           \
         IG app    Sandcastle V2    future apps
```

In direct mode, the application embeds the package, supplies trusted provider configuration and resolved references, and owns authorization, persistence, recovery, queue delivery and artifact access. Sandcastle retains story, scene and domain decisions.

In service mode, the application uses centralized IG through the IG#0011 service APIs. IG owns provider credentials, job durability and tenant-scoped artifact access. Both modes use the same capability declarations, validation and adapters. No shared deployment model is required.

In IG, PostgreSQL remains authoritative for Job and Execution state. Queue messages deliver work and continuation tokens. Provider operation IDs and neutral outcomes cross the core boundary, while transition and fencing rules remain with durable persistence.


## Implemented package boundary (IG#0012)

IG#0012 implements `ig-core` on this feature branch, ready for Sandcastle V2 to consume as an internal dependency. Its reusable, application-independent scope is:

- Media request contracts, technical capabilities, generation controls and model catalogs
- Prompt translation and provider-neutral outcomes
- OpenAI image, Google image and Alibaba video provider adapters
- Media inspection, deterministic previews and the byte-storage protocol

IG the application retains FastAPI routes, IS/browser authentication, Nexus/tenancy policy, credential persistence, provider routing, ORM/sessions, idempotency, workers, frontend, artifact authorization and retention, and infrastructure integrations. **`ig-core` provides media/provider/capability logic; IG owns durable application state, tenancy, authorization, policy and operations.** Extraction does not make the core a tenant authority, a credential vault or an artifact store.

## Planned `ig-queue` extraction (IG#0013)

`ig-queue` is a reusable scheduling and delivery package, **planned, not part of the IG#0012 implemented boundary**. Unlike a protocol-only library, it may depend directly on Redis, RQ and Vercel Queue. Its intended contracts and implementations cover:

- Job/Execution state vocabulary and delivery envelopes
- Scheduling and delayed continuation
- Retry/recovery primitives and cancellation semantics
- RQ backend and Vercel Queue backend behind one application-facing abstraction

**The consuming application's database is the durable source of truth; `ig-queue` is work delivery and scheduling infrastructure.** Application-owned transitions, transaction fencing, idempotency, tenant authorization, credentials and durable records must not be silently moved into a queue backend. Queue messages may be duplicated, delayed or retried; handlers reconcile against the authoritative application DB. Redis/RQ is not assumed to exist in production: both local/dev RQ and production Vercel Queue must remain supported. The earlier dependency audit describes where queue code currently resides, not a permanent prohibition on extracting it.

## Reusable media workflow architecture (authoritative design; IG#0014 planned)

This section is the **authoritative cross-application workflow design**. Sandcastle's consumer-facing summary is maintained in [Sandcastle's IG media workflow integration](https://github.com/ywptr/sandcastle/blob/master/docs/ig-media-workflow-integration.md). The workflow abstraction below is **planned**, not an implemented public API or frozen Python class hierarchy.

```text
Sandcastle: story/canon/scene reasoning → creative production intent → scene-to-media plan
      ↓ workflow specification
ig-core: operation/capability validation → typed inputs/outputs → provider execution
      ↓ delivery requirements
ig-queue: scheduling → continuation → retries/recovery → RQ or Vercel Queue
      ↕
consuming application DB: durable orchestration state and authorization
```

Sandcastle decides **what** workflow to execute, including dependencies and creative sequencing. The media layer validates **whether and how** a provider/model can execute an operation. Queue infrastructure delivers work and resumes it. Sandcastle must not duplicate provider adapters or RQ/Vercel-specific plumbing. Provider-specific asynchronous submission/polling may be encapsulated in reusable media execution adapters; durable recovery policy and scheduling belong behind the shared queue/application boundary, not in creative code.

### First concrete workflow: voice → video

```text
Scene / shot instruction
        ↓
voice.generate
        ↓
Artifact(audio)
        ↓
video.generate
    ├── prompt
    ├── Artifact(image/reference)
    └── Artifact(audio)
        ↓
Artifact(video)
```

A step's output Artifact becomes a **typed input Artifact** of a dependent step; it is not merely pasted into a prompt. A conceptual `GenerationWorkflow` contains `WorkflowStep` definitions with operation, typed inputs, dependencies and output contract. Names, serialization, persistence schema and execution API remain to be designed. The workflow engine must validate input type, slot support, artifact authorization and lifecycle before execution. A voice-first plan does not imply every video provider accepts an audio input.

### Typed input slots and capability negotiation

Illustrative future slot contracts:

| Slot | Expected type | Meaning |
| --- | --- | --- |
| `first_frame` | `Artifact(image)` | Initial frame / visual reference |
| `voice_track` | `Artifact(audio)` | Generated or supplied speech/audio |
| `character_ref` | `Artifact(image)` | Character identity reference |
| `style_ref` | `Artifact(image)` | Visual style reference |

Each provider/model declares supported operations, input slots, constraints and output types; unsupported required slots must fail validation or lead to a different compatible workflow/model, **never be silently discarded**. Illustratively, the current Alibaba video path supports an image first-frame input but **does not currently support `voice_track`**. Thus the voice → video design is not a claim of end-to-end Alibaba audio-conditioned video support. Kling is a candidate second video provider; verify its exact model capabilities and API behavior before implementing or advertising any slot.

### Provider-neutral operations and voice

Workflows request capabilities/operations such as `text_to_video`, `image_to_video`, `image_audio_to_video` and `text_to_speech`, rather than hard-coding Alibaba, Kling or another provider. Provider/model constraints may be explicitly supplied where necessary, but routing must only choose implementations that satisfy the requested capabilities and typed slots. The same valid workflow specification should be portable across matching implementations.

Planned voice vocabulary includes `text_to_speech`, `voice_profile` and `speaker_reference`. OpenAI, ElevenLabs and future providers are **candidates**; no support for a particular voice operation, speaker cloning or reference format is asserted until verified and implemented. Voice profiles are creative Character-domain associations in Sandcastle; media execution receives only the validated technical voice inputs.

### Direct-core and centralized IG modes

**Direct package mode:**

```text
Sandcastle → ig-core → ig-queue → provider
```

Sandcastle owns its own workflow/domain persistence, application DB, authorization, credential provisioning and Artifact lifecycle/access. The packages supply technical primitives, not those policies.

**Centralized service mode:**

```text
Sandcastle → IG service → ig-core / ig-queue internally → provider
```

IG owns managed credentials, durable Jobs/Executions, tenant-authorized Artifacts, recovery and centralized operations. Sandcastle keeps creative decisions and semantic associations. Both modes must share the **same** technical capability declarations, provider adapters, typed media/workflow primitives and validation semantics; service mode must not become a divergent workflow implementation. A workflow may require a service API extension before it is executable centrally.

## Extraction roadmap and status

| Milestone | Status | Scope |
| --- | --- | --- |
| IG#0012 | **Implemented on feature branch** | `ig-core` foundation |
| IG#0013 | **Planned** | `ig-queue` extraction; RQ and Vercel Queue backends |
| IG#0014 | **Planned** | Reusable typed media workflow abstraction and Artifact chaining |
| IG#0015 | **Planned** | Provider-neutral voice operations and verified voice providers |
| IG#0016 | **Planned** | Second video provider, Kling candidate; validate capabilities |

A feature branch name or prototype does not by itself establish completion of a future milestone.

## Video provider boundaries

Alibaba Wan and Kling 4.0 own their API payloads, credentials translation, status and task identity parsing, CDN host authorization, and provider error mapping. They share media validation and bounded MP4 downloads. The app owns ordered tenant/model selection, approved Artifact inputs, durable video deadlines and continuation tokens, and fenced Artifact publication. Polling and timeout policies are shared across providers, and native audio output is independent of the typed external-audio input. [Kling video](kling-video.md)
