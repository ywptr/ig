# IG#0018 — Dedicated voice providers

## Status

PLANNED

## Depends on

IG#0017

## Objective

Add first-class provider adapters for dedicated speech synthesis / voice cloning systems, starting with ElevenLabs, while preserving a provider-neutral voice contract in `ig-core`.

The goal is not to expose every vendor-specific feature directly. It is to give Sandcastle and other consumers a stable way to:

- synthesize speech from text;
- use a persistent provider voice / clone handle;
- create or register a voice from reference audio where supported;
- preserve model/version/settings for continuity;
- request multilingual / expressive speech;
- use the resulting audio as an Artifact for later video generation or lip sync.

## Priority provider

### ElevenLabs — first implementation

Implement ElevenLabs first because it currently provides a mature API surface for:

- text-to-speech;
- multilingual speech;
- voice cloning / persistent voice identities;
- expressive controls;
- pronunciation dictionaries / custom pronunciation mechanisms where supported;
- long-running voice continuity workflows.

Exact current model identifiers and API details must be verified against official ElevenLabs documentation at implementation time.

The adapter should map provider-neutral IG voice requests into ElevenLabs-specific APIs rather than leaking ElevenLabs request shapes into Sandcastle.

## Additional providers to evaluate / support

The provider registry should remain open to additional voice engines. Priority candidates:

### OpenAI Audio / TTS

Evaluate current OpenAI speech generation models and voice capabilities.

Useful for:

- simple TTS;
- low-friction integration where a consumer already uses OpenAI;
- provider diversity.

Do not assume OpenAI provides the same cloning semantics as ElevenLabs. Capability metadata must describe actual support.

### Google Cloud / Gemini / Cloud TTS

Evaluate Google's current speech synthesis stack, including high-quality / expressive voice models and any current custom-voice or voice-replication capability.

Useful for:

- multilingual production;
- Google ecosystem deployments;
- potential tight pairing with Veo workflows.

### Cartesia

Evaluate current Sonic-family speech models and cloning / low-latency voice APIs.

Useful for:

- expressive speech;
- low-latency generation;
- real-time or conversational future use;
- provider diversity for Sandcastle previews and production.

### Resemble AI

Evaluate current TTS and voice-cloning APIs.

Useful for:

- persistent cloned voices;
- multilingual / production speech;
- enterprise-oriented voice workflows.

### PlayAI

Evaluate current PlayAI speech / cloning offerings if API access and commercial terms remain attractive.

Useful for:

- expressive dialogue;
- multi-speaker / conversational speech;
- provider diversity.

### Fish Audio

Evaluate current Fish Audio speech / cloning APIs if commercial API stability, licensing, and terms meet production requirements.

Useful for:

- expressive cloned voices;
- multilingual speech;
- potentially cost-effective generation.

### Azure AI Speech / Custom Neural Voice

Evaluate Azure's custom neural voice and speech synthesis APIs where access requirements and consent controls fit the product.

Useful for:

- enterprise deployments;
- regulated / consent-heavy voice workflows;
- language coverage.

## Provider-neutral operations

IG should support a small set of generic voice operations such as:

```text
synthesize
clone_or_register_voice
inspect_voice_capabilities
delete_provider_voice   # only where safe/authorized
```

Do not force every provider to implement every operation.

Capability discovery must state what each provider/model supports.

## Generic request shape

A provider-neutral synthesis request may conceptually include:

```text
text
voice_profile / provider_voice_handle
language / locale
performance:
  emotion
  intensity
  pace
  volume
pronunciation guidance
output format
sample rate / quality where supported
```

The exact public API should be designed during implementation, not copied blindly from any vendor.

## Generic result shape

Voice generation should return or create a normal audio Artifact with:

- media type;
- MIME type / codec;
- duration;
- sample rate / channels where available;
- provider/model/version;
- provider voice handle / effective voice identity metadata;
- generation settings;
- provenance and lineage.

## Voice creation / cloning boundary

Where a provider supports voice cloning or registration:

```text
canonical reference audio Artifacts
→ provider adapter
→ provider voice / clone handle
→ consuming app persists provider binding
```

IG should not invent a new Sandcastle Character identity.

Sandcastle owns the Character-linked Voice Profile and decides whether a created provider voice binding becomes approved.

## Security, rights and consent

Voice cloning is not a normal anonymous media-generation primitive.

Provider adapters must preserve room for:

- explicit user authorization;
- provider-required consent / verification;
- provenance of reference audio;
- tenant policy restrictions;
- auditability;
- deletion / revocation where required.

Do not attempt to bypass provider consent or verification mechanisms.

## Provider selection

Provider selection should remain capability-driven.

Consumers should be able to ask for requirements such as:

```text
persistent voice identity
multilingual
voice cloning
low latency
expressive controls
pronunciation lexicon
streaming
```

and choose among compatible providers.

Do not encode:

```python
if provider == "elevenlabs":
```

in Sandcastle creative logic.

## Sandcastle integration

Preferred production flow:

```text
Sandcastle VoiceProfile
→ approved provider binding / canonical reference audio
→ IG dedicated voice provider
→ generated dialogue audio Artifact
→ Wan / Kling / Veo / lip-sync workflow
```

This should become the strongest continuity path for recurring characters.

## Acceptance criteria

- ElevenLabs is implemented as the first dedicated voice adapter.
- Voice synthesis produces normal audio Artifacts with reproducible provider/model/settings metadata.
- Persistent provider voice handles can be used without exposing vendor-specific request syntax to consumers.
- Voice clone/register support is capability-gated.
- At least OpenAI, Google, Cartesia, Resemble, PlayAI, Fish Audio, and Azure are documented/evaluated as provider candidates.
- Provider registry design allows adding another voice adapter without changing Sandcastle domain models.
- No voice adapter depends on Sandcastle Character/Canon models.
- Consent/verification restrictions are preserved rather than bypassed.
- Generated audio can feed IG#0019–IG#0022 video workflows.
