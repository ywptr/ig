# IG#0007A — Tenant Settings completion report

Branch: `feature/ig-0007a-tenant-settings`, based on merged IG#0007 commit
`7c5a531`. No commit, push, merge, deployment or production migration performed.

1. **Previous locations:** `App.tsx` showed personal `StorageSettings` under My
   storage and admin `TenantStorageSettings` plus `RetentionSettings` under Settings.
   External retention already had an API but no UI. Providers had tenant-scoped
   APIs/operator CLI, without a frontend administration page.
2. **Navigation:** Settings group contains My Settings (all authenticated users)
   and Tenant Settings (tenant admins only). Existing Generate/Jobs/Executions
   navigation remains. Browser history/back/forward is supported with native
   History API; no routing dependency is introduced.
3. **Route:** `/settings/tenant` supports direct entry/reload. `/settings/me` is
   personal settings; existing `/?storage=connected` OAuth returns still work.
   Existing Vite/frontend SPA serving and Vercel routing need no configuration change.
4. **Authorization:** Before rendering tenant controls, the page reads the existing
   server-authorized Drive capability endpoint plus tenant context. A forbidden
   response shows “Tenant admin access required” without controls. All admin APIs
   still independently return 403 for members, service callers and editor sessions,
   including delegated admin actors. The SPA shell itself follows existing serving
   behavior; this is not a new server-rendered admin endpoint.
5. **Tenant information:** Name, slug and IG ID appear in a read-only description
   list. No Nexus mapping IDs, canonical identity editors or tenancy administration
   controls. IS/Nexus remains authoritative for tenant identity, lifecycle,
   membership and platform entitlements. Existing IG admin-role checks are reused.
6. **Drive governance:** Dedicated Artifact storage section displays current
   enabled/disabled state and an independently saved availability checkbox. Copy
   explains per-user ownership/privacy, no shared destination, new-write blocking
   and continued access to existing artifacts with valid owner authorization.
   Personal connection/account details are never requested by this page.
7. **Retention:** Separate IG-managed and Google Drive-backed forms reuse actual
   effective policies and source labels. Defaults are 30 days/on and off,
   respectively. Each scope permits explicit enable/disable and 1–3650 whole days.
   Copy accurately describes preview deletion, retained history/lineage, provenance
   exclusions and reference reuse. No new policy setting is invented.
8. **Provider integration:** Read-only summary uses existing tenant-provider API:
   enablement, credential-presence flag and known configured model defaults.
   Arbitrary config and credentials are excluded from the display model. Editing
   remains in the supported API/operator helper; no credential editor or new
   generation-policy subsystem is introduced.
9. **My Settings:** Personal Drive connect/reconnect/disconnect, health, folder
   label and preference remain exclusively under Storage / Integrations on My
   Settings. An admin is also a user and can access both pages with separate scope.
10. **APIs:** Unchanged GET `/v2/tenant`; GET/PATCH tenant settings `/storage`,
    `/retention`, `/external-retention`; GET `/v2/tenant/providers`. Personal storage
    routes remain unchanged. No unified/duplicate endpoint was needed. Legacy
    authentication/admission and tenant-management APIs are not surfaced here.
11. **Files:** Inventory below. Frontend API helpers, page, routing utility and
    scoped retention form reuse replace the former loose admin view. CSS uses the
    current palette/cards and wraps on small screens. Application/backend/schema
    code outside the frontend is unchanged; backend changes are tests only.
12. **Frontend tests:** **68 unit tests passed**, including nine new settings API,
    navigation, scope, validation, server-response, privacy and denial cases.
    Six new browser scenarios cover admin/member/direct routes, read-only identity,
    governance saves, both retention forms, validation/pending/server errors,
    personal scope, provider summary, back navigation and responsive layout.
    Existing retention/storage browser fixtures were updated for the new routes,
    explicit section saves and scoped selectors. All browser/network suites were
    attempted with WebSocket enabled but are **blocked before assertions** by
    `listen EPERM 127.0.0.1`. They are not reported as passing.
13. **Backend tests:** **39 relevant tests passed**, including actual browser
    admin/member, retention, Drive governance and service/editor denial tests.
    Four new authorization contract cases cover all reused admin endpoints and
    verify no cross-tenant writes. No backend implementation changes were made,
    so a full backend rerun/migration test was not required. Tests used isolated
    databases/fake Google APIs. The existing temporary selector-cap runner was
    needed for sandbox TestClient cross-thread wakeups; application code was not
    patched for this workaround.
14. **Build/lint/checks:** Frontend build/TypeScript passed. Lint passed with only
    the existing `AuthScreen.tsx` set-state-in-effect warning. New/updated browser
    files pass syntax checks. `git diff --check` passed. React review covered
    cancellation, cleanup, labels, independent state and lack of idle polling.
15. **Migration/config:** **None.** No backend schema, environment, dependency,
    deployment configuration or production infrastructure changes.
16. **Release:** Feature branch → explicitly authorized push → Vercel Preview →
    validate admin/member direct links, mobile layout, both retention policies,
    saves and role denials → review → authorized merge to main. No production
    database migration is needed. This task stops with reviewable local changes.
17. **Limitations:** Existing APIs have no version/ETag/conflict mechanism: last
    successful save in a section wins. Sections do not overwrite one another.
    Reset-to-storage-default, provider editing and advanced generation-policy UI
    remain outside this task. Tenant-role navigation is cached for the workspace;
    server checks continue to deny access after role removal. Browser visual and
    interaction validation must be rerun outside the restricted environment.

[Admin/UI guide](tenant-settings.md) documents ownership boundaries, routes,
policy semantics, API reuse, operator provider configuration and release commands.

## Files changed

- `README.md`
- `docs/artifact-lifecycle.md`
- `docs/google-drive-storage.md`
- `docs/ig0007a-report.md`
- `docs/tenant-settings.md`
- `frontend/src/App.tsx`
- `frontend/src/api/tenantSettings.ts`
- `frontend/src/components/RetentionSettings.tsx`
- `frontend/src/components/StorageSettings.tsx`
- `frontend/src/components/TenantSettings.tsx`
- `frontend/src/index.css`
- `frontend/src/lib/settingsNavigation.ts`
- `frontend/tests/retention-browser.test.mjs`
- `frontend/tests/storage-browser.test.mjs`
- `frontend/tests/tenant-settings-browser.test.mjs`
- `frontend/tests/tenant-settings.test.mjs`
- `tests/test_editor.py`
- `tests/test_tenant_settings_access.py`
