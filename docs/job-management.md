# Job timekeeping and cancellation

Jobs and Executions remain generic control-plane resources. Queue/provider details
stay in adapters. All endpoints require an authenticated, active tenant member and
use both the resolved tenant ID and current IG user ID for resource access.

## UTC invariant

Job and Execution timestamps represent UTC instants. Python uses aware UTC values;
all job/execution API timestamps include `+00:00`. Frontend duration subtracts
absolute instants and may display dates in the viewer's local timezone.
Duration is processing elapsed time from `started_at` to `completed_at`, or now
while active. A queued job without a start has no processing duration yet.

The existing physical SQL columns remain `timestamp without time zone` for
compatibility. `UTCDateTime` normalizes writes to UTC-naive storage and attaches
UTC when reading. Legacy writers used `utcnow()`, so existing values are interpreted
as UTC, never as the PostgreSQL session's local timezone. This is intentional
conversion at the storage boundary, not an instruction to strip arbitrary timezone
information. Cancellation columns follow the same convention.

The 28,800-second UI error came from serializing UTC-naive timestamps without an
offset and then letting a UTC+8 browser parse them as local wall time. Active
elapsed time mixed those parsed values with `Date.now()`. No fixed-hour subtraction
is used. Inspected local completed-job intervals were approximately 5.14 and 40.20
seconds; no historical timestamp data correction was indicated or performed.
Do not rewrite all rows merely because the database session uses Asia/Makassar.

## States

```text
queued -> running -> completed | failed
                  -> waiting -> polling -> waiting | completed | failed
queued | waiting -> canceled
running | polling -> cancel_requested -> canceled
```

`cancel_requested` is active and remains in pending-operation polling.
`canceled`, `completed`, and `failed` are terminal. Execution rows are marked
`canceled` when local work stops. ImageRequest compatibility records follow the
canceled job; pre-existing artifact data is not deleted.

## API

```http
POST /v2/jobs/{job_id}/cancel
```

The response is the current Job representation, including `cancel_requested_at`
and `canceled_at`, plus:

```json
{
  "cancellation": {
    "queue": {
      "supported": true,
      "removed": 1,
      "detail": "Best-effort pending-delivery removal; no provider call was canceled."
    },
    "provider_cancel_supported": false
  }
}
```

Queue details are transport-level diagnostics, not proof that upstream generation
was stopped. A request returns HTTP 200 with either `cancel_requested` or `canceled`.
Repeating an in-flight `cancel_requested` request is idempotent. Attempting to
cancel any terminal Job (including already canceled) returns HTTP 409. Missing,
other-tenant and other-user Jobs return HTTP 404. Unauthenticated callers receive
HTTP 401. Tenant administration does not override another user's job ownership.

Queued work is canceled immediately in the database before transport retraction.
Waiting external operations are also canceled locally immediately; no further
polling is scheduled. A delayed or duplicate delivery cannot claim canceled work.

Running/polling work records a request first. Workers check at dispatch and safe
provider/storage boundaries. On observing the request they stop remaining work,
mark the Job/Execution canceled, and do not retry generation. If submission already
returned an operation ID, it is retained for diagnosis without further polling.

## Races and limitations

Cancellation and completion compete through conditional database updates. Both
initial and continuation finalization lock Job before Execution. If completion
commits first, cancellation returns conflict. If cancellation commits first, the
worker cannot subsequently mark the Job completed or failed. Lease/claim fences
continue to reject stale continuation owners.

A provider dispatch boundary commits authorization immediately before the call,
without keeping a database transaction open across network I/O. If cancellation
wins that boundary, the provider is not called. If dispatch wins first, that call
is considered in flight even if the network send occurs moments later. None of
the current adapters supports upstream cancellation. Generation, provider-side
billing, or an adapter's in-flight download can continue. IG cannot truthfully
claim to have stopped those external operations.

Returned results are discarded before new artifact storage when cancellation is
already visible. Cancellation during an already-started storage upload prevents
artifact-row finalization where fenced, but can leave an unreferenced stored blob.
Storage writes or image artifact commits that won an earlier checkpoint cannot be
undone by cancellation; this endpoint does not delete existing artifacts. Avoid
blind resubmission because the provider may already have billed the generation.

A worker crash can leave `cancel_requested` awaiting acknowledgment, just as
existing crashed execution claims need operational recovery. No process-killing,
new watchdog, automatic generation retry, or new provider API is introduced.

## Queue adapters

`JobQueue.cancel(job_id)` reports whether physical pending-delivery retraction is
supported, a removal count, and a sanitized limitation. IG database state is always
the authoritative cancellation fence.

RQ records delivery IDs in adapter-owned `ig:deliveries:<IG-job-id>` Redis sets
with a seven-day TTL. Cancellation removes queued/scheduled/deferred deliveries
using RQ's cancellation API under a Redis WATCH transaction, so a concurrent
reservation invalidates the pending-only cancellation. It never sends worker stop
commands or changes running RQ jobs to canceled. Legacy/untracked/expired references and enqueue-vs-cancel
races can still leave transport deliveries; those deliveries safely skip IG work.

The current Vercel Queue integration cannot retract deliveries and explicitly
reports `supported: false`. Queue outages also return an explicit limitation while
the already-committed IG cancellation remains effective.

## Deployment and development

Apply the additive Alembic migration for the two nullable cancellation timestamps.
No timestamp-column conversion or historical backfill is required. Production
migrations follow the explicit procedure in [database operations](database-operations.md).

Coordinate deployment of the updated API and workers: drain old worker code before
allowing cancellation requests. Old worker versions do not implement completion
fencing. In Docker development, the worker uses `PYTHONPATH=/app` to prefer mounted
source and must be restarted after code edits, ideally while idle. Uvicorn reloads
API edits automatically. Cancellation itself never restarts a worker.

Validation includes isolated HTTP/worker concurrency tests, UTC/UTC+8 duration
regressions and opt-in Redis tests. Run the latter only with an explicitly selected
`IG_TEST_REDIS_URL`; they create UUID-namespaced queues and clean only owned keys.
They never flush Redis or submit provider generation.

## Implementation map

| Area | Files |
| --- | --- |
| UTC timestamps | `app/core/time.py`, `app/db/models/job.py`, `app/db/models/execution.py`, `app/executions/service.py` |
| Additive migration | `alembic/versions/d42c6e18a907_add_job_cancellation.py` |
| API | `app/api/jobs.py`, `app/api/executions.py` |
| Cancellation and completion fences | `app/jobs/cancellation.py`, `app/jobs/service.py`, `app/jobs/worker.py`, `app/jobs/continuation.py` |
| Queue adapters | `app/jobs/queue.py`, `app/jobs/rq_queue.py`, `app/jobs/vercel_queue.py` |
| Media checkpoints | `app/images/tasks.py`, `app/media/video_tasks.py` |
| Jobs/result UI | `frontend/src/components/Jobs.tsx`, `frontend/src/components/MediaResultCard.tsx`, `frontend/src/App.css` |
| Frontend API/state/time | `frontend/src/api/jobs.ts`, `frontend/src/api/generations.ts`, `frontend/src/lib/generation.ts`, `frontend/src/lib/jobDuration.ts`, `frontend/src/types/Job.tsx`, `frontend/src/types/media.ts` |
| Tests | `tests/test_job_cancellation.py`, `tests/test_rq_cancellation_integration.py`, `tests/test_job_continuation.py`, `tests/test_media_job_contracts.py`, `frontend/tests/job-duration.test.mjs` |
| Runtime/documentation | `compose.dev.yaml`, `README.md`, `docs/async-continuations.md`, `docs/local-development.md`, `docs/job-management.md` |
