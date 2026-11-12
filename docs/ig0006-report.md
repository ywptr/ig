# IG#0006 completion report

1. **Lifecycle model:** Active → eligible (computed policy) → removing (durable fence) → removed. Artifact rows remain as tombstones; metadata identity is separate from physical content.
2. **Default policy:** Generated media in local/Vercel storage defaults ON at 28 days. Uploaded references, uploaded external generations, and unknown provenance are excluded.
3. **Tenant settings:** Nullable `generated_artifact_retention_enabled` and `generated_artifact_retention_days` distinguish inherited defaults from explicit tenant choices. Days must be 1–3650.
4. **Admin API:** GET/PATCH `/v2/tenant/settings/retention`. PATCH requires both strict typed values and returns effective policy/source.
5. **Admin UI:** Settings navigation for admins, retention checkbox/duration, save confirmation, client/server errors and explicit upload/external-storage wording.
6. **Authorization:** Existing tenant-admin membership checks plus defensive service denial. Signed Sandcastle service requests remain forbidden even with a delegated admin actor. Artifact ownership checks are preserved before expiry information.
7. **Eligibility:** Generated provenance, effective retention enabled, strict UTC age threshold, no prior removal, no active input job and no live preview claim. Tenant/original locks serialize input admission and cleanup. PostgreSQL concurrency includes FK lock compatibility.
8. **Cleanup runner:** Bounded candidate scan and processing, dry-run, per-object acknowledgements, safe summaries, failure isolation and resumable idempotent removal. Failed claims retry after 15 minutes.
9. **Scheduling:** Repository config adds daily 03:00 UTC Vercel Cron with an operator-only bearer `CRON_SECRET` endpoint, capped at 50 removals/invocation. Manual CLI supports local scheduling. Nothing depends on browser visits.
10. **Original deletion:** An irreversible logical fence commits first; physical original deletion is acknowledged after all preview objects. Already-missing objects are safe. Metadata and original private locator remain internal.
11. **Preview deletion:** All historical versions are removed, rows become `deleted` with NULL locators, and preview generation/publication cannot cross the original fence.
12. **Lineage:** Artifact/input/producer/job/execution records are retained. Downstream media stays valid; no cascade deletion occurs.
13. **Expired APIs:** Metadata/listing remains 200 with content availability/status/reason/removal time. Content and preview return 410 `ARTIFACT_CONTENT_REMOVED`; legacy Artifact-backed image content does too. Preview metadata has no URL.
14. **Storage abstraction:** Idempotent delete added to protocol/local/Blob/router. Local deletion preserves descriptor-relative containment and rejects symlinks. Blob SDK errors are sanitized. Current put/iter/exists/delete suffice; generic stat/inventory remain future optional capabilities.
15. **Routing:** A scheme registry owns read/exists/delete and allows adapter registration. Write selection remains deployment-global; historical locators keep routing after a writer change.
16. **External defaults:** Providers recommend retention metadata; unknown/user-owned providers default off. Cleanup uses each original's backend default; admin reads use current writer default. Explicit tenant overrides survive changes.
17. **Orphans:** Known failed image/upload/preview publications and losing staged video completions receive conservative compensation. DB confirmation protects published objects. Hard crashes, ambiguous DB outages and failed compensation can still leak objects; no unsafe bucket sweep or durable orphan journal is claimed.
18. **Schema/migration:** `1a0006000006` follows `1a0003000003`; single Alembic head. Adds nullable overrides, cleanup/removal timestamps/reason and indexes. SQLite/PostgreSQL round-trips preserve rows. No production migration was run.
19. **Files changed:** Complete inventory below.
20. **Verification:** Full backend: 421 tests, OK (24 skipped). Explicit disposable PostgreSQL: 20 tests, OK; additional final graph/migration round-trip: OK. Full frontend: 60 passed, including Chromium admin/member flows. Build/TypeScript, lint and `git diff --check` pass. Lint retains the pre-existing AuthScreen set-state-in-effect warning. Local storage and mocked pinned Vercel Blob adapter tests are included; no live Blob deletion was performed.
21. **Production rollout:** Inspect old generated content with dry-run and set explicit exceptions; migrate before serving new code; configure production `CRON_SECRET`; deploy/enable cron; verify secret protection and monitor failures/backlog. Existing NULL-policy tenants inherit the new default too. Keep invocations below the 15-minute retry lease. No production infrastructure, secrets or content were changed.
22. **IG#0007 foundation:** Stable Artifact IDs, opaque registry-routed locators, provider defaults separate from tenant policy, deletion/tombstones, preview coordination, input admission fences, and documented copy/verify/atomic-swap migration invariants. Drive still needs account-bound credential resolution, tenant destination configuration, adapter/scopes and stat/inventory design. No Drive/Photos adapter or video resiliency work was implemented.
23. **Remaining concerns:** No general orphan inventory or durable write journal; bounded daily capacity needs backlog monitoring; byte counts estimate recorded logical sizes rather than billing; SQLite is single-worker development only; backend migration/preview movement is specified but not implemented. Disabling retention does not undo already committed removal decisions.

See [Artifact lifecycle operations and design](artifact-lifecycle.md) for the audit, exact semantics, CLI examples and rollout instructions.

## Changed files

- [README.md](../README.md)
- [alembic/versions/1a0006000006_artifact_retention.py](../alembic/versions/1a0006000006_artifact_retention.py)
- [app/api/artifact_cleanup.py](../app/api/artifact_cleanup.py)
- [app/api/artifacts.py](../app/api/artifacts.py)
- [app/api/images.py](../app/api/images.py)
- [app/api/tenant_settings.py](../app/api/tenant_settings.py)
- [app/artifacts/orphans.py](../app/artifacts/orphans.py)
- [app/artifacts/previews.py](../app/artifacts/previews.py)
- [app/artifacts/references.py](../app/artifacts/references.py)
- [app/artifacts/retention.py](../app/artifacts/retention.py)
- [app/artifacts/service.py](../app/artifacts/service.py)
- [app/artifacts/storage/base.py](../app/artifacts/storage/base.py)
- [app/artifacts/storage/local.py](../app/artifacts/storage/local.py)
- [app/artifacts/storage/router.py](../app/artifacts/storage/router.py)
- [app/artifacts/storage/vercel_blob.py](../app/artifacts/storage/vercel_blob.py)
- [app/db/models/artifact.py](../app/db/models/artifact.py)
- [app/db/models/tenant_settings.py](../app/db/models/tenant_settings.py)
- [app/images/tasks.py](../app/images/tasks.py)
- [app/jobs/continuation.py](../app/jobs/continuation.py)
- [app/jobs/service.py](../app/jobs/service.py)
- [app/main.py](../app/main.py)
- [app/tenancy/dependencies.py](../app/tenancy/dependencies.py)
- [docs/artifact-lifecycle.md](../docs/artifact-lifecycle.md)
- [docs/artifact-previews.md](../docs/artifact-previews.md)
- [docs/artifact-storage.md](../docs/artifact-storage.md)
- [docs/ig0006-report.md](../docs/ig0006-report.md)
- [frontend/src/App.tsx](../frontend/src/App.tsx)
- [frontend/src/components/RetentionSettings.tsx](../frontend/src/components/RetentionSettings.tsx)
- [frontend/src/types/media.ts](../frontend/src/types/media.ts)
- [frontend/tests/retention-browser.test.mjs](../frontend/tests/retention-browser.test.mjs)
- [scripts/cleanup-artifacts.py](../scripts/cleanup-artifacts.py)
- [tests/test_artifact_library.py](../tests/test_artifact_library.py)
- [tests/test_artifact_retention.py](../tests/test_artifact_retention.py)
- [tests/test_artifact_retention_postgres.py](../tests/test_artifact_retention_postgres.py)
- [tests/test_artifact_storage.py](../tests/test_artifact_storage.py)
- [vercel.json](../vercel.json)
