# IG#0007B completion report

Implemented on `feature/ig-0007b-multimodel-governance`. No commit, push, deployment,
production migration or paid provider call was performed.

1. **Previous architecture:** adapters already declared model metadata; tenant JSON
   stored defaults, with no allowlist. Unknown legacy image defaults could bypass
   capability validation. Frontend production code already had no fixed model IDs.
2. **Global catalog:** provider adapters remain authoritative. OpenAI now has an
   explicit immutable registry in `app/media/providers/openai/models.py`.
3. **Tenant governance:** existing encrypted provider-config rows and `config_json`
   own provider enablement, optional model allowlists and media-specific defaults.
4. **OpenAI models:** `gpt-image-2`, `gpt-image-2.5-flare`,
   `gpt-image-2.5-sunburst`, `gpt-image-2.6-flare`, `gpt-image-2.6-sunburst`.
   Official model pages and API references are linked in the catalog guide.
5. **Capabilities:** each declares synchronous text/image generation/editing, one
   PNG output, up to eight references (32 MiB each), 32,000 prompt characters,
   single/four-panel layouts, semantic styles and reference roles/strengths.
   All expose the reviewed IG subset: `2:3`, `4:5`, `1:1`, `16:9` and
   draft/standard/high. See the guide for exact upstream versus IG limits.
6. **Translation:** explicit per-definition size and quality mappings; all map
   draft/standard/high to low/medium/high. Background auto/opaque/transparent is
   supported; only 2.6 accepts complexity low/medium/high. The pinned SDK uses
   `extra_body` for complexity, verified with mocked JSON and multipart requests.
   New upstream xhigh/max quality levels are recorded but not advertised as IG profiles.
7. **Legacy bypass:** unknown/default/explicit models must resolve through the
   registered catalog; references cannot fall back to another model's capabilities.
8. **Enabled models:** absent restriction allows the registered catalog; an empty
   list disables that media type. Unknown IDs, duplicates and malformed policies
   are rejected by configuration writes. Disabled models cannot route explicitly/Auto.
9. **Defaults:** explicit `default_models.<media_type>` wins; image legacy
   `default_model` remains supported. Defaults must be registered, current and enabled.
10. **Auto routing:** preserve OpenAI-first general provider preference, then provider
    registration order. Without an explicit tenant model default, prefer an eligible
    adapter default, then catalog order. Purpose routing uses eligible tagged models.
    No runtime failure triggers a replacement provider/model.
11. **Discovery:** adds known/supported/enabled/lifecycle and safe provider configuration
    errors; availability/usability reflects tenant policy. No remote health call or secret.
12. **Frontend:** choices remain entirely discovery-driven; no model/provider ID
    constants added to production React. Generic deprecated labels and eligibility filters.
13. **Tenant Settings:** dynamic allowlist/default forms for configured providers/media,
    independent saves, server-authoritative results, inline errors and lifecycle warnings.
    Missing credentials disable controls; user storage and tenant governance stay separate.
14. **CLI/API:** existing configure-provider helper validates governance and prints only
    safe catalog summaries. Additive admin GET `/v2/tenant/model-governance` and PATCH
    `/v2/tenant/model-governance/{provider}` expose no credentials/raw configuration.
    Updates merge under a row lock and preserve unrelated config/credentials.
15. **Lifecycle:** optional deprecated/sunset/replacement metadata is generic. GPT Image 2
    is deprecated but retained as the implicit compatibility default; no silent policy
    migration. GPT Image 1.5 is not newly registered. Sunset models cannot receive new work.
16. **Job stability:** selected model remains pinned in Job/Execution/Artifact metadata;
    worker eligibility checks do not resolve changed defaults. Disabled/unknown queued
    selections fail clearly. Idempotency preserves the original selection.
17. **Schema:** no migration, dependency, environment or deployment configuration change.
    Alembic head remains `1a0007000007`.
18. **Files:** inventory below; current guides updated rather than rewriting old reports.
19. **Validation:** full backend: 599 tests, OK, 52 skipped. Focused final registry/admin
    and editor authorization checks: 16 passed. Frontend unit suites: 73 passed.
    Build/typecheck passed. Lint passed with the pre-existing AuthScreen effect warning.
    `git diff --check` passed. Browser suites were attempted but this environment rejects
    localhost binding (`listen EPERM 127.0.0.1`), before assertions. Browser validation
    remains required on a capable host. Full backend includes provider, capability,
    routing, integration-client, service/delegation, editor and storage regressions.
    Sandbox TestClient tests used a temporary selector-wakeup harness; no repository
    test infrastructure was altered for that workaround.
20. **Optional live smoke:** operator-only instructions in the catalog guide: discover
    each tenant-enabled model, submit one draft square image, inspect the recorded
    Job/Execution model and Artifact result. Account-specific access may differ.
21. **Rollout:** explicitly authorize push → Vercel Preview → browser/API validation →
    review → authorized merge → production deployment. No DB migration. Upgrade all
    producers/workers before relying on governance; old versions do not enforce it.
22. **Limits:** local eligibility cannot prove upstream credentials/quota/access. IG
    intentionally exposes a tested subset of upstream settings, not arbitrary sizes,
    all native quality levels or extra formats. No remote model catalog, automatic
    default migration, health ranking or post-failure fallback. No ETag/version conflicts;
    last successful save wins within the same media policy. Credential/provider-enable
    provisioning remains operator/API managed. Browser validation remains outstanding.

## Changed files

- `README.md`
- `app/api/media.py`
- `app/api/model_governance.py`
- `app/api/tenant_providers.py`
- `app/artifacts/references.py`
- `app/images/tasks.py`
- `app/main.py`
- `app/media/capabilities.py`
- `app/media/capability_schemas.py`
- `app/media/model_governance.py`
- `app/media/providers/base.py`
- `app/media/providers/google/image.py`
- `app/media/providers/openai/image.py`
- `app/media/providers/openai/models.py`
- `app/media/providers/video.py`
- `app/media/routing.py`
- `app/media/validation.py`
- `app/media/video_service.py`
- `app/tenancy/provider_configs.py`
- `docs/ig0007b-report.md`
- `docs/media-capabilities.md`
- `docs/media-routing.md`
- `docs/openai-model-catalog.md`
- `docs/tenant-settings.md`
- `frontend/src/api/tenantSettings.ts`
- `frontend/src/components/GenerationLab.tsx`
- `frontend/src/components/ProviderModelSettings.tsx`
- `frontend/src/components/TenantSettings.tsx`
- `frontend/src/lib/generation.ts`
- `frontend/src/types/media.ts`
- `frontend/tests/model-governance-browser.test.mjs`
- `frontend/tests/model-governance.test.mjs`
- `frontend/tests/retention-browser.test.mjs`
- `frontend/tests/storage-browser.test.mjs`
- `frontend/tests/tenant-settings-browser.test.mjs`
- `frontend/tests/tenant-settings.test.mjs`
- `scripts/configure-tenant-provider.py`
- `tests/test_editor.py`
- `tests/test_google_image.py`
- `tests/test_google_image_generation.py`
- `tests/test_media_capabilities.py`
- `tests/test_media_routing.py`
- `tests/test_model_governance.py`
- `tests/test_video_generation.py`
