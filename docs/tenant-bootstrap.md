# Tenant bootstrap and first administrator

Use `scripts/bootstrap-tenant.py` after migrations. Provider credentials are a
separate operation. This command makes no IS or Nexus requests and changes no
infrastructure. All examples use the canonical development container.

## Fresh development sequence

1. Create the local `ig_dev` database/role and configure Docker as described in
   [local development](local-development.md). Verify the masked target using
   [database operations](database-operations.md).
2. Apply migrations:

   ```sh
   docker compose -f compose.yaml -f compose.dev.yaml exec ig alembic upgrade head
   ```

3. Bootstrap the migration-seeded tenant. Migrations name this tenant **Default**;
   the name must match. Specify the hostname of the registered IS callback as well
   as any other browser entry points. For the current development origin:

   ```sh
   docker compose -f compose.yaml -f compose.dev.yaml exec ig \
     python scripts/bootstrap-tenant.py \
     --slug default --name Default \
     --domain localhost --domain 5480-deb13.lan.com \
     --allow-self-registration
   ```

   A new, unseeded slug may instead use `--name "IG Development"`. Slug, name,
   and at least one domain are required for setup; repeat `--domain` for aliases.
   Domain values are hostnames/IP addresses without scheme, path, or port.
   Case, surrounding whitespace, and terminal dots are normalized exactly as in
   tenant resolution. The browser's Host hostname (port excluded) selects the
   tenant; Vite preserves Host. `localhost` alone does not cover a LAN callback.

4. Configure the IS confidential development client and the four API environment
   variables described in [IS/OIDC development](is-oidc-development.md). Recreate
   the API after environment changes. Preserve HTTPS, secure cookies and PKCE.
5. The intended administrator logs in through IS from the registered HTTPS origin.
   JIT creates a local user, exact issuer/subject identity mapping, and **member**
   membership. Other admitted users also remain members, regardless of login order.
6. Obtain the intended user's exact subject and discovery issuer from trusted IS
   administration/verified identity information. Do not substitute an email, local
   user UUID, token, or client ID. Run:

   ```sh
   docker compose -f compose.yaml -f compose.dev.yaml exec ig \
     python scripts/bootstrap-tenant.py --slug default --promote-admin \
     --is-issuer '<exact-discovery-issuer>' --is-subject '<verified-user-subject>'
   ```

   This is privileged local administration. It requires a previously authenticated
   identity and an existing active membership in this tenant. Unknown identities,
   missing/inactive memberships and inactive tenants fail. It does not reactivate,
   admit, link by email, or create a user. Rerunning promotion creates no records.
   Tenant admins can subsequently manage members through existing APIs.
7. If admission should be restricted after setup, rerun setup with
   `--no-allow-self-registration`, or use the existing admin settings API. Explicit
   admission enables JIT for **all valid IS identities**, not just the administrator.
   It never grants an admin role. Legacy password/Google settings are preserved;
   configure the desired IS-only policy through the existing admin settings API.
8. Configure tenant providers separately:

   ```sh
   docker compose -f compose.yaml -f compose.dev.yaml exec ig \
     python scripts/configure-tenant-provider.py --tenant-slug default --provider openai
   ```

   Credentials use the hidden prompt and tenant encryption service. Then use IG.

## Reruns, conflicts, and transactions

Setup creates a UUID-backed active Tenant when the slug is absent, missing
TenantDomain rows, and missing TenantSettings. The first new domain becomes
primary only when the tenant has no primary; existing domains/primary flags remain
unchanged. Nexus mapping remains null for new tenants and unchanged for existing
ones. No additional metadata is required in the current schema.

Schema-owned ORM defaults apply: active tenant; self-registration false; legacy
password and Google flags true. Existing settings are preserved. Only an explicitly
supplied `--allow-self-registration` / `--no-allow-self-registration` changes
admission. Omitting the option never resets it. Setup does not rename tenants.
Same slug/name/domains/settings reports `already configured`; adding missing rows
or explicitly changing admission reports `updated`; new tenants report `created`.

A slug with a different name or inactive status fails. A hostname owned by another
tenant fails without reassignment. Existing local IDs and Nexus mappings remain
intact. Each command uses one transaction: any failure rolls back its changes.
Existing tenant rows are locked on PostgreSQL to serialize bootstrap/promotion;
unique slug, hostname, settings and membership constraints prevent duplicates.
Concurrent creation of a new slug/domain can cause one command to fail with a
sanitized database conflict; rerun it to converge or receive the ownership conflict.
There is no automatic retry or partial success. Ordinary IS JIT behavior is unchanged.

`--dry-run` performs the operation and rolls back, reporting the projected result.
It still requires the normal write safeguards because it executes transactional
writes/locks. Generated dry-run UUIDs are provisional. Success is printed only after
commit (or dry-run rollback). No secrets or external identity subjects are printed.

## Database safeguards

Both modes call `require_safe_admin_target` before importing the engine or
connecting. Local development targets work normally. Production metadata and
unknown remote databases require **both** `--allow-production-write` and exact
`--confirm-database-target '<verified-host[:port]/database>'`, as documented in
[database operations](database-operations.md). Do not append these to routine local
commands. Never use a production connection for tests.

## Inspection findings and boundaries

- `Tenant` requires local ID, unique slug, name; status/time have ORM defaults.
  `nexus_tenant_id` is optional and unique. Local tenants need no fabricated mapping.
- `TenantDomain` has globally unique hostname ownership. Host resolution filters
  active tenants in `get_tenant_by_hostname`; callback and tenant APIs depend on it.
- `TenantSettings` is required by `get_or_create_is_user`, auth-config and settings
  APIs. The settings API updates existing rows, not initial records.
- Historical migrations seed `default` / `Default`, its settings and admin
  memberships for users already present at migration time. On an empty installation
  there are no such users/admins, and migrations create no domains.
- IS JIT automatically creates User/UserIdentity/member after admission. It does not
  promote admins. Before this command, missing domains/settings/new tenants and
  first-admin promotion required SQL or custom administrative code.
- Pre-login admin assignment has no existing representation: membership requires a
  local user. Safe support would need explicit identity provisioning or pending
  state. Post-login promotion reuses existing verified mappings without introducing
  either. Email is profile data, not a safe authorization key; issuer + subject is
  the durable identity. Local user IDs are not required as CLI input.
- No schema migration, provider record, invitation, auth-provider record, quota,
  billing record or Nexus registration is required for this baseline. Provider
  setup is needed separately for generation. Nexus phase 1 remains legacy mode;
  authoritative Nexus mapping/reconciliation and Consumer API contracts are future
  integration work, described in [Nexus tenancy](nexus-tenancy-phase-1.md).
