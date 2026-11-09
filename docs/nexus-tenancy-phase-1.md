# Unified tenancy: Phase 1

Historical `Tenant.tenant_id` values and all resource foreign keys remain intact.
`nexus_tenant_id` is an optional unique indexed mapping. Apply the new Alembic
migration through the normal release process; this change does not populate it.
A missing mapping stays missing: lookup helpers never infer canonical identity
from a local ID. Mapping lookups do not authorize access or replace hostname routing.

`NEXUS_TENANCY_MODE=legacy` is the default and only supported mode. Other values
raise a configuration error when governance is resolved. Shadow and enforce must
not be configured yet. The authenticated tenant dependency delegates its existing
active-membership query to `LegacyTenancyGovernance`; local admin roles, resource
ownership checks, OIDC admission/JIT membership, and membership write APIs remain
unchanged. No remote Nexus calls or alternative login flow are implemented.

Run the read-only report from the repository root, with the intended database
configured and the migration applied:

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec -T ig \
  python scripts/audit-tenancy.py --is-issuer https://verified-is-issuer.example
```

The report includes mapping readiness, UUID validity, missing domains, orphaned
tenant references in all registered tenant-scoped models, duplicate identity-key
counts, and users needing identity review. It emits IDs, never credential fields
or external identity subjects. Use a database account with SELECT-only privileges
for operational audits. The script issues SELECTs and rolls back its transaction.
An active membership is only a candidate for identity review, not proof that the
user has authenticated through IS. No identity is inferred from email.

Local constraints establish mapping uniqueness; correctness against Nexus and
complete membership/entitlement reconciliation still require authoritative Nexus
data. There is no automatic correction or live identifier rewrite.

Pending Consumer API dependencies: endpoint paths, service authentication,
membership and entitlement schemas, role ownership, canonical lookup semantics,
cache/revocation rules, and error behavior. TenantContext will remain unchanged
until those requirements are known. Nexus failures in future shadow mode must
leave legacy authorization intact; future enforcement must fail closed according
to the agreed session/cache policy. Preserve local memberships for rollback.

Sandcastle service access remains a separate acceptance gate. It requires verified
service identity plus delegated issuer/subject, canonical tenant and entitlement
checks, then local IG resource authorization. Arbitrary JSON IDs and hostname
alone must never establish service authorization. No service protocol is invented
in this phase.
