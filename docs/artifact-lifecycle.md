# Artifact lifecycle and retention — IG#0006

## Policy and scope

For IG-managed/Vercel storage: generated retention defaults ON at 28 days.

For future user-owned Google Drive storage: retention defaults OFF unless tenant
policy enables it. No Drive or Photos adapter or controls are implemented here.

Only `provenance=generated` is governed. Uploaded references, uploaded external
generations (which have uploaded provenance), imported and unknown-provenance
records are excluded. A generated artifact later used as a reference retains its
generated provenance and can expire once active jobs finish. Historical use does
not pin content indefinitely. This policy never cascades to downstream artifacts.

Tenant admins can use Settings → Generated media retention to turn cleanup on or
off and choose 1–3650 whole days. The boolean is explicit; zero never means off.
The duration remains valid while disabled. Both settings are tenant-scoped:

- `generated_artifact_retention_enabled`: nullable boolean override.
- `generated_artifact_retention_days`: nullable integer override.

NULL means storage default, rather than an implicit tenant choice. Admin updates
persist both explicit values; changing backend defaults never overrides these.
GET includes `retention_policy_source` (`storage_default` or `tenant`). There is no
reset-to-default UI in this release. Existing tenants with NULL overrides also
receive the new defaults: **inspect old generated content before enabling cron**.

The UI reads defaults for the current write policy. Cleanup resolves defaults for
each original's owning backend, so changing the deployment write destination does
not accidentally change the recommendation for historical objects. Providers can
advertise `retention_default_enabled` and `retention_default_days`; unknown/new
providers default to disabled, 28 days. Explicit tenant choices take precedence.

## Audit findings before implementation

The initial code paths were:

1. `app/artifacts/storage/provider.py` constructs the deployment-global writer
   from `ARTIFACT_STORE_PROVIDER` (`local` or `vercel`). Tenant configuration did
   not select storage. `RoutingArtifactStore._store_for_uri` already routed reads
   for `file` and `vercel-blob` independently of the writer.
2. `ArtifactStore` exposed put/exists/iter_bytes, with no deletion operation.
   Local reads enforce descriptor-relative containment, including symlink checks;
   Vercel uses private Blob objects and opaque internal locators.
3. `ArtifactPreview` is a versioned mechanical derivative, not an Artifact.
   `ensure_preview` leases/fences publication and stores a separate object;
   historical preview versions remain readable. There was no retirement path.
4. `Artifact.provenance`, `ArtifactGeneration`, and `ArtifactInput` distinguish
   uploaded/generated content and retain job/execution/source relationships.
   Job inputs additionally contain reference IDs before output lineage exists.
5. `TenantSettings` and `/v2/tenant/settings` already own tenant policy.
   `get_admin_tenant_context` checks admin membership. Service authentication is
   route-allowlisted and denies all tenant settings endpoints, even for delegated
   admin actors. React had no tenant administration surface.
6. Originals can become orphaned after successful put and failed upload/image DB
   publication, cancellation, or superseded video finalization. Preview objects
   can become orphaned after a lost claim or publication failure. Process death
   and ambiguous commits require special care; absence of a visible row alone
   does not authorize a general bucket sweep.
7. Vercel Queue supports work delivery/continuations but no daily maintenance
   scheduler was configured. A bounded Vercel Cron endpoint is sufficient.

## Lifecycle, identity and authorization

Artifact rows and all lineage stay in place. Metadata identity is independent of
content and storage backend. No artifact, generation, input, job or execution row
is deleted by retention.

- **Active**: neither cleanup timestamp exists. Policy evaluation can make this
  artifact eligible; eligibility is computed, not persisted as a stale status.
- **Eligible for cleanup**: generated, strictly older than its effective policy
  duration, retention enabled, no active input job or live preview claim, and not
  already removed.
- **Removing**: `cleanup_started_at` durably fences content before external I/O.
  This is a committed removal decision and survives process/storage failures.
- **Removed**: `content_removed_at` acknowledges original and preview removal;
  `content_removal_reason=retention` explains why.

Age uses Artifact `created_at`, interpreted as UTC (legacy naive timestamps are
UTC). Exactly at the threshold is retained; strictly older is eligible. Storage
file timestamps, filenames, and provider timestamps do not determine retention.

Queued/running/waiting/polling/cancel-requested (and any unknown nonterminal) jobs
protect input IDs in their tenant-scoped input JSON. Terminal statuses are
completed/failed/canceled. Conservative substring matching supports legacy job
wrappers; a false positive retains content. It does not read all jobs into Python.
Policy updates, input admission and cleanup decisions share a tenant row lock; admission rechecks
ownership and the Artifact fence under lock. PostgreSQL supplies the cross-worker
locking guarantee; SQLite is for single-worker development/testing. Existing live
preview leases also postpone cleanup. Preview admission locks the original;
expired preview processors cannot publish across the original's cleanup fence.

Cleanup authorization never changes ownership. Ordinary members cannot read or
change retention settings. Service scopes never grant tenant administration.
`get_admin_tenant_context` additionally rejects a service caller defensively.

## API and UI

Admin-only GET/PATCH `/v2/tenant/settings/retention` reads/writes the two settings.
PATCH requires both values, validates strict boolean/integer types and 1–3650
bounds (422 on invalid input), and returns the effective policy. No storage or
provider secrets appear. Existing authentication settings APIs stay compatible.

Artifact metadata and listings remain 200 for authorized owners and include:

- `content_available`: false once cleanup begins.
- `content_status`: `active`, `removing`, or `removed`.
- `content_removed_at`: UTC timestamp after successful removal.
- `content_removal_reason`: `retention`.

Both `/v2/artifacts/{id}/content` and `/preview` return 410 with
`ARTIFACT_CONTENT_REMOVED` after fencing. The legacy `/v1/images/{request}/content`
route applies the same check when it has an Artifact ID. Unauthorized/wrong-owner
requests still return their normal denial/404 before lifecycle information.
Metadata never returns physical locators. Preview metadata is `deleted` with no
preview URL once cleanup starts. Browser responses use private, no-store for 410.
Already downloaded content cannot be recalled; a read admitted before the fence
may finish. Future library UI can render “Expired / Content removed by retention
policy” using these fields; no full library-management UI is added.

## Runner and scheduling

Manual inspection from the repository root:

```sh
PYTHONPATH=. .venv/bin/python scripts/cleanup-artifacts.py --dry-run
PYTHONPATH=. .venv/bin/python scripts/cleanup-artifacts.py --batch-size 50 --max-items 500 --scan-limit 1000
```

Set the intended `DATABASE_URL` and storage credentials in the invoking process.
The script does not load a production env file or print connection details.
The scheduled route and CLI share these environment-configured defaults:

| Setting | Default | Allowed range | Meaning |
| --- | ---: | ---: | --- |
| `ARTIFACT_CLEANUP_BATCH_SIZE` | 50 | 1–500 | Candidate rows fetched per bounded page |
| `ARTIFACT_CLEANUP_MAX_ITEMS_PER_RUN` | 500 | 1–10000 | Attempted eligible removals per invocation |
| `ARTIFACT_CLEANUP_SCAN_LIMIT` | 1000 | 1–10000 | Candidate inspections per invocation |
| `ARTIFACT_CLEANUP_MAX_SECONDS` | 240 | 1–600 | Stop starting new items after this many seconds |

Batch size, run budget and schedule frequency are independent. The runner repeats
small batches until exhaustion or an item/scan/time budget stops it. CLI options
`--batch-size`, `--max-items` (`--limit` remains an alias), `--scan-limit`, and
`--max-seconds` override environment values for that invocation. Invalid bounds
fail before any deletion. No query materializes the whole eligible population.
Policy/age/active-reference filtering happens in each candidate query and is
rechecked under the existing per-object locks.

Pages use a `(created_at, artifact_id)` cursor, globally oldest first across all
tenants. Advancing past failed, skipped and dry-run rows avoids retry loops and
OFFSET skipping when earlier rows disappear. There is no per-tenant quota: one
tenant's older backlog can consume the run budget. This deliberately simple age
ordering prioritizes the oldest retained bytes; it does not guarantee tenant
round-robin fairness. Newer arrivals do not jump ahead of older work. Monitor
sustained backlogs; tenant scheduling can be added later without changing deletion.

Summary fields retain `scanned`, `eligible`, `processed`, `deleted`, `failed`, and
`bytes_reclaimed`, and add `eligible_seen` (alias of `eligible`),
`batches_processed`, `batch_size`, `max_items_per_run`, `limit_reached`,
`remaining_work_possible`, and `stop_reason` (`exhausted`, `item_limit`,
`scan_limit`, `time_limit`). The API returns these values and the runner logs the
same safe summary, without secrets/locators. A one-row lookahead beyond the cursor
sets `remaining_work_possible` when a budget stops the run. It is advisory under
concurrent activity, not an exact backlog count. Hitting an exact budget with no
more candidates yields `limit_reached=true`, `remaining_work_possible=false`.
Batches count nonempty pages examined, including dry-run. Failures consume a
processing slot when a removal was attempted, and later batches still run.

Dry-run uses the same paging and budgets; its item budget counts eligible
inspections, while `processed`, `deleted` and reclaimed bytes stay zero.
Dry-run never marks rows or calls storage delete. Reclaimed bytes use recorded
original/preview sizes, counted only for objects observed present and successfully
removed/acknowledged. This estimates logical stored bytes, not provider billing;
crashes, concurrent external deletion and unknown historical sizes can undercount.

A removal decision commits before I/O; each preview version is removed and
acknowledged separately, then original bytes are removed and the original is
acknowledged. All preview rows remain as `deleted` with NULL locator. No long
transaction spans storage I/O. Individual failures do not abort the batch. A
15-minute decision lease prevents immediate duplicate processing; failed/crashed
removals become retryable after it. Idempotent delete makes partial removal safe.
Disabling retention affects future decisions; it does not revoke an already
committed removal decision. Keep invocation duration below 15 minutes. Original
locators remain internal historical metadata, even after content removal.

`vercel.json` adds daily `0 3 * * *` (UTC) invocation of
`GET /v2/internal/artifacts/cleanup`. It requires an exact bearer `CRON_SECRET`;
missing configuration fails closed. This operator credential is separate from
IS sessions and service tokens. A daily invocation can process up to 500 artifacts
in pages of 50 by default, subject to scan/time bounds. If cleanup repeatedly
reaches its run limit:

- Increase max items (and scan budget if needed), keeping bounded pages.
- Increase schedule frequency in `vercel.json` and redeploy.
- Later move the same runner to a dedicated worker for larger workloads.

Do not enlarge a database transaction: object fences and acknowledgements remain
independent commits. The 240-second default leaves headroom below a typical
300-second Fluid Compute function duration; verify the actual deployment's
configured duration before rollout. The time budget is checked between items,
not an interruption of in-flight storage calls. Allow enough headroom for a slow
object; platform termination still recovers through the existing deletion fence.
The configured upper bound remains below the 15-minute retry lease. See
[Vercel function duration limits](https://vercel.com/docs/functions/limitations).
No browser visit or idle polling triggers maintenance. This uses the still-supported
Cron configuration already compatible with this deployment; adopting the newer
Schedules product is a separate infrastructure decision. See [Vercel cron
configuration and security](https://vercel.com/docs/schedules/cron-jobs/manage-cron-jobs). Local installations can use
system cron to invoke the CLI. Queue pins remain unchanged.

### CRON_SECRET configuration and rotation

`CRON_SECRET` protects the operator-only scheduled cleanup endpoint, independently
of tenant sessions, IS identities and Sandcastle service scopes. The actual
contract is `GET /v2/internal/artifacts/cleanup` with an exact header:

```text
Authorization: Bearer <CRON_SECRET>
```

The handler compares that header with the environment value using
`hmac.compare_digest`. Missing/empty configuration or a missing/wrong header
returns 401 without cleanup. The schedule header or user-agent alone does not
authorize a request. The secret and authorization header must never be logged.

Generate a strong random value privately using Python already available in IG:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Store it in a secret manager. Never reuse application, user, IS or provider
credentials. Never commit the generated value; `.env.example` contains only
`CRON_SECRET=` and instructions. For production, set `CRON_SECRET` in the IG Vercel
project's **Settings → Environment Variables → Production**, then deploy.
Vercel sends the active deployment's captured value with the Bearer prefix.
Changing project settings alone does not update an existing deployment.

To rotate: generate/store a replacement, update the Production environment
variable, redeploy and wait until that deployment is Current, then confirm the
next scheduled request returns 2xx. Update any manual caller's secret securely at
that switch. IG accepts one current value, with no dual-secret grace period.
Promotions/rollbacks can restore a deployment's older captured value; account for
that when updating manual callers. Investigate 401 via environment scope and
deployment version, without printing values. This follows the
[Vercel cron authentication/rotation contract](https://vercel.com/docs/schedules/cron-jobs/manage-cron-jobs).
Local CLI execution needs no cron secret; a locally scheduled HTTP invocation
needs a separately configured development secret and the same header.

Production rollout (not performed by this follow-up):

1. Review existing generated content with a dry-run and set explicit policy
   exceptions. For initial IG#0006 rollout, apply `1a0006000006` after
   `1a0003000003` before serving lifecycle code. This follow-up adds no migration.
2. Configure the Production cron secret and cleanup budgets. Start with batch 50,
   max items 500, scan 1000, 240 seconds; verify runtime duration/headroom and
   retain all required local/Blob deletion credentials.
3. Deploy, make the new deployment Current and enable the existing daily cron.
   Verify missing/wrong authentication returns 401 and the scheduled request
   returns 2xx without printing authorization headers.
4. Inspect summary counters/stop reason/backlog and storage metrics. Increase
   run budget/cadence when repeatedly limited; keep transactions small. Confirm
   the first scheduled invocation after every secret rotation succeeds.

## Orphan coverage and limitations

`app/artifacts/orphans.py` tracks known, freshly allocated immutable writes in the
publisher Session. A successful commit clears receipts. Rollback/close verifies
in a separate session that neither an original nor a preview row owns the locator
before attempting deletion. A DB outage/ambiguous commit defers removal. Image
and upload publication use this compensation; preview publication also tracks its
write and reclaims a known superseded derivative. Video continuation finalization
reclaims a known staged result when its completion fence loses or publication
fails. Successful video finalization avoids an extra orphan inspection read.

This does **not** inventory storage or delete unrecognized objects. Hard process
crashes between put and receipt registration, failed compensating deletes, or DB
outages can still leave orphan bytes. There is no durable orphan queue in this
release. A safe inventory interface would page opaque object descriptors with
stable identity, creation time and ownership namespace. A future sweep also needs
a durable preallocated write journal, grace period, live-write exclusions and DB
reconciliation before deletion. Merely implementing listing is not permission to
sweep a shared/external bucket. Existing unknown objects remain untouched.

## Storage routing and IG#0007 foundation

`ArtifactStore.delete(locator)` is idempotent. Local deletion walks directories
relative to trusted descriptors without following symlinks and unlinks only a
regular file inside the configured root. Missing objects are safe. Blob deletion
uses the pinned SDK's delete operation; missing blobs are safe. Adapter deletion
errors and runner logs use safe messages/classes, never locator/SDK exception text.

`RoutingArtifactStore.providers` is the scheme registry for read/exists/delete;
`register` adds an adapter without changing generation code or Artifact identity.
Unregistered locators fail with a generic error. Writes still use the deployment
writer; this release does not add tenant-specific destination configuration or
external credentials. Existing local and Blob locators continue to route even
when the write provider changes. Future adapters can supply retention defaults
independently of the nullable tenant override mechanism.

Put/iter_bytes/exists/delete are sufficient for current lifecycle operations.
A future optional stat boundary should expose only generic byte count/checksum,
not backend metadata. Inventory should be a separate optional capability; user
storage permissions may support ordinary reads/writes without safe listing or
sweeping. IG#0007 still needs account-bound credential resolution, tenant write
routing, external locator/account design, adapter stat/delete semantics and scope
handling, and default-off metadata. No Drive-specific identity enters Artifact IDs.

A future backend transition can keep the same Artifact ID and replace its private
locator after copying. Required invariants: verify byte count and checksum before
publication; lock/fence concurrent cleanup and writes; atomically swap only the
physical locator; preserve tenant/user ownership, authorization, provenance,
created_at and lineage; explicitly migrate or invalidate/rebuild **all** preview
versions; retain the old object until the new locator commits; safely reclaim it
afterward. Do not move expired/removing artifacts or revive tombstones implicitly.
Bulk migration is not implemented.

## Verification

`tests/test_artifact_retention.py` covers policy, validation, service denial,
eligibility/UTC boundaries, active references/previews, tombstone APIs/listing,
lineage, preview deletion, dry-run/batches/retries/accounting, local containment,
Blob deletion and conservative orphan compensation. Browser coverage is in
`frontend/tests/retention-browser.test.mjs` (admin/member visibility, persistence,
enable/disable, duration and server errors).

Optional real PostgreSQL tests (random disposable schemas only):

```sh
IG_TEST_RETENTION_POSTGRES_URL=postgresql+psycopg://user@127.0.0.1:port/testdb \
  .venv/bin/python -B -m unittest tests.test_artifact_retention_postgres -v
```

These repeat lifecycle/migration tests on PostgreSQL and exercise concurrent input
admission versus cleanup fencing. The URL must explicitly target localhost; the
tests never inherit the application's `DATABASE_URL`.
