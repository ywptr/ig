# IG#0002 — Video resiliency and capability-driven execution

IG keeps the existing Alibaba video adapter and generic Job → Execution → Artifact
pipeline. Sandcastle and other applications are unchanged. No new provider,
provider credential environment variable, or schema migration is introduced.

## Re-audit and recovery model

The production path is `/v2/media/generations` → `submit_media_generation` →
`create_job` → queue → `execute_job` → `execute_video_generation` → adapter submit
→ durable Pending → `schedule_continuation` → `_claim_operation` → adapter poll
and bounded output download → `_finalize_claim` → immutable storage staging →
Artifact/lineage and fenced completion → eager preview after original commit.

Confirmed initial gaps: the worker's queued→running claim could strand an initial
execution; successful submit preceded the only operation checkpoint; repair was
manual; poll leases expired but lost delivery required manual republish; polling
had no overall deadline; schema/global/provider constraints duplicated model
metadata and video selection bypassed `validate_model_capabilities`. Existing
claim-ID/checkpoint fencing, private download protections, cancellation and eager
previews were already implemented and are retained.

`Execution.metadata_json.video_lifecycle` now carries a durable phase:

| Phase/state | Recovery rule |
| --- | --- |
| Queued, delivery overdue | Republish initial work with a fresh transport delivery identity; queued→running DB claim remains authoritative. |
| Running, `prepared`, live lease | Leave it alone. No paid submit boundary has been crossed. |
| Running, expired `prepared` | Mark old execution interrupted and return Job to queued; at most three preparation attempts. Old execution is fenced from submitting/completing. |
| Running, `submitting`, live lease | Leave it alone; provider call may be in flight. |
| Running, stale `submitting`, no operation ID | Fail as `VIDEO_SUBMISSION_UNCERTAIN`; never automatically re-submit. |
| Legacy running without a known submission phase | Treat stale work as uncertain, not safely retryable. |
| Durable operation/checkpoint, waiting and due | Republish continuation of the same operation. |
| Polling, live claim lease | Leave it alone. |
| Polling, expired claim | Republish so normal claim-ID fencing reclaims it. |
| Durable operation with missing token/lease or inconsistent active status | Repair the checkpoint/token and fence stale readers; resume polling, not submission. Invalid ownership/identity fails closed. |
| Deadline elapsed | Fail Job/Execution with a timeout code; preserve operation history. |
| `cancel_requested` | Converge to canceled without provider polling/submission. A late submit response may attach its operation ID for audit only. |
| Completed/failed/canceled | Never restart or resurrect it. |

The initial worker commits the prepared state before dispatch. Immediately before
the paid call, `begin_submission` locks Job and verifies the same running,
owner-scoped Execution is still prepared, then commits the submitting marker.
`checkpoint_submission` immediately records the returned operation and waiting
state, retrying only that DB publication up to three times. The generic worker
recognizes this committed checkpoint and dispatches its continuation. Recovery
and submission always serialize on Job first. The worker's final ownership fence
also checks its Execution ID/status, so an old worker cannot complete a newer
attempt merely because the Job is running again.

### The unavoidable paid-submission ambiguity

The [Alibaba Wan3 API reference](https://www.alibabacloud.com/help/en/model-studio/wan3-video-generation-api-reference/)
documents asynchronous task creation and polling by returned `task_id`. It does
not document a caller-supplied submission idempotency key or a task lookup by an
IG correlation ID. IG therefore does **not** invent an idempotency header or claim
exactly-once provider creation. A process death/network ambiguity after Alibaba
accepts a submit but before IG durably stores its ID remains unresolved.

The submitting marker narrows and identifies that window. DB checkpoint retries
reduce transient publication failures; ambiguous outcomes terminate rather than
risk another paid call. A late response can preserve the ID on a terminal
execution but cannot revive it. Operators must inspect upstream billing/tasks
before choosing a new logical generation. Reusing a Sandcastle submission
idempotency key returns the existing IG job, including its terminal uncertainty;
it does not create replacement provider work.

## Time, leases and polling

| Setting | Default | Bounds | Purpose |
| --- | ---: | --- | --- |
| `VIDEO_MAX_PENDING_SECONDS` | 7200 | 60–82800 | Absolute lifetime captured once per execution |
| `VIDEO_POLL_INTERVAL_SECONDS` | 30 | 5–300 | Durable earliest next poll and normal delayed delivery |
| `VIDEO_SUBMISSION_LEASE_SECONDS` | 300 | 120–900 | Prepared/submitting stale threshold |
| `VIDEO_RECOVERY_BATCH_SIZE` | 50 | 1–500 | Candidate page size |
| `VIDEO_RECOVERY_MAX_ITEMS_PER_RUN` | 500 | 1–10000 | Per-invocation scan/action bound |
| `VIDEO_RECOVERY_MAX_SECONDS` | 240 | 1–600 | Stop starting recovery actions after this time |

The two-hour default is a conservative IG policy, not an Alibaba runtime
promise. Alibaba describes typical generation in minutes and expires task/result
access after 24 hours; the IG lifetime leaves room for queue/provider delays while
remaining below that retention window. No other currently unsupported Wan
operations or expanded material combinations are enabled by this work.

The deadline is anchored to the initial execution/preparation time; the submitting
timestamp is also retained. `started_at`, `deadline_at`, `poll_count`,
`next_poll_at`, submission phase/lease and recovery dispatch lease survive restarts.
Changing environment policy does not extend an existing captured deadline.
Legacy checkpoints bootstrap once from their existing execution start time.
`poll_count` counts claimed attempts, including transient failures, not just
successful status responses. Time is authoritative, with no browser dependency.

The continuation claim checks deadline and earliest poll time before calling the
adapter. Early deliveries reschedule without polling. Pending rotates the
checkpoint token, keeps the original deadline and sets the next due time. The
existing 120-second poll lease remains longer than the adapter's bounded status
read and 60-second download budget. Transient status/download failures retain the
operation/lease for retry; permanent outcomes finalize failed. Deadline checks
also run before storage staging and inside the final publication fence. A late
result cannot publish an Artifact after timeout, cancellation or a replacement
claim. Successful completion still generates the preview eagerly after commit.

Deadline/cancellation are **IG control-plane fences**. Neither claims that an
Alibaba task stopped externally or that billing was canceled. There is no new
upstream cancellation API.

## Capability-driven validation

`validate_video_capabilities` selects the exact declared model metadata and calls
the shared model validator at submission and execution. Unknown model aliases
cannot borrow a different model's constraints. Validation consumes technical
operations (text-to-video versus image-to-video), reference slots/counts/roles,
media/MIME/byte limits, generation controls (duration, resolution, aspect ratio,
audio, count/layout), interface support and prompt limits. Reference ownership
and lifecycle availability are rechecked inside execution.

Video transport schemas enforce types and broad input sanity, not Alibaba's model
ranges. Unsupported but well-formed video model values now produce the existing
400 `INVALID_MEDIA_REQUEST` response; malformed types/shape remain 422. Images
retain their existing schema/legacy behavior. Defaults remain IG's established
5 seconds, 720p, 16:9, audio off. The adapter's declaration retains the existing
supported subset. Removed global video model constants no longer compete with it.

The adapter's final defense invokes the same shared declaration validator for a
resolved request, including actual reference bytes. Only adapter code knows the
native endpoint/payload, upper-case resolution translation, task ID/status parsing,
API timeouts and download host/size/type restrictions. There is no provider status
switch in orchestration or recovery.

## Scheduling, bounds and fairness

`vercel.json` adds an independent five-minute schedule:

```text
GET /v2/internal/video/recover
Authorization: Bearer <CRON_SECRET>
```

The existing operator secret protects it; missing/wrong authentication is 401.
Sandcastle service scopes and tenant sessions do not authorize this maintenance
route. Artifact cleanup remains a separate endpoint/schedule. Configure the secret
in Vercel Production and deploy; rotation is covered in
[maintenance secret operations](artifact-lifecycle.md#cron_secret-configuration-and-rotation).

Five-minute Vercel Cron requires a plan supporting sub-daily schedules (Pro or
Enterprise); Hobby only supports daily cron and will reject this configuration.
Verify the deployment plan before rollout. For another host, schedule the manual
runner every five minutes with the intended DB/queue configuration. Daily repair
is insufficient for the chosen pending lifetime. See
[Vercel cron frequency limits](https://vercel.com/docs/schedules/cron-jobs/usage-and-pricing).
No production scheduler or deployment was changed by implementing repository code.

The sweep keysets by Job creation time and ID across tenants, loads at most the
configured page, and excludes healthy leases in SQL. It commits/rolls back each
job separately and performs queue I/O afterward, with no provider calls in the
sweep. Recovery dispatch leases suppress rapid repeated checkpoint republishing;
a failed/lost repair can be retried after 60 seconds. Queued repair deliveries use
fresh transport identities because reusing the initial transport deduplication
key can suppress a valid recovery; the database remains the execution authority.

Fairness is global oldest-first, with no per-tenant quota. One tenant's older
stale backlog may consume a run. Errors do not abort later jobs/batches. Increase
run budget or frequency if repeatedly limited; later move the same reconciliation
function to a dedicated worker without changing submission/poll semantics. Keep
240 seconds below the deployed function duration with headroom for in-flight
queue/DB I/O; the time budget stops new actions rather than interrupting a call.

Manual operation, after verifying the intended database (prefer inside `ig`):

```sh
PYTHONPATH=. python scripts/recover-video-jobs.py --dry-run
PYTHONPATH=. python scripts/recover-video-jobs.py --batch-size 50 --max-items 500
```

Dry-run classifies the same bounded population and rolls back every inspection;
it does not enqueue or change state. Summary output includes scanned/processed,
requeued/reset/canceled/timed_out/uncertain/invalid/skipped/failed, batch count,
configured bounds, stop reason, limit status and possible additional work. The latter is a bounded lookahead,
not an exact estate-wide count. Requeued/reset counts describe planned/committed
actions; consult `failed` too, since queue delivery can fail after a DB commit.

RQ must use a scheduler-enabled worker (`rq worker --with-scheduler ig`) so delayed
continuations are promoted. Pinned Queue/SDK dependencies are unchanged.

## API and diagnostics

Existing statuses remain queued/running/waiting/polling/cancel_requested/canceled/
completed/failed. Timeout is **failed** plus an additive `error_code` in Job and
Execution read/list APIs:

- `VIDEO_PENDING_TIMEOUT`: deadline exceeded; no more polls/finalization.
- `VIDEO_SUBMISSION_UNCERTAIN`: possibly paid operation with no durable ID; no auto submit.
- `VIDEO_CHECKPOINT_INVALID`: inconsistent state/ownership requiring inspection.
- `VIDEO_PREPARATION_RETRIES_EXHAUSTED`: repeated safe preparation interruptions.
- `VIDEO_PROVIDER_FAILED`: known provider rejection/permanent termination.

Execution metadata also carries terminal error code and lifecycle diagnostics.
Operation ID/provider/model and safe adapter metadata remain durable through
success/failure/cancellation/timeout. Authorized execution metadata already exposed
operation identity before this milestone; that existing API contract is preserved.
No new public operation-control API or credential exposure is added. Do not treat
provider operation IDs as public artifact URLs or as generation-retry instructions.

`video.lifecycle` structured events include trusted job/execution/tenant IDs,
provider/model, operation-present boolean, phase/state, poll count, elapsed time,
action/error code and cancellation state. Recovery logs safe summaries and failure
classes. These logs exclude prompts, credentials, operation IDs, signed URLs,
source locators and raw provider response text. Queue subscriber logging no longer
dumps arbitrary message dictionaries.

## Rollout and remaining limits

1. Deploy code; no migration is required. Existing JSON metadata is lazily
   bootstrapped and old unknown running submissions are never blindly replayed.
2. Keep tenant credentials and endpoint configuration valid for in-flight tasks.
   Provider configuration is resolved per tenant on resume; do not rotate away
   required regional configuration before existing operations finish.
3. Set/retain Production `CRON_SECRET`, check five-minute schedule support and
   function duration, deploy/enable the separate recovery cron, and verify 401
   protection plus an authorized 2xx summary. Enable RQ scheduler where applicable.
4. Inspect dry-run and uncertainty counts before bulk repair of legacy work.
   Monitor timeouts, poll errors, recovery failures and repeated limit hits.
5. Validate a controlled generation/cancellation through the public read APIs.
   No live paid provider operation or production configuration change was made
   during this implementation's tests.

Remaining limits: the paid-submit/DB-checkpoint crash window cannot be eliminated
with the documented upstream contract; inventory/lookup by IG intent is unavailable.
Recovery relies on a healthy recurring scheduler and queue. No upstream task
cancellation, distributed scheduler or new video provider is implemented. Hard
crashes after storage upload can still leave orphan bytes as documented by
IG#0006; known failed/stale writes receive conservative compensation. Preview,
lineage, retention and private Artifact ID authorization stay on their shared path.


## Implementation map and verification

| Path | Responsibility |
| --- | --- |
| `app/media/video_service.py`, `validation.py`, `schemas.py` | Exact model selection/declaration validation and broad transport sanity |
| `app/media/providers/alibaba/video.py` | Single existing provider declaration, translation, status/download parsing and shared defensive validation |
| `app/media/video_tasks.py` | Generic submit/resume, durable boundary and immediate checkpoint publication |
| `app/jobs/video_lifecycle.py` | Durable phases, deadline policy, checkpoint identity preservation, error codes and safe events |
| `app/jobs/worker.py`, `app/executions/service.py` | Initial execution ownership, checkpoint handoff and durable next-poll state |
| `app/jobs/continuation.py` | Earliest-poll/deadline checks, claim-ID fences, output publication and eager preview |
| `app/jobs/video_recovery.py` | Bounded candidate pages, state classification, safe repair and summary reporting |
| `app/jobs/queue.py`, `rq_queue.py`, `vercel_queue.py`, `vercel_subscriber.py` | Queue-neutral fresh repair delivery and safe dispatch logging |
| `app/api/video_recovery.py`, `vercel.json`, `scripts/recover-video-jobs.py` | Separate authenticated schedule and manual/dry-run entry point |
| `app/api/jobs.py`, `executions.py` | Additive public error code with existing authorization and statuses |
| `.env.example`, `compose.dev.yaml` | Documented policy defaults and scheduler-enabled local RQ worker |

Final verification:

- Full backend suite: **487 tests, OK (51 skipped)**. Optional local PostgreSQL
  suites and external Redis integrations account for skipped tests in this run.
- Explicit disposable PostgreSQL video suite: **27 passed**, including submission
  versus recovery, checkpoint versus ambiguous recovery, old-worker replacement
  races, deadlines, cancellation and malformed checkpoints.
- Focused video/continuation/cancellation/eager-preview regression suite: **108
  passed**, followed by targeted final checks and the full suite above.
- Full frontend suite: **60 passed**, including Chromium workflows.
- Frontend build/TypeScript passes. Lint passes with the existing
  `AuthScreen.tsx` set-state-in-effect warning. CLI help and `git diff --check` pass.
- An existing JWT lifetime test was stabilized by using one issuance timestamp
  for its overlong-token case; authentication implementation/policy was unchanged.
- No migration was added, no production infrastructure was modified, and no live
  Alibaba/Blob/queue operation was made for these tests.

### Changed files

- [.env.example](../.env.example)
- [README.md](../README.md)
- [app/api/executions.py](../app/api/executions.py)
- [app/api/jobs.py](../app/api/jobs.py)
- [app/api/video_recovery.py](../app/api/video_recovery.py)
- [app/artifacts/references.py](../app/artifacts/references.py)
- [app/executions/service.py](../app/executions/service.py)
- [app/jobs/continuation.py](../app/jobs/continuation.py)
- [app/jobs/queue.py](../app/jobs/queue.py)
- [app/jobs/rq_queue.py](../app/jobs/rq_queue.py)
- [app/jobs/vercel_queue.py](../app/jobs/vercel_queue.py)
- [app/jobs/vercel_subscriber.py](../app/jobs/vercel_subscriber.py)
- [app/jobs/video_lifecycle.py](../app/jobs/video_lifecycle.py)
- [app/jobs/video_recovery.py](../app/jobs/video_recovery.py)
- [app/jobs/worker.py](../app/jobs/worker.py)
- [app/main.py](../app/main.py)
- [app/media/constraints.py](../app/media/constraints.py)
- [app/media/providers/alibaba/video.py](../app/media/providers/alibaba/video.py)
- [app/media/schemas.py](../app/media/schemas.py)
- [app/media/validation.py](../app/media/validation.py)
- [app/media/video_service.py](../app/media/video_service.py)
- [app/media/video_tasks.py](../app/media/video_tasks.py)
- [compose.dev.yaml](../compose.dev.yaml)
- [docs/async-continuations.md](../docs/async-continuations.md)
- [docs/local-development.md](../docs/local-development.md)
- [docs/video-generation.md](../docs/video-generation.md)
- [docs/video-resiliency.md](../docs/video-resiliency.md)
- [scripts/recover-video-jobs.py](../scripts/recover-video-jobs.py)
- [tests/test_media_capabilities.py](../tests/test_media_capabilities.py)
- [tests/test_service_access.py](../tests/test_service_access.py)
- [tests/test_video_generation.py](../tests/test_video_generation.py)
- [tests/test_video_recovery_postgres.py](../tests/test_video_recovery_postgres.py)
- [tests/test_video_resiliency.py](../tests/test_video_resiliency.py)
- [vercel.json](../vercel.json)
