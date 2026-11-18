# Image purpose routing

`POST /v2/media/generations` accepts image `output.purpose`:

- `general` (default): preserve the tenant's normal image path.
- `thumbnail`: prefer an available suitable model with a `thumbnail` use-case tag.

Unknown purposes are rejected. Video has no purpose field and retains its existing
provider selection. Purpose controls image selection only: it does not change
quality, aspect ratio, style, count or references. For example:

```json
{
  "media_type": "image",
  "interface_level": "easy",
  "intent": {"description": "A clear lighthouse silhouette at sunset"},
  "output": {
    "purpose": "thumbnail",
    "aspect_ratio": "16:9",
    "style": "illustration",
    "quality": "standard"
  }
}
```

## Selection policy

1. Explicit provider and/or model in hard mode wins. Provider-only selection uses
   that provider's tenant/adapter model default. Model-only selection uses the
   first available registered provider declaring that model, in registration order.
   An unavailable explicit selection fails; it never triggers purpose fallback.
2. For thumbnail requests, scan available providers and models in declaration
   order, selecting the first tagged model compatible with the generic controls
   and adapter submission validation. Current matches normally select Google.
3. Otherwise use the normal image path: prefer the existing default image provider
   (OpenAI) when its configured default is usable, then other registered available
   providers in declaration order. This also enables Google-only tenants.
4. Model defaults use `default_models.image`, then legacy `default_model`.
   Without an explicit tenant default, prefer the eligible adapter default, then
   the first eligible model in catalog order. All routes enforce the same tenant
   allowlist, registered technical catalog and lifecycle rules as discovery.

Ties use stable registration order, not price, latency, health or quality scores.
There is no tenant purpose-route JSON configuration in this release. Provider rows
do not offer an unambiguous home for cross-provider tenant routes, so that feature
is deferred rather than duplicating settings across provider configurations.

If no specialized thumbnail model is available/compatible, use the normal image
route. If there is no usable normal provider either, fail HTTP 400 before creating
an ImageRequest or Job and before enqueue. Explicit unsupported model IDs also
fail locally. Unknown configured defaults are configuration errors; explicit and default model
IDs must both be registered and tenant-enabled.

Availability is structural: registered adapter, enabled tenant row, encrypted
credential presence and valid required non-secret settings. Routing never decrypts
credentials, constructs clients or contacts providers. Credential validity, quota,
model access and runtime health are not guaranteed. An invalid normal default may
make another configured provider the normal route; no generation has occurred yet.
Normal-route adapter validation can still reject unsupported controls.

## Durable selection and diagnostics

Both V1 and V2 image submissions resolve before enqueue and persist this generic
server-generated Job input:

```json
{
  "selection": {
    "provider": "google",
    "model": "gemini-3.1-flash-lite-image",
    "purpose": "thumbnail",
    "reason": "purpose:thumbnail"
  }
}
```

Reasons are `explicit`, `purpose:thumbnail`, `tenant_default`, `adapter_default`,
or `purpose:thumbnail:fallback:tenant_default` / `purpose:thumbnail:fallback:adapter_default`.
Fallback reasons preserve both the requested purpose and the selected default source.
Purpose and selection also survive into completed Execution and Artifact metadata.
The new Job selection is built internally; public request fields cannot override it.

Workers do not rerun routing or model-default resolution for these Jobs. Changing
tenant defaults between submission and execution cannot change their provider/model.
Workers still use current encrypted credentials and require an enabled configuration;
removing/disabling it or disabling its recorded model can fail a queued Job, without a replacement generation.
Older Jobs without a selection keep their historical execution behavior. V1 response
shape and image controls are preserved; its new internal Jobs gain a general selection.
The existing image transaction/crash/projection limitations remain unchanged.

## Generation Lab

Discover image purposes and generic model metadata through
`GET /v2/media/capabilities`. Its image `default_provider` represents the general
automatic route. Easy/medium should offer purpose with provider/model Auto. Hard
may additionally offer explicit provider/model selection, which overrides purpose.
No frontend, billing, pricing, provider-health routing, automatic post-failure
fallback, benchmarking, or dynamic optimization is implemented here.
