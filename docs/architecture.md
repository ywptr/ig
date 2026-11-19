# Internal package boundaries

| Component | Ownership |
| --- | --- |
| `ig-core` | Reusable AI/media execution primitives and provider adapters. Developed on its own extraction branch. |
| `ig-queue` | Reusable work envelopes, delivery, scheduling, and backend-specific transport behavior. |
| IG | Application API, authentication, tenant authorization, durable Job and Execution state, provider configuration, and recovery policy. |
| Sandcastle V2 | Its own domain, persistence, authorization, and recovery policy. It can use the reusable packages around its own work records. |
| `ig-storage` | Planned next extraction for reusable artifact byte storage contracts and adapters. |

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

For IG#0014, start with the artifact byte-store contract and audit the local, Vercel Blob, and Google Drive adapters. Keep artifact metadata, tenant access policy, credential lifecycle, preview handling, and retention/deletion decisions in IG. Resolve how each adapter receives explicit configuration before moving it.
