# ig-core

`ig-core` is IG's internal Python package for reusable AI media generation. It
contains media request contracts, technical capabilities, model catalogs, validation,
OpenAI and Google image adapters, and the Alibaba video adapter. It also provides
provider operation outcomes, bounded media inspection, deterministic preview
processing, and a protocol for storing and streaming artifact bytes.

The embedding application supplies provider selection, credentials, endpoints and
authorized reference bytes. IG owns tenant policy, credential storage, authentication,
HTTP routes, database records, artifact authorization, state transitions and queue delivery.

## Basic usage

Install the wheel into a Python 3.11+ environment. IG's repo uses Python 3.13.
The example validates an image request locally and makes no provider calls.

```python
from ig_core.media.schemas import MediaGenerationRequest
from ig_core.media.validation import validate_media_request, validate_model_capabilities
from ig_core.providers.openai.models import DEFAULT_OPENAI_MODEL, OPENAI_IMAGE_MODELS

request = MediaGenerationRequest(
    media_type="image", intent={"description": "A lighthouse at dusk"}
)
metadata = OPENAI_IMAGE_MODELS[DEFAULT_OPENAI_MODEL].metadata
validate_media_request(request)
validate_model_capabilities(request, metadata)
print(metadata.id, metadata.technical.operations)
```

For execution, use a concrete adapter with trusted `ProviderRuntimeConfig` and
pass a `ResolvedImageRequest` or `ResolvedVideoRequest`. Reference IDs are opaque
to core. The caller resolves them to authorized bytes and controls where results
are persisted. Keep credentials and endpoints in trusted application configuration.

From the repository root, build with `uv build --wheel packages/ig-core`. Install
IG and its local core with `uv sync --locked` or
`pip install ./packages/ig-core -e .`. The independent smoke test can run with
`python /path/to/packages/ig-core/tests/test_consumer.py` from outside the checkout
after installing the wheel.

## Runtime dependencies

| Dependency | Purpose |
| --- | --- |
| Pydantic | Request and capability contracts |
| OpenAI SDK | OpenAI image generation and editing transport |
| Requests | Google image and Alibaba video HTTP transport |
| Pillow | Bounded image inspection and preview rendering |
| PyAV | Video inspection and sampled frames for previews |

The isolated video decoder worker requires POSIX process and resource limits.

## Consumers and stability

IG uses the same core primitives for centralized generation and service APIs.
Sandcastle V2 may embed core for capabilities, validation and provider execution
while keeping its own story, scene and domain reasoning, or call the IG service
for managed execution. See the [architecture and dependency audit](../../docs/ig-core-architecture.md).
This is an internal package with no public compatibility promise.

## Speech generation

`ig_core.media.speech.SpeechGenerationRequest` is a serializable speech request. Select a provider/model from its catalog, then supply an opaque voice ID and supported output format. `SpeechProvider` is the synchronous adapter protocol. The bundled `OpenAISpeechProvider` returns a bounded audio `ArtifactPayload` that a consumer can store under its own Artifact identity.

To add another speech provider, advertise a `text_to_speech` model with `SpeechCapabilities`, implement `from_config` and `synthesize`, and normalize upstream errors as `SpeechProviderError`. The caller owns selection, credentials, storage, and Job state. Workflow plans bind the resulting audio by Artifact ID. The provider does not need to know about creative voice profiles or workflow state.

OpenAI's `gpt-4o-mini-tts` Speech API model and formats were verified on November 20, 2026. The model is catalogued with a January 6, 2027 sunset. [Speech API reference](https://developers.openai.com/api/reference/resources/audio/subresources/speech/methods/create) · [OpenAI deprecations](https://developers.openai.com/api/docs/deprecations)
