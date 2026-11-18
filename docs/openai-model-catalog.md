# Multi-model catalogs and tenant governance — IG#0007B

Technical model definitions belong to provider adapters. Tenant policy belongs in
`TenantProviderConfig.config_json`. Credentials stay encrypted once per tenant/
provider; there is no credential copy per model and no database model catalog.
The frontend consumes metadata and never decides which provider/model exists.

## Audit and source-of-truth decision

Before IG#0007B, `ProviderMetadata.models` already supported many `ModelMetadata`
entries. Active catalog sources were adapters registered in `IMAGE_PROVIDERS` and
`VIDEO_PROVIDERS`. Google and Alibaba owned local technical declarations too.
No database table persisted model capabilities. The existing ORM `default_model`
column was ignored by current selection; the operative legacy setting was
`config_json.default_model`. Google/video also supported `default_models`.

There were no tenant enabled-model restrictions. Discovery marked every model
available from the provider-level flag/configuration alone. Hard-mode UI already
used discovery arrays; production `frontend/src` had **zero fixed model IDs**.
The only OpenAI name literals in runtime/tooling were the adapter's default/catalog
entry and the CLI example. Tests and documentation used literal IDs as fixtures/
examples; those remain appropriate evidence, not runtime policy.

The bypass was confirmed in `app/media/routing.py:_validate_options` (skip validation
when metadata was absent), OpenAI's permissive resolver, and
`app/artifacts/references.py:reference_slots` (borrow the first model's slots).
All three are removed. New submitted defaults and selections must be registered.
Old stored unknown defaults are marked unavailable with a safe configuration error;
new configuration writes reject them. Historical jobs with unknown recorded model
IDs fail; their identities are not rewritten.

No schema change is necessary: the existing JSON column holds policy safely.
Adding another reviewed technical model in the future requires code/catalog changes,
not a DB migration or a frontend model-name change.

## Official verification, reviewed 2026-11-17

Each requested model has its own official page confirming text/image inputs,
image output and **both Images generation and edits endpoints**:

| Model | Official model page | Current lifecycle |
| --- | --- | --- |
| `gpt-image-2` | [GPT Image 2](https://developers.openai.com/api/docs/models/gpt-image-2) | Deprecated; supported endpoints remain listed; no shutdown date inferred |
| `gpt-image-2.5-flare` | [2.5 Flare](https://developers.openai.com/api/docs/models/gpt-image-2.5-flare) | No deprecation indicated |
| `gpt-image-2.5-sunburst` | [2.5 Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst) | No deprecation indicated |
| `gpt-image-2.6-flare` | [2.6 Flare](https://developers.openai.com/api/docs/models/gpt-image-2.6-flare) | No deprecation indicated |
| `gpt-image-2.6-sunburst` | [2.6 Sunburst](https://developers.openai.com/api/docs/models/gpt-image-2.6-sunburst) | No deprecation indicated |

The [generation guide](https://developers.openai.com/api/docs/guides/image-generation)
verifies shared custom-size limits: edges divisible by 16, at most 3840 pixels,
ratios within 1:3–3:1 and 655,360–8,294,400 pixels. It distinguishes low/medium/high
for Image 2 from additional xhigh/max quality in 2.5/2.6, describes PNG/JPEG/WebP
output and transparency, and documents 2.6 complexity control. The
[edits reference](https://developers.openai.com/api/reference/resources/images/methods/edit)
confirms source-image input, up to 16 inputs and 32,000 prompt characters.
The installed official SDK's multipart edit parameter documentation additionally
specifies PNG/WebP/JPG files below 50 MB. IG retains stricter local limits.

`gpt-image-1.5` is deliberately **not registered**. Its
[official legacy reference](https://developers.openai.com/api/docs/guides/image-prompting)
schedules shutdown for December 1, 2026. Its fixed-size contract differs from the
new models. Existing tenants using that unregistered default must explicitly
choose one of the five supported models; no automatic migration is performed.
Dated snapshots, other models, providers, SVG/GIF and new media paths are not added.

## Exact implemented subset

`app/media/providers/openai/models.py:OPENAI_IMAGE_MODELS` is authoritative. Every
entry contains technical metadata plus immutable size/quality translations,
output/background settings and optional complexity support. Shared helpers do
not imply blanket support for all upstream features.

| Model | IG sizes, semantic quality, output | Reference inputs | Background / special settings |
| --- | --- | --- | --- |
| `gpt-image-2` | S / Q / PNG | R | auto/opaque/transparent; transparency is upstream preview |
| `gpt-image-2.5-flare` | S / Q / PNG | R | auto/opaque/transparent; no IG complexity override |
| `gpt-image-2.5-sunburst` | S / Q / PNG | R | auto/opaque/transparent; no IG complexity override |
| `gpt-image-2.6-flare` | S / Q / PNG | R | auto/opaque/transparent; optional low/medium/high complexity |
| `gpt-image-2.6-sunburst` | S / Q / PNG | R | auto/opaque/transparent; optional low/medium/high complexity |

- **S:** `2:3 → 1024x1536`, `4:5 → 1024x1280`, `1:1 → 1024x1024`,
  `16:9 → 1536x864`. Every mapping meets the verified limits. IG does not advertise
  arbitrary sizing or silently crop/rescale unsupported ratios.
- **Q:** `draft → low`, `standard → medium`, `high → high`. Native xhigh/max/auto
  are recorded in the private model definition where supported, but not exposed
  as new IG normalized profiles. No quality coercion is introduced.
- **R:** zero to eight source images, at most 32 MiB each, PNG/JPEG/JPG/WebP,
  using existing semantic roles and loose/normal/strong reference intent. The
  provider's larger limit of 16 is not advertised as IG support.
- All five use the synchronous adapter, text-to-image and reference-conditioned
  editing, one result, existing single/four-panel layouts/styles, and a 32,000
  character prompt limit. Raw intent length is checked before admission; the
  final composed provider prompt is checked again before network I/O.
- PNG is explicit on the wire and Artifact MIME is PNG. JPEG/WebP/SVG/GIF output
  selection is not accepted or advertised by this adapter's IG output contract.
- Optional Hard-mode API parameters are `background` for all five and `complexity`
  for the two 2.6 entries only. Other raw parameters are rejected before queuing.
  No new UI controls for these optional provider-specific parameters are added.

The pinned OpenAI SDK accepts arbitrary string model IDs but predates `complexity`
as a named argument. The adapter uses its supported `extra_body` path for this
field for both JSON generation and multipart edits. A real-SDK MockTransport test
verifies serialization without making a paid request. No SDK/queue dependencies
are upgraded. Existing single-image generation, reference files, prompt composition,
provider-neutral jobs and Artifact storage paths are retained.

## Tenant configuration semantics

Example (one encrypted OpenAI credential set remains unchanged):

```json
{
  "default_model": "gpt-image-2",
  "default_models": {"image": "gpt-image-2.5-flare"},
  "enabled_models": {
    "image": ["gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.6-sunburst"]
  }
}
```

- A nonempty `default_models.<media>` wins when present. For image only, absent
  media-specific default falls back to legacy `default_model`. Empty legacy values
  retain the old implicit-default meaning. A malformed/unknown explicit default
  is an error, not an arbitrary API model name.
- Absent `enabled_models.<media>` means every locally registered model. An explicit
  list is an allowlist; IDs must be unique and registered. `[]` disables every
  model for that provider/media. No default is allowed outside the enabled list.
- With no explicit default, choose the adapter's existing default if enabled and
  not sunset; otherwise choose the first eligible model in catalog order. Allowlist
  array ordering never overrides catalog order. For OpenAI this preserves the
  existing implicit Image 2 choice; its deprecation is displayed, not silently
  used as a reason to migrate tenant policy.
- Unknown media keys, wrong object/list shapes, duplicates, unknown models and
  disabled/sunset defaults fail configuration validation. The old ORM default
  column does not become a second authority.
- Shared policy is applied to Google image and Alibaba video too, preserving their
  declared models, normal protocol behavior and provider configuration. No new
  models/providers are registered for them.

The same resolver governs discovery, defaults, Auto and explicit selection. Image
Auto keeps OpenAI first, then other providers in registration order. Invalid or
unconfigured providers are unavailable; if none can serve the request, reject it.
Purpose routing first uses eligible declared use-case tags in registration/catalog
order, then normal routing. Explicit provider/model selection never switches to a
different provider or model after failure. Video retains its existing single-
enabled-provider ambiguity rule. No live provider model listing occurs on requests.

Discovery remains schema version 1 with additive fields: models are `known`, tenant
`enabled`, technically `supported` (not sunset/unknown), `available` from local
provider/policy state, and `usable` with implemented technical metadata. Optional
`lifecycle` includes deprecated, sunset date and replacement model; provider-level
configuration errors are sanitized. Local availability does **not** prove the
actual upstream account is entitled to use a model.

## Admin UI/API and tooling

Tenant Settings → Providers / Generation settings → Model governance is populated
from the backend, including labels/order, enabled checkboxes, unrestricted policy,
default selectors and deprecation notices. No provider/model constants exist in
production frontend source. Missing provider credentials disables editing with
operator setup guidance. Generation Lab uses only available/usable models and
shows deprecated labels. Its Hard-mode selector remains the explicit model path.

- Admin GET `/v2/tenant/model-governance` returns only configured provider rows and
  reviewed catalog/policy fields, never credentials or arbitrary provider config.
- Admin PATCH `/v2/tenant/model-governance/{provider}` accepts `media_type`,
  `enabled_models` (list or null for unrestricted), and `default_model` (ID or
  null to clear the override). Clearing/replacing an image override removes the
  superseded legacy JSON default. Row locking/merge preserves credentials,
  provider enablement, unrelated config and other media policy.
- Existing PUT `/v2/tenant/providers/{provider}` and the operator helper validate
  model governance too. Member/service/editor callers cannot administer it, even
  with a delegated human admin actor. No service scopes are expanded.
- The CLI builds its JSON example from the registered catalog and prints only
  safe policy/status summaries, not credentials or arbitrary config. Preserve
  [container-only development and database safety](local-development.md) guidance;
  this task does not grant permission for production configuration writes.

Saves use the server response and do not mutate the catalog. There is no ETag or
optimistic conflict API: within a policy section the last successful save wins.
The update endpoint serializes row merges, but does not claim client versioning.

## Queued work, lifecycle and compatibility

New jobs record concrete `selection.provider/model` before dispatch. Execution and
Artifact metadata retain it. Changing defaults does not re-resolve queued work.
Workers recheck registration, sunset and current model allowlist for the recorded
model; removing its permission fails explicitly without substitution. Provider
disablement retains existing failure behavior. Invalid changes to unrelated
current defaults do not replace a valid recorded selection. Legacy jobs without a
selection can resolve a registered default; they cannot infer an unknown model's
reference contract. Historical Artifact IDs and recorded metadata are not migrated.

A still-supported deprecated model remains usable. A configured sunset default
fails, and a queued sunset/unregistered model fails without changing its recorded
identity. No tenant policy is automatically rewritten to the replacement model.

## Optional real-account smoke test (operator only)

No live OpenAI request is run by automated tests or this implementation task.
Use a dedicated development tenant with the real credential already encrypted in
IG, on an isolated preview/development environment. For each enabled model:

1. Read `/v2/media/capabilities`; check that the exact model is known, enabled and
   usable. Local discovery cannot verify the OpenAI account's access rights.
2. In Generation Lab Hard mode select the discovered provider/model. Choose one
   square image at draft quality and submit once. Do not blindly retry an uncertain
   request: it may already have incurred provider usage.
3. Inspect its Job's concrete `input.selection.model` and Execution model. Verify
   the Artifact's generation metadata/model, content endpoint and preview result.
4. Change the default for a subsequent submission and confirm earlier Job/Artifact
   identity remains unchanged. Optionally test reference editing as a separate,
   explicitly chosen paid call.
5. Record any upstream access/quota/model-unavailable error per account. Do not
   silently fall back to a different model or copy credentials into a frontend.

## Release

No migration, environment change or dependency upgrade is required. Feature branch
→ authorized push → Vercel Preview → run unit/browser/provider/integration tests
and optional account smoke tests → review → merge when authorized → Vercel Production.
Verify the existing Alembic head as part of normal release checks; there is no new
revision to apply. Deploy all producers/workers before relying on allowlists or
new parameters: old deployments do not enforce the new policy. No commit/push/
merge/deployment/production operation is performed by this task.
