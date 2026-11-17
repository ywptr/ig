# IG#0007 implementation report — per-user Google Drive Artifact storage

Implemented on `feature/ig-0007-google-drive-storage`; no commit, push, merge,
production migration, deployment, or real Google-account operation was performed.
The earlier documentation-only amendment is superseded by this implementation.

1. **Tenant capability:** `TenantSettings.google_drive_enabled`, default false.
   Admin-only GET/PATCH `/v2/tenant/settings/storage` exposes only the boolean.
   Capability is admission/write governance, never a shared destination.
2. **User connection:** `DriveConnection` belongs to `User.user_id`, with immutable
   connection identity, account permission ID/safe profile, encrypted refresh
   credential, dedicated folder ID, status and timestamps. A partial unique index
   permits at most one active connection per user; historical connections remain
   so locators cannot switch accounts.
3. **Identity separation:** Separate OAuth client, state, PKCE and credentials.
   No IS/OIDC/Google-login token or email identity linking is used. Tests connect
   users with no Google login identity.
4. **Scope:** Only `https://www.googleapis.com/auth/drive.file`; offline consent.
   Drive v3 `about.user.permissionId` identifies the connected account independently
   of login issuer/subject. No broad Drive or login scope is requested.
5. **Encryption/security:** Existing Fernet infrastructure and
   `TENANT_SECRET_ENCRYPTION_KEY` encrypt refresh credentials and transient PKCE
   verifiers. State hashes bind user, tenant, session, intent and browser nonce;
   atomic claim prevents replay. Callback query parameters are redacted from
   application/access logging while preserving Uvicorn formatter arguments.
6. **Preference:** User `artifact_storage_preference` defaults to `ig_managed`.
   `google_drive` requires tenant capability and the user's healthy connection.
   Owner-only browser settings never expose tokens, private locators or file IDs.
7. **Owner-aware routing:** New `select_write_store` sits above the unchanged
   ArtifactStore protocol. Image persistence, uploads and video continuation pass
   explicit owner context; the selected bound adapter rechecks write governance.
   It never infers current user from request-global state.
8. **Sandcastle delegation:** Existing signed actor resolution and job ownership
   are preserved. Tests execute real service submissions for two delegated actors,
   including forged irrelevant user claims and idempotent redelivery. The human
   actor owns the destination; Sandcastle remains storage-neutral and unchanged.
9. **Adapter:** `GoogleDriveArtifactStore` and a small Drive v3 transport support
   put, streamed read, exists/stat and idempotent delete. Private locators contain
   connection UUID/file ID; the registry resolves saved connection credentials.
   Folder identity comes from the saved ID, never a shared display-name lookup.
10. **Upload/resume:** Resumable 8 MiB chunks, preallocated ID, bounded transient
    retries/backoff and acknowledgement probes reuse one file/session. Uncertain
    final completion is checked by size/checksum. Upload URL validation prevents
    credential redirection. The existing provider byte-payload interface remains;
    memoryview avoids a second full-file buffer. Cross-process resume journals
    and completely streamed provider-to-storage ingestion remain deferred.
11. **Previews:** IG's deterministic WebP/lease/version lifecycle remains in use.
    Derivatives follow the original backend/connection even when preference
    changes. Drive thumbnail URLs are not used; preview APIs remain unchanged.
12. **IG-managed retention:** Local and Vercel generated-media defaults are now
    enabled at **30 days**. Tests cover age boundaries and cleanup batching.
13. **Drive retention:** Disabled by default. New nullable
    `external_generated_retention_enabled/days` tenant overrides are separate from
    existing IG-managed override columns. Admin external-retention API uses the
    existing strict payload/validation. No per-user override is introduced.
14. **Existing artifacts:** IDs/locators/ownership/provenance/lineage are unchanged.
    Cleanup uses actual backend, so old Vercel/local content keeps its policy after
    anyone connects Drive. Uploaded provenance remains excluded. No automatic
    migration or content deletion occurs on preference changes.
15. **Disable/disconnect:** Tenant disable blocks connect, selection and new writes,
    including queued jobs and new preview writes; existing originals/ready previews
    remain readable with valid authorization. Disconnect clears only that user's
    credential and pending OAuth intents. A selected Drive preference stays invalid
    until explicit reconnect or IG-managed selection. No silent fallback/copy.
16. **Health:** Cached healthy/revoked/permission_lost/folder_unavailable/unknown_error
    status, explicit health endpoint, and safe write-failure updates. No Google
    requests on settings/artifact listing. Disconnected records remain; inaccessible
    physical content fails explicitly rather than borrowing credentials.
17. **Tenant UI:** Settings contains capability governance only, separate from
    existing IG-managed retention controls. No other user's connection details.
18. **User UI:** My storage is available to members/admins and shows account,
    health, dedicated folder label, connect/reconnect/disconnect and preference.
    Disabled/unavailable Drive explains why new writes cannot proceed. No polling.
19. **Migration:** `1a0007000007`, parent `1a0010000010`. Additive columns/tables/
    indexes; existing explicit policies remain unchanged. SQLite round-trip and
    disposable PostgreSQL 17 single-user DDL round-trip/data-preservation assertions
    passed. The regular socket-based PostgreSQL tests are provided but blocked
    here by socket permissions. Capability must remain off during mixed-version
    deployment; old code cannot serve Drive locators or distinguish external cleanup.
20. **Files:** Full inventory below. No queue/provider-generation dependencies were
    upgraded; Google Photos and other applications were not changed.
21. **Validation:** See the final validation record below. Fake Google API/account
    tests cover ownership, OAuth state/PKCE, encryption/privacy, browser and service
    generation, video/preview routing, retry/idempotency, retention, governance,
    disable/disconnect, health and failures. No paid provider generation was used.
22. **Optional development test:** [Development guide](google-drive-storage.md)
    documents Google Cloud consent/client setup, exact redirect, dedicated-account
    connection, upload/generation choice, physical Drive verification, IG APIs and
    switching back. `scripts/test-drive-development.py` uploads a small PNG and
    verifies IG content/preview using an explicitly provided development session;
    its help was checked. It was not run against a real account.
23. **Rollout:** Feature → tests → authorized push → Vercel Preview with isolated
    migrated database → validate → review migration compatibility → authorized
    production migration/verify head → merge → production deployment. Additions
    permit expand/migrate before deploy; keep capability disabled until all readers,
    workers and cleanup runners are upgraded. Configure exact OAuth redirect and
    private application credentials first. Do not roll back to Drive-unaware code
    after Drive artifacts exist. Also redact callback query strings in edge logs.
24. **Limitations:** Browser/network test execution is blocked by sandbox localhost
    binding; Chromium also lacks `libatk-1.0.so.0`. Full networked PostgreSQL locking
    tests need an unrestricted disposable local DB. No folder picker, external
    retention UI, aggregate admin metrics, inventory/bulk migration, persisted upload
    journal or Photos integration. Deleted/inaccessible folders require operator/user
    recovery before reconnect can reuse them. An in-flight remote write may finish
    during concurrent disable/disconnect. Crash-orphan limitations from IG#0006
    remain; unreachable credentials can prevent later cleanup. Callback must share
    the initiating tenant/session context; arbitrary cross-domain callback handoff
    is not implemented.

## Final validation record

- Focused Drive tests: **24 passed**; combined Drive/retention run: **56 passed**.
- Full backend: **580 run, OK (52 skipped)**, including service/delegation, editor
  and integration-client regressions. Final checks use the diagnostic runner below.
- Frontend unit suites run directly: **59 passed**. Browser/network suites were
  explicitly attempted with WebSocket enabled and failed before assertions at
  `listen EPERM 127.0.0.1`; they are **blocked**, not passed. The normal Node test
  subprocess runner hid inner test detail in this sandbox, so direct-file results
  are used instead of treating its file-level successes as browser validation.
- Build/TypeScript passed; lint passed with the existing AuthScreen
  `react(set-state-in-effect)` warning only. React guidance was applied to the new
  settings UI (request cancellation, labels, no idle polling, no shared user state).
- SQLite migration round-trip passed. PostgreSQL 17 standalone backend executed
  emitted upgrade/downgrade DDL with existing user/policy preservation assertions
  and no SQL errors. Socket-based startup failed with `Operation not permitted`.
- Backend TestClient startup hangs under the sandbox's suppressed cross-thread
  event-loop wakeups. Tests ran through `/tmp/ig0007-run-tests.py`, which caps
  selector sleeps at 1 ms; it does not patch application behavior and is not shipped.
- Development smoke help and `git diff --check` passed. No real account tested.

## Files changed

- `.env.example`
- `README.md`
- `alembic/versions/1a0007000007_user_drive_storage.py`
- `app/api/tenant_settings.py`
- `app/api/user_storage.py`
- `app/artifacts/previews.py`
- `app/artifacts/retention.py`
- `app/artifacts/service.py`
- `app/artifacts/storage/google_drive.py`
- `app/artifacts/storage/local.py`
- `app/artifacts/storage/provider.py`
- `app/artifacts/storage/selection.py`
- `app/artifacts/storage/vercel_blob.py`
- `app/core/logging.py`
- `app/db/models/__init__.py`
- `app/db/models/drive_connection.py`
- `app/db/models/tenant_settings.py`
- `app/db/models/user.py`
- `app/images/tasks.py`
- `app/jobs/continuation.py`
- `app/main.py`
- `app/storage_integrations/__init__.py`
- `app/storage_integrations/connections.py`
- `app/storage_integrations/google_drive.py`
- `docs/artifact-lifecycle.md`
- `docs/artifact-storage.md`
- `docs/google-drive-storage.md`
- `docs/ig0006-report.md`
- `docs/ig0007-report.md`
- `frontend/src/App.tsx`
- `frontend/src/components/RetentionSettings.tsx`
- `frontend/src/components/StorageSettings.tsx`
- `frontend/tests/retention-browser.test.mjs`
- `frontend/tests/storage-browser.test.mjs`
- `scripts/test-drive-development.py`
- `tests/test_artifact_retention.py`
- `tests/test_google_drive_postgres.py`
- `tests/test_google_drive_storage.py`
- `tests/test_job_cancellation.py`
- `tests/test_video_resiliency.py`
