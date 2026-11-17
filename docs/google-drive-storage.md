# Per-user Google Drive Artifact storage — IG#0007

Drive is a user's storage authorization, independent of IG/IS authentication.
A tenant enables the capability; each user connects their own account, dedicated
folder and storage preference. No shared tenant destination exists. Google Photos
is not implemented; a future import source/export destination is a separate concept.

## Setup and development account check (operator-run only)

1. Create a dedicated development Google Cloud project, enable Google Drive API v3,
   configure the OAuth consent screen and add the dedicated account as a test user.
   Create a Web application OAuth client. Register the exact callback URL, for
   example `http://localhost:8000/v2/user/storage/google-drive/callback` for local
   development or `https://ig-dev.example.com/v2/user/storage/google-drive/callback`.
   The callback must resolve the same tenant and browser IG session as connect.
2. Configure `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET` and
   `GOOGLE_DRIVE_REDIRECT_URI` in the development server's private environment.
   Keep the existing `TENANT_SECRET_ENCRYPTION_KEY` stable and private. These are
   OAuth application credentials, not a global user's Drive token. Never copy IS
   or deprecated Google-login tokens into Drive settings. OAuth testing-mode
   refresh credentials can expire; use explicit health checks/reconnect as needed.
3. Apply migration `1a0007000007` to a disposable development database. Sign in to
   IG with any supported login identity. A tenant admin enables Google Drive under
   Settings. Under My storage, connect the dedicated Drive account and consent to
   `https://www.googleapis.com/auth/drive.file`. No full-Drive or login scope is
   requested. The app creates an `IG Artifacts` folder and persists its ID.
4. Select My Google Drive. Upload a PNG/JPEG/WebP reference in the Generation Lab.
   Verify the original and IG-generated WebP preview in that account's folder and
   verify IG content/preview APIs by Artifact ID. Provider generation is optional
   and must be an explicit operator choice; the smoke script never generates.
5. Optionally run the script below, supplying your development browser's IG session
   cookie privately in `IG_DRIVE_DEV_SESSION`. Do not put its value in command-line
   arguments, logs, files or Git. The script uploads a small generated PNG, verifies
   IG content/preview, retains the test artifact, and switches to IG managed in a
   `finally` block. If that final request fails, switch back manually in My storage.

   ```sh
   .venv/bin/python -B scripts/test-drive-development.py --base-url http://localhost:8000
   ```

6. Switch back to IG managed. New uploads should stay IG managed; old Drive content
   should still read through IG. Disable tenant Drive: new Drive writes/connects
   fail, existing originals and ready previews remain readable while credentials
   work. Disconnect/revoke the account to observe the safe unavailable response.
   Use a second user/account to verify that folders, settings and writes stay isolated.

No real-account test was run automatically.

## API and identity

Browser-only routes under `/v2/user/storage` expose the caller's preference and
safe account/health information. They never accept authoritative user/connection
IDs or expose tokens, folder/file IDs or locators. Mutations reject cross-origin
requests. Service/editor principals cannot administer integrations.

- GET/PATCH `/v2/user/storage`: inspect/change preference.
- POST `/v2/user/storage/google-drive/connect`: begin independent offline consent.
- GET `/v2/user/storage/google-drive/callback`: validate and consume bound OAuth state.
- POST `/v2/user/storage/google-drive/health`: explicitly refresh/check folder access.
- POST `/v2/user/storage/google-drive/disconnect`: remove only this user's usable credential.
- Admin GET/PATCH `/v2/tenant/settings/storage`: capability boolean only.

OAuth uses PKCE S256 and a ten-minute, single-use server state record bound to
user, tenant, session digest, intent and an HttpOnly SameSite browser nonce cookie.
The verifier and refresh credential use the existing Fernet secret infrastructure.
The callback rechecks tenant membership/capability before publishing the connection.
A pending exchange cannot complete after disconnect cancels its intent. The Drive
account is identified by Drive `permissionId`, independently of email/IS subject.
Application/access logging redacts callback query parameters; deployment-edge access
logs must also be configured to exclude OAuth callback query strings.

Only one connection may be active per user, enforced by a partial unique index.
Reconnect to the same account reuses that user's connection and folder. Choosing
a different account deactivates/clears the previous credential and allocates a
new connection identity. Old locators never borrow credentials from a new account.
Disconnect clears only the caller's credential and pending intents. It leaves
Drive preference selected but invalid, requiring reconnect or explicit selection
of IG managed. It neither revokes other users nor copies/deletes files.

## Storage, previews and failures

`select_write_store` takes explicit tenant and owner IDs above `ArtifactStore`.
Image persistence uses ImageRequest ownership, video continuation uses its verified
claim, and uploads use the authenticated owner. Sandcastle delegation continues
resolving signed actor issuer+subject to an IG user; service identity never selects
a destination. Artifact APIs and the integration client remain storage-neutral.

New Drive writes require active tenant/membership, capability, selected connection
ownership and cached healthy status. The bound writer rechecks that gate before
refreshing its separate credential and writing. Authorization, folder and upload
errors fail explicitly; there is no Vercel fallback. An already in-flight remote
write may finish if governance changes during the call; the system does not hold
a database lock across the upload.

Private locators are `gdrive://<connection UUID>/<Drive file ID>`. Reads/deletes use
that saved connection, independent of current preference or tenant write capability.
Existing local/Vercel locators remain unchanged. Derivatives follow the original's
backend/connection even after preference changes. IG's deterministic WebP generation,
preview leases, versions, API and deletion coordination remain in use; Drive
thumbnail URLs are not used. Tenant disablement also blocks new Drive preview
writes; already stored previews remain readable.

Drive v3 writes use resumable 8 MiB chunks and a preallocated file ID. Uncertain
chunk acknowledgements probe/resume the same session, with at most three transient
failures and bounded backoff. The final uncertain result is verified using byte
count and checksum. Server upload-session locations are restricted to Google's
upload endpoint and redirects are disabled. Downloads stream and delete accepts
missing files. `appProperties` contain schema/kind hints, never authorization/PII.
The pipeline already hands adapters a byte payload; memoryview avoids another
whole-file copy, but this release does not make provider generation disk-streamed.

Health is cached: healthy, revoked, permission_lost, folder_unavailable or
unknown_error. Listing artifacts/settings does not call Google. Explicit health
checks can restore healthy state. Storage failures update safe status; missing
individual files do not mark the account unhealthy. Disconnected/revoked content
retains its Artifact records but can be unavailable until authorization is restored.

## Retention and compatibility

Generated local/Vercel artifacts default to enabled retention at **30 days**.
Generated Drive artifacts default to **disabled**. Cleanup selects policy from
actual saved backend, never the owner's current preference/capability.

Existing `generated_artifact_retention_enabled/days` tenant values retain their
IG-managed meaning and are not rewritten. New nullable
`external_generated_retention_enabled/days` fields govern Drive separately. An
explicit external override is available to tenant admins through GET/PATCH
`/v2/tenant/settings/external-retention`, using the existing retention payload
field names and duration validation (1–3650). External override controls are API-only
initially; no per-user retention override is introduced. Uploaded/reference
provenance remains excluded from generated retention. Existing removing/tombstoned
artifacts keep IG#0006's durable cleanup decisions.

Migration `1a0007000007`, after `1a0010000010`, adds preference/capability columns,
separate external retention fields, connection and OAuth-state tables/indexes.
It does not rewrite existing locators, artifacts, identities or explicit policies.
All additions are compatible with old application readers/writers; keep Drive
capability disabled until every reader/worker/cleanup deployment understands Drive.
Do not roll back to an old deployment after creating Drive locators: it cannot
serve them, and old cleanup code does not understand external policy separation.

There is no bulk migration, folder picker, general provider inventory, persisted
cross-process resumable-upload journal, or Google Photos adapter. A crash between
remote success and DB publication can leave an orphan, as in IG#0006. Job/service
idempotency and publication fences prevent duplicate logical artifacts; transport
retries reuse one file ID. Automatic cleanup cannot remove unreachable files after
credential disconnect. An interrupted OAuth connect may leave an empty IG folder.

## Verification and release

Default tests use fake Google accounts/API responses; no paid generation:

```sh
.venv/bin/python -B -m unittest tests.test_google_drive_storage -v
.venv/bin/python -B -m unittest discover -s tests -t .
IG_TEST_DRIVE_POSTGRES_URL=postgresql+psycopg://user@127.0.0.1:port/testdb \
  .venv/bin/python -B -m unittest tests.test_google_drive_postgres -v
node --test --experimental-websocket frontend/tests/*.test.mjs
npm --prefix frontend run build
npm --prefix frontend run lint
git diff --check
```

The PostgreSQL test requires an explicitly selected disposable localhost database
and creates/drops a random schema. Never use production DATABASE_URL for tests.

Release sequence: feature branch → tests → operator-authorized push → Vercel
Preview (isolated migrated preview DB) → optional development-account check →
review additive migration compatibility → authorized production migration → verify
head → merge main → Vercel Production. Keep tenant Drive disabled until all new
code is deployed, then enable for a dedicated pilot user. Configure the exact
production OAuth callback and private credentials before enabling. No production
migration, deployment, commit or push is performed by this implementation task.

Google API references: [resumable uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
and [preallocated file IDs](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/generateIds).
