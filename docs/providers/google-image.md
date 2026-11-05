# Google Gemini image provider

Provider ID: `google`. Initial model: `gemini-3.1-flash-lite-image` (display name
Gemini 3.1 Flash-Lite Image). IG positions it as an economical option for thumbnail,
draft and preview imagery. Those discovery `use_cases` tags are descriptive only;
purpose/profile routing, ranking, fallback and cost estimation are not implemented.

## Tenant configuration

Use the existing generic tool:

```bash
python scripts/configure-tenant-provider.py --tenant-slug <tenant-slug> --provider google
```

Supply tenant credentials through its existing credential prompt:

```json
{"api_key": "<google-gemini-api-key>"}
```

Non-secret configuration:

```json
{"default_models": {"image": "gemini-3.1-flash-lite-image"}}
```

Credentials use the existing encrypted tenant-provider store. There is no global
Google API-key variable or Google-specific configuration script. No base URL is
required; this adapter uses the direct Gemini Developer API, not Vertex AI.

Google model selection is explicit hard-mode model → tenant `default_models.image`
→ legacy `default_model` → adapter default. Unknown models fail locally without a
provider call. OpenAI retains its existing `default_model` policy unchanged.

Automatic easy/medium image generation still selects OpenAI, including when both
providers are configured. Google-only tenants must use hard mode with an explicit
provider; IG does not silently switch automatic requests to Google. Hard mode with
no explicit provider also retains OpenAI. Task 9 will address purpose/profile routing.

## Generic API request

```json
{
  "media_type": "image",
  "interface_level": "hard",
  "intent": {"description": "A simple lighthouse illustration, clear silhouette"},
  "output": {"aspect_ratio": "1:1", "quality": "draft", "count": 1},
  "advanced": {"provider": "google", "model": "gemini-3.1-flash-lite-image"}
}
```

Send this to `POST /v2/media/generations`. Its normal image `request_id` follows
the existing ImageRequest compatibility projection and image Job/Execution path.
Poll `GET /v1/images/{request_id}` until completed/failed; retrieve completed content
at `GET /v1/images/{request_id}/content`. Existing Artifact APIs also remain usable.
Google is automatically listed by `GET /v2/media/capabilities`; discovery neither
decrypts credentials nor contacts Google and does not guarantee runtime model access.
The model currently reports hard-only supported interface levels, matching its
explicit-selection-only availability before Task 9 routing.

Initial supported controls:

- Text-to-image, one output, `strategy: single`.
- Common IG image ratios: `2:3`, `4:5`, `1:1`, `16:9`. Other image ratios remain rejected.
- Existing semantic styles: auto, photoreal, anime, 3d, painterly, illustration,
  comic and cinematic. Auto adds no style instruction.
- Single canvas or one four-panel image using existing generic panel semantics.
- Zero or one resolved image reference, PNG/JPEG/WebP, at most 10 MiB. Every IG
  reference role/strength is expressed semantically in the prompt, not through
  provider-native role enforcement. Normal tenant/user Artifact access checks
  run before reading bytes. No private Blob URLs are sent to Google.
- Fixed 1K output tier (`1k` in discovery). Draft asks for a simpler composition,
  standard adds no quality instruction, and high asks for greater care/detail.
  They do not select different resolutions, pricing tiers or thinking settings.
  There is no new generic image resolution selector.

## Adapter boundary and failure behavior

Only `app/media/providers/google/image.py` owns endpoint/authentication, JSON
translation, inline reference encoding, response candidate/part parsing, MIME
decoding and provider metadata sanitization. It uses the existing `requests`
dependency and the documented synchronous `generateContent` API. Google now also
documents Interactions; IG deliberately uses a stateless single-turn exchange.

One candidate and one image are requested. Text/thought content is not persisted.
A blocked, incomplete, missing, malformed or multiple-image response fails cleanly;
IG does not automatically submit a replacement generation. Only allowlisted numeric
usage and a bounded request identifier are retained alongside generic provenance.
Raw responses, API keys, request headers and generated prose are not retained.

HTTP uses connect/read timeouts of 5/90 seconds, no redirects and no automatic
retries, plus a 120-second response deadline checked between chunks (a blocking
read is still bounded by the read timeout). Response reads are incremental and
capped at 24 MiB; decoded PNG/JPEG/WebP
outputs are capped at 16 MiB and checked for matching file signatures. Bytes are
persisted through existing image Artifact storage with a MIME-matching extension.
These are bounded buffered reads, not streaming-to-storage. Image generation keeps
the existing synchronous worker/crash semantics; a timeout can remain ambiguous
after a billable provider submission. The existing synchronous image path can
also hold its read transaction across the provider call. Reference lookup errors
occur before its image-failure projection handler, so a failed Job can leave its
ImageRequest showing generating. This task does not redesign those existing paths.

## Manual smoke test — billable, not run automatically

1. Configure/enable Google for a test tenant using the generic tool above. Do not
   put credentials in source, logs, shell history or the generation request.
2. Authenticate as an ordinary member on that tenant's hostname. Check discovery
   shows Google configured/enabled/available and the expected default model.
3. Submit the generic request above once. Keep one output, 1:1 and draft/default
   quality, with no references. Every quality profile uses the same 1K tier.
4. Follow its request ID through the existing image endpoints. Verify one completed
   Artifact, correct tenant/user, provider/model provenance and usable image bytes.
5. If submission times out or fails ambiguously, inspect the Job before considering
   a manual retry; do not assume no billable generation occurred.

## Sources and future models

- [Google model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite-image)
- [Generate Content API reference](https://aistudio.google.com/api-reference/generate-content/generate-content)
- [Image configuration and response reference](https://aistudio.google.com/api-reference/generate-content)
- [Generate Content image guide](https://ai.google.dev/gemini-api/docs/generate-content/image-generation?hl=en)

Future Google models can extend the adapter's model metadata and model-specific
capability handling. Nano Banana 2.1 is not registered in this task. Broader
resolution support and different reference limits require explicit validation and
tests, even if the underlying HTTP API is shared. There is no Google Search
grounding, conversational editing, native safety/thinking UI or Gemini video here.
