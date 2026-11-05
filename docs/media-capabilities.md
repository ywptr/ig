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
  `draft` and `preview` tags; these tags do not affect routing.

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
selection retains OpenAI as the existing default, even when Google is enabled.
A Google-only tenant must explicitly select Google in hard mode; its image
`available` can be true while `default_provider` is null. Google's registered model
reports `supported_interface_levels: ["hard"]` until automatic routing supports it.
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
this does not change existing image-generation acceptance behavior. Video uses
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
`use_cases` tags can later describe draft or thumbnail intent without changing
the response schema. Tags do not implement routing.

The future Generation Lab should use `interface_levels`, `selectable_in` and
generic controls from discovery, keep provider/model automatic in easy/medium,
and optionally offer selection in hard mode. It must independently follow Job
correlation, treat waiting/polling as active states, and render video Artifacts.
No frontend is implemented here.
