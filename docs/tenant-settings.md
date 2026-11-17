# Tenant Settings — IG#0007A

Settings → **Tenant Settings** (`/settings/tenant`) consolidates IG-specific tenant
configuration. It is available only to an authenticated tenant admin. Direct links
recheck access with the existing admin API before rendering controls; hiding the
navigation is not the authorization boundary. The APIs independently reject
ordinary members, Sandcastle service principals and editor sessions, even when
the delegated human actor is a tenant admin.

**IS/Nexus remains authoritative for tenant identity, lifecycle, membership and
platform entitlements.** IG shows tenant name, slug and ID as read-only context.
This page does not create/delete tenants, edit canonical identity, administer
membership/users, assign platform admins, change application entitlements or
implement Nexus admission/global administration. Existing IG tenant-admin
checks continue to authorize IG application settings; no new authority is added.

## Sections

- **Tenant information:** name, slug and IG tenant ID, read-only. No Nexus mapping
  identifiers or identity-editing controls.
- **Artifact storage:** enable/disable Google Drive availability. Enabling lets
  individual users authorize their own private Drive account. No shared tenant
  Drive is connected and no personal account details are displayed here.
  Disabling blocks new connections/writes, including new Drive preview writes.
  Existing originals/ready previews remain readable while owner authorization is
  valid. No automatic content deletion or migration occurs.
- **IG-managed retention:** generated local/Vercel content defaults to cleanup
  enabled after **30 days**. Tenant admins can explicitly enable/disable cleanup
  and set 1–3650 whole days. The server-resolved effective policy and its source
  (storage default or tenant override) are shown.
- **Google Drive-backed retention:** generated Drive content defaults to **off**.
  This uses the existing, separate external-retention override. Enabling Drive
  capability never enables cleanup. Duration remains valid while cleanup is off.
  Both retention policies remove physical generated content and previews while
  retaining Artifact history/lineage. Uploaded/reference-upload provenance remains
  excluded. Generated content later referenced can expire after active jobs finish.
- **Providers / Generation settings:** read-only tenant enablement, credential
  presence and supported configured model defaults. The frontend renders only
  known default-model fields, not arbitrary configuration or secrets. There was
  no existing provider-admin UI to move. Provider edits/credentials remain with
  the supported `/v2/tenant/providers` admin APIs and operator helper
  `scripts/configure-tenant-provider.py`. No new generation policy is invented.

## Personal settings remain separate

Settings → **My Settings** (`/settings/me`) contains the user's Storage / Integrations
controls: connect/reconnect/disconnect their Drive, inspect their own health/folder
label and select their own storage preference. Tenant admins also use this personal
page for their own accounts. Tenant Settings only governs whether Drive is allowed.
The existing `/?storage=connected` OAuth return remains supported. Native browser
back/forward navigation and direct settings links are supported without adding a
routing dependency or changing deployment configuration.

## Saving and APIs

Each capability/retention section saves independently, shows a saving state,
disables its fields during the request, and displays success or a nearby error.
Successful responses replace draft values with the server's authoritative values.
The current saved policy is separate from an unsaved draft. Client validation
rejects empty/non-integral/out-of-range durations; server validation messages are
shown without displaying raw response bodies. No optimistic update claims a save
before the server accepts it.

Existing APIs are reused unchanged:

| Purpose | Endpoint |
| --- | --- |
| Tenant context | GET `/v2/tenant` |
| Drive capability | GET/PATCH `/v2/tenant/settings/storage` |
| IG-managed retention | GET/PATCH `/v2/tenant/settings/retention` |
| External/Drive retention | GET/PATCH `/v2/tenant/settings/external-retention` |
| Provider summary | GET `/v2/tenant/providers` |
| Personal storage | Existing `/v2/user/storage` routes, on My Settings only |

There are no new backend endpoints, configuration variables or migrations.
Existing APIs do not provide revisions, ETags or conflict detection: the last
successful save within a section wins. Reload to see other administrators' changes.
Saving one section does not rewrite the other sections. Resetting nullable
retention overrides to storage defaults is not supported by the existing API/UI;
saving creates an explicit override. No concurrency/versioning redesign is included.

## Validation and release

Frontend coverage includes navigation/deep links, separate API scopes, validation,
server-authoritative responses, provider-field allowlisting and forbidden-response
handling. Browser coverage exercises admin/member direct access, read-only context,
Drive enable/disable, both retention forms, pending/error states, personal settings
separation and back navigation. Backend contract tests exercise the unchanged
admin APIs with browser admins/members and service/editor delegated admin actors.

```sh
node --experimental-websocket frontend/tests/tenant-settings.test.mjs
node --experimental-websocket frontend/tests/tenant-settings-browser.test.mjs
node --test --experimental-websocket frontend/tests/*.test.mjs
npm --prefix frontend run build
npm --prefix frontend run lint
.venv/bin/python -B -m unittest tests.test_tenant_settings_access \
  tests.test_editor.EditorTests.test_editor_sessions_cannot_administer_tenant_settings_even_for_admin_actor -v
git diff --check
```

See [the completion report](ig0007a-report.md) for actual results and environment
limitations. This task changes frontend implementation, tests and documentation
only; no backend implementation or schema change is required.

Release: feature branch → explicitly authorized push → Vercel Preview → validate
admin/member routes, mobile layout, section saves and authorization → review →
merge to main when authorized. **No production database migration is needed.**
