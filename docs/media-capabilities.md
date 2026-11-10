# Tenant-aware media discovery

Authenticated tenant members can call `GET /v2/media/capabilities` (HTTP 200).
Tenant resolution and membership authorization are the same as generation.
No administrative role is required. Clients should use this endpoint to build
media, model and generic control choices rather than maintaining provider catalogs.

The response contains:

- `interface_levels`: easy and medium use automatic provider/model selection;
  hard permits optional explicit `advanced.provider` and `advanced.model` selection.
- `media_types`: registered image/video capabilities, including unavailable entries.
  `available` means at least one registered provider is locally usable.
- `providers`: stable IDs and display names, tenant-specific state, resolved
  `default_model`, `default_model_available`, and the registered model catalog.
- `models`: stable IDs and display names, availability, supported interface levels,
  `selectable_in` (hard when available), generic `capabilities`, and optional
  `use_cases` metadata. The economical Google image model carries `thumbnail`,
  `draft` and `preview` tags. Image `thumbnail` routing uses its matching tag;
  `draft` and `preview` are descriptive tags, not additional request purposes.
- Image `purposes`: `general` and `thumbnail`. Video has no purposes.

Provider state means:

| Field | Meaning |
| --- | --- |
| `registered` | An adapter and discovery declaration exist in IG. |
| `configured` | An encrypted credential blob exists and required non-secret configuration passes local checks. |
| `enabled` | The tenant configuration row is enabled. |
| `available` | Registered, configured and enabled; catalog models may be explicitly selected in hard mode. |

Discovery never decrypts credentials, constructs provider clients, calls providers,
performs health checks or submits generation. Credential presence cannot establish
whether the encrypted contents contain a valid API key. Availability does not
guarantee provider health, account access, model access, quota or successful generation.
No credentials, endpoints, workspaces, raw configuration, storage URIs or temporary
media URLs are returned.

`default_provider` is the current automatic provider when discovery can establish
a usable registered default. It is null when none can be established. Image
selection retains OpenAI when its normal default is usable, even when Google is
enabled. A Google-only tenant can use Google automatically in easy/medium.
Both current image models support easy/medium/hard. `default_provider` reflects
the general route; thumbnail selection can differ. See [purpose routing](media-routing.md).
Video still requires
exactly one enabled registered provider for automatic selection; multiple enabled
providers remain selectable in hard mode but do not trigger automatic ranking.
An enabled provider with incomplete configuration still participates in that
existing video ambiguity rule. This endpoint does not change generation routing.

OpenAI image defaults retain `config_json.default_model` with adapter fallback.
The unused ORM `default_model` column and `default_models.image` do not override
OpenAI defaults. Google image defaults resolve `default_models.image`, then legacy
`default_model`, then the adapter default. Explicit hard-mode models take priority.
Existing custom image defaults are reported as identifiers, but
`default_model_available` is false when they are outside the registered catalog;
legacy configured image defaults retain their existing acceptance behavior. Explicit
hard-mode model IDs must be registered. Video uses
`default_models.video` with adapter fallback and registered-model validation.
Invalid video defaults produce a null default rather than failing discovery.

Generic image controls include semantic quality/styles, references with roles and
strength, supported aspect ratios and panel layouts. Discovery conservatively
advertises one output image and four-panel grids: the current adapter returns one
image, and the current layout resolver only automatically arranges four panels,
although request schemas accept broader counts. This task does not change those
legacy request semantics. Explicit panel arrangements remain hard-only.
Google initially accepts one reference and strictly enforces one output/single
strategy. Its `resolutions: ["1k"]` describes a fixed output tier, not a new
generic image resolution input. All semantic quality profiles use that same tier.
See [Google image generation](providers/google-image.md) for configuration and limits.

Generic video controls include text input, one optional `first_frame` image,
one output, single layout, 2–30 seconds, `480p`/`720p`/`1080p`, `16:9`/`9:16`/`1:1`
and optional audio. Native provider payloads and status names are not part of
discovery. See [video generation](video-generation.md) for request details.

Future adapters declare model names and generic capabilities beside their
registration; the API route does not need provider-specific branches. Generic
`use_cases` tags describe intended model usage. The routing service consumes tags
for supported non-general purposes; discovery itself never submits generation.

The [Generation Lab](generation-lab.md) uses `interface_levels`, `selectable_in`
and generic controls from discovery. It keeps provider/model automatic in
easy/medium, allows optional hard-mode selection, follows IG Job correlation,
treats waiting/polling as active, and renders IG image/video Artifacts.

## Technical contract (schema version 1)

The discovery document now includes `schema_version: 1`. Each model has an
additive `technical` contract and `usable` flag. Existing `capabilities` controls
and interface hints remain compatible. Technical declarations are independent of
UI modes and reusable by routing, workflow validation, and future consumers.

`TechnicalCapabilities` (`app/media/technical.py`) uses closed Literal types for
media kinds and generation operations. Inputs and outputs can represent text,
image, video, and audio without enabling their execution. Execution describes the
provider operation, not the HTTP submission: synchronous image operations still
run inside queued IG Jobs. It records polling, webhooks, IG cancellation semantics,
and provider-side cancellation separately. Current registered adapters have no
webhook or provider cancellation support. `cooperative` means IG can cancel queued
work and fence running results at safe checkpoints; an in-flight provider call
may continue and incur charges.

Adapter model declarations are the sole authority for technical support. The
provider registry determines which adapters are deployed. The database controls
tenant credentials, enablement, and defaults only; it cannot grant features.
There is no Provider, Model, Capability, or Workflow database entity to synchronize,
and no migration or capability seed is required. New models are reviewed code
changes with tests rather than schema migrations. Contract versions are independent
of provider model IDs. Adding fields is additive; changing semantics requires a
contract-version decision.

Provider metadata owns identity, display name and its model catalog. Technical
behavior belongs on each model because models from one provider can differ.
Provider-wide support is the union of its declared models, never an implicit grant
to every model. Shared declarations can be constructed in adapter code when useful.
No duplicated provider-wide feature flags or tenant overrides are introduced.

`available` preserves the existing local configuration meaning. `usable` additionally
requires a known technical declaration. Neither flag verifies credentials or account
entitlements remotely. An unknown legacy OpenAI default is still accepted for
backward compatibility but has no invented model contract; it is not added to the
catalog and `default_model_available` remains false. Catalog models remain usable
through explicit selection even with an unknown configured default.

`validate_model_capabilities` rejects incompatible output media, text/reference
inputs, reference counts, multiple-reference support, aspect ratios, and declared
prompt/duration/resolution/audio limits before creating jobs. Image selection calls
it for known models, including legacy image submission through `/v2/images`.
Existing video execution retains its existing validation; future adapters can use
the reusable validator without changing it. Artifact
ownership, MIME and byte validation remain at reference resolution, where the
actual artifact is known; technical MIME lists describe supported formats.
Count/layout/strategy and native parameters retain existing adapter/legacy checks:
OpenAI historically accepts counts/layouts broader than the conservative discovery
controls, so this milestone does not tighten them. Unknown legacy image defaults
retain their earlier validation. Strict discovery-based validation therefore applies
to known models rather than promising universal validation of legacy requests.

Example excerpt for a configured, enabled tenant (other response fields omitted):

```json
{
  "schema_version": 1,
  "media_types": [{
    "id": "image",
    "providers": [{
      "id": "openai", "configured": true, "enabled": true, "available": true,
      "models": [{
        "id": "gpt-image-2", "available": true, "usable": true,
        "technical": {
          "version": 1,
          "input_media": ["text", "image"], "output_media": ["image"],
          "operations": ["text_to_image", "image_to_image", "reference_image_conditioning", "multi_reference_conditioning"],
          "execution": {
            "mode": "synchronous", "polling_required": false,
            "webhook_supported": false, "cancellation": "cooperative",
            "provider_cancellation_supported": false
          },
          "reference_mime_types": ["image/png", "image/jpeg", "image/webp", "image/jpg"],
          "output_mime_types": ["image/png"], "max_prompt_characters": null
        }
      }]
    }]
  }]
}
```

OpenAI's controls declare eight references, `2:3`, `4:5`, `1:1`, `16:9`, semantic
quality/styles, one output and single/four-panel layouts. Reference images route to
`images.edit`; no mask/inpainting, upscale or standalone variation operation is
claimed merely because an upstream API might support it.

Illustrative only: a future model can declare:

```python
TechnicalCapabilities(
    input_media=("text", "image"), output_media=("video",),
    operations=("text_to_video", "image_to_video", "reference_image_conditioning"),
    execution=ExecutionBehavior(mode="asynchronous", polling_required=True),
    reference_mime_types=("image/png",), output_mime_types=("video/mp4",),
    max_prompt_characters=4000,
)
```

Pair this with model-specific `GenerationControls` (duration, aspect ratios,
resolution, reference limits), declare it in adapter `CAPABILITIES`, and register
the adapter. Discovery and generic validation require no provider-specific branches.
Execution support must be implemented and tested separately before registration;
a declaration does not create an executable path. This milestone adds no provider,
video execution path, selector, external model-catalog fetch, or remote health poll.
The pre-existing Alibaba video implementation remains in the checkout.
