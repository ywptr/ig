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
