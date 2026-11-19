# Internal package boundaries

| Component | Ownership |
| --- | --- |
| `ig-core` | Reusable AI/media execution primitives and provider adapters. Developed on its own extraction branch. |
| `ig-queue` | Reusable work envelopes, delivery, scheduling, and backend-specific transport behavior. |
| IG | Application API, authentication, tenant authorization, durable Job and Execution state, provider configuration, and recovery policy. |
| Sandcastle V2 | Its own domain, persistence, authorization, and recovery policy. It can use the reusable packages around its own work records. |
| `ig-storage` | Byte/object storage, locator routing, and local, Vercel Blob, and Google Drive adapters. |

The application commits durable state before publication. A work delivery locates that state so the worker can claim it, verify cancellation or a continuation checkpoint, and handle duplicate messages. Queue receipts describe transport operations. The application's database owns progress and outcome.

## IG#0013 queue audit

| Class | Existing code and disposition |
| --- | --- |
| A · Safe to extract | The transport operations behind `RQJobQueue` and `VercelJobQueue`, including Redis connection, RQ enqueue and scheduled enqueue, retry translation, Redis tracking and watched retraction, and the SDK publish call. |
| B · Small decoupling | The queue protocol and continuation validation now accept opaque work. SDK client construction takes explicit settings. Subscriber dispatch takes a callback and a decoder. IG supplies the legacy wire format, RQ callable arguments, and publish identity inputs. |
| C · IG-specific | `get_job_queue` translates IG environment settings. Worker and scheduler startup commands, the Vercel subscriber declaration and poller entrypoint stay with IG. `execute_job`, `continue_job`, cancellation fencing, submission idempotency, and `video_recovery` retain durable claim and repair policy. |
| D · Defer | Additional worker process management, cross-app recovery scanners, provider cancellation and a new queue wire format for existing IG deployments. |

Before extraction, image submission and video recovery both called IG's `JobQueue.enqueue`. Continuation passed a delay and checkpoint token through the same interface. RQ selected the worker callable and tracked its generated job ID in Redis. Vercel published a stable initial key and fresh continuation/recovery keys. Its subscriber dispatched to the same workers. IG first committed the cancellation fence and then requested best-effort retraction.

IG keeps the same queue names, Redis key namespace, Vercel wire payload, subscriber registration, worker arguments, 600-second RQ timeout, RQ continuation retry settings, and Vercel subscriber attempt limit. Existing queued messages can continue through their original entrypoints. The package's native API is documented in [ig-queue](../packages/ig-queue/README.md).

The Compose dev worker enables the RQ scheduler. The legacy cloud Redis scripts are immediate-worker entrypoints and omit `--with-scheduler`. Delayed work requires a scheduler-enabled worker for the queue.

## IG#0014 storage audit

| Class | Existing code and disposition |
| --- | --- |
| A · Safe to extract | Local descriptor-relative containment, immutable writes, stream reads, and idempotent deletes. Blob private upload, SDK read, existence, and delete calls. Drive resumable upload, streaming download, file stat/delete and folder validation. |
| B · Small decoupling | Blob SDK credential lookup becomes explicit application configuration. The router accepts configured stores or resolver callbacks. Drive operations receive a credential-bound runtime and opaque namespace. IG's upload tags are supplied separately from the HTTP transport. |
| C · IG-specific | `select_write_store`, Drive OAuth and account setup, connection and encryption records, health reporting, Artifact rows and authorizations, derivative backend affinity, preview leases, and retention and deletion fences. |
| D · Defer | Cross-store migration and archive jobs, streaming-input writers, and a replacement for the pinned Blob SDK's buffered download. |

Before extraction, IG's router forwarded writes to the managed provider, and selected the backend of existing bytes by locator. Owner-aware selection overrode the managed writer for enabled Drive users. The Drive adapter mixed file operations with connection resolution and health reporting. Previews selected the original object's store and Drive connection. Retention selected policy from the stored locator and committed its deletion fence before removing bytes.

The package retains local `file:` paths, `vercel-blob:///pathname`, and `gdrive://namespace/file-id`. IG maintains its UUID connection checks and binds the namespace to the correct Drive runtime. Retention defaults remain in the IG compatibility adapters. Authorization, credential persistence, and artifact or preview metadata stay in IG. [Storage API](../packages/ig-storage/README.md)

`ig-core`, `ig-queue`, and `ig-storage` are batteries-included internal Python packages. Each uses a setuptools wheel and can use a repository-relative uv source within the same monorepo. This branch includes the queue and storage packages. The separate `ig-core` extraction uses the same packaging pattern. Shared deployment hardening remains a separate task.
