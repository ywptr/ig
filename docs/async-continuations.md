# Asynchronous operation continuations (V4.1 Task 5)

> IG#0002 adds automatic **video** recovery, durable submission intent, an absolute
> deadline and earliest-poll gating. See [video resiliency](video-resiliency.md).
> The generic continuation/transport contract below remains applicable to other
> capabilities; the historical manual-only recovery limitations are superseded
> for `video.generate`.

Initial handlers remain in `HANDLERS`. A capability supporting asynchronous work
registers a separate `ResumeHandler` in `RESUME_HANDLERS`. The video capability
uses this lifecycle; the deterministic fake remains only in tests.

An initial `Pending` commits provider/model, `provider_operation.operation_id`,
non-secret checkpoint metadata, and a new `continuation.id` in the original
Execution. Job and Execution become `waiting`. After commit,
`schedule_continuation(job_id)` publishes a delayed delivery (30 seconds).

The internal message is:

```json
{"job_id": "...", "kind": "continue", "continuation_id": "..."}
```

It carries a checkpoint token, not provider credentials or an external operation
ID. The external operation and trusted tenant/user are loaded from Execution/Job.
`POST /v2/jobs` does not authorize internal capabilities or continuation dispatch.

`continue_job` conditionally claims the Execution whose current token matches the
message, then conditionally updates the Job. Both become `polling`. The Execution
stores a server-generated `continuation.claim_id` and `lease_expires_at` alongside
the checkpoint token. This claim commits and its session closes before invoking
the resume handler. The handler receives a plain `ProviderOperation` snapshot and
trusted ownership. It never invokes the initial handler.
Pending must retain provider/model/operation ID; Completed may omit provider/model
and inherit them from the persisted operation.

A fresh transaction finalizes only if Execution is still polling with the same
claim ID, checkpoint token, provider/model/operation ID, and trusted ownership.
An expired claim replaced by another worker cannot overwrite that worker's state.
Expiry alone does not discard a result if that claim is still the current owner.

A pending poll rotates the token, clears the claim/expiry fields, updates the
checkpoint, returns both records to waiting, and schedules again after commit.
Completed clears the claim/expiry fields and finalizes the original Job and
Execution, preserving operation history. An old token cannot poll a later
checkpoint, even if the Job is waiting again.

## Transaction and recovery boundaries

The lifecycle has two short database transactions, separated by a provider status
read with **no database session or transaction held by the continuation worker**:

1. Atomically claim waiting work or an expired polling lease, persist claim ID and
   expiry, transition Job/Execution to polling, and commit/close the session.
2. Perform a bounded, idempotent status read of the same external operation.
3. In a new transaction, fence on the current claim and original operation identity,
   persist the outcome, clear the lease, and commit/close. Stale results are ignored.

`POLL_LEASE_SECONDS` is the internal lease policy, currently 120 seconds. Status
read timeouts must be comfortably shorter than this; leases use canonical UTC
timestamps and assume synchronized host clocks. There is no heartbeat, sleeping,
or automatic reconciliation sweep. An active lease rejects duplicate polls. A
later delivery or explicit republish can reclaim an expired lease with a new
claim ID. Slow or interrupted reads may repeat after expiry, but a stale worker
cannot finalize over a newer owner/checkpoint.

A crash before claim commit rolls back to waiting. After claim commit, crashes
and handler/finalization errors leave polling with its lease; errors propagate to
the queue. Early redelivery during an active lease skips polling and does not
schedule a replacement. If no delivery arrives after expiry, an operator must
explicitly republish. Retry exhaustion or expired messages likewise require
operator/reconciliation action. Initial submission errors retain the existing
failed-job behavior and are never implicitly retried.

V4.2 adds a generic `TerminalOperationError`: a known permanent provider failure
finalizes Job/Execution as failed through the same claim fence, clearing the lease
without scheduling or submitting a replacement. Other errors retain retry behavior.

Resume handlers may return Completed with a deferred generic ArtifactPayload.
Bounded bytes are uploaded to storage outside a database transaction. The claim
is checked again after upload; only the current owner can atomically create the
Artifact row, attach its ID, and finalize Job/Execution. A crash or stale claim
after upload can leave an orphan blob, but cannot create a stale Artifact row.

| Crash window | Behavior |
| --- | --- |
| Provider submission succeeds before initial checkpoint commit | Unresolved ambiguity. Initial Job may remain running with no operation ID. Do not reset/re-submit blindly. |
| Checkpoint commits, continuation delivery is lost | Checkpoint survives. Redelivery or explicit internal republish recovers scheduling. There is no automatic outbox/reconciliation sweep. |
| Continuation claims, crashes before poll | Before claim commit, rollback restores waiting. After commit, polling/lease survives; redelivery or explicit republish after expiry can reclaim it without provider resubmission. |
| Poll returns completed, crash before finalization commit | Prior operation/checkpoint and polling lease survive. After expiry, a status read can repeat; exactly-once polling is not guaranteed. Claim-ID fencing rejects late stale results. Artifact/external side effects are not transactionally covered. |
| Pending checkpoint commits, next enqueue fails | Waiting state and new token survive. The old message raises; redelivery republishes the latest checkpoint without polling the stale token. Exhausted/lost deliveries require explicit republish. |

The internal repair entry point is
`app.jobs.continuation.schedule_continuation(job_id)`. It reads the latest durable
waiting token or expired polling token and republishes it. It leaves active leases
alone. A repair tool may call it for a known eligible Job. It does not change
ownership, reset Jobs, or re-submit generation. This task does not install or run
a reconciliation service.

## Queue adapters verified locally

The installed distributions were inspected directly: `vercel-queue==0.9.1`,
`vercel==0.11.5`, and `rq==2.12.0`.

Vercel's `QueueClient.send(topic, payload, *, idempotency_key=..., delay=...)`
accepts a delay in seconds/timedelta. Its installed `_send` implementation converts
the delay to seconds and sends `VQS_HEADER_DELAY_SECONDS`. Each continuation
enqueue uses a fresh delivery UUID in its idempotency key; subsequent rounds and
repair republishes are not permanently deduplicated. Database checkpoint tokens
provide logical poll deduplication. Initial enqueue retains its original Job-ID
idempotency key. The subscriber dispatches in a thread, leaving async queue lease
renewal unblocked and permitting the synchronous adapter to schedule a successor.

RQ uses `enqueue_in(timedelta(seconds=30), continue_job, job_id, token, ...)`,
with a fresh queue Job ID and `Retry(max=3, interval=30)` for continuations. It
requires an RQ scheduler-enabled worker, for example:

```sh
rq worker --with-scheduler --url "$REDIS_URL" ig
```

Existing development launch scripts are not changed by this task; workers started
without the scheduler process immediate image jobs but will not promote delayed
continuations. No Redis/Vercel requests were made during verification.

## Frontend follow-up

Jobs currently considers queued/running active; Executions considers only running
active. Future UI work must include waiting/polling as non-terminal states and
stop on completed/failed. No frontend files are changed in Task 5.

Cancellation also fences waiting/polling continuations. See [job management](job-management.md)
for cancel_requested/canceled states, dispatch races, and provider-side limitations.
