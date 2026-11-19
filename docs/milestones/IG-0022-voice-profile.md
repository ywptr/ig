# IG#0022 — Reusable voice profiles and voice references

## Status

PLANNED

## Depends on

IG#0016

## Objective

Add reusable, provider-neutral voice identity and voice-reference capabilities to IG / `ig-core` so applications can keep a generated character or campaign voice consistent across many generations.

This is a technical media capability. It must remain independent of Sandcastle's Character, Story, Episode, Canon, or other creative-domain entities.

## Why this exists

Speech-capable video is useful only if recurring voices remain recognizable and controllable across scenes, episodes, campaigns, providers, and model upgrades.

A prompt such as "warm female voice, slightly husky" is not sufficient identity.

IG should support stable technical voice identity through explicit provider voice handles and/or canonical reference audio, while leaving creative ownership and character association to the consuming application.

## Core principle

Separate **voice identity** from **performance**.

Voice identity describes the durable technical reference:

```text
provider / provider voice handle
canonical reference audio
language / accent metadata
vocal descriptors
model/version
generation settings
pronunciation resources
```

Performance describes scene-specific delivery:

```text
emotion
intensity
pace
volume
whisper / shout
scene-specific delivery
```

Changing performance must not silently create a new voice identity.

## Proposed reusable contracts

A provider-neutral technical contract may resemble:

```text
VoiceProfile
├── voice_profile_id / external identifier where appropriate
├── provider
├── provider_voice_id / clone_id
├── canonical_reference_artifact_ids
├── language
├── accent
├── age / gender presentation descriptors where useful
├── pitch-range descriptor
├── speaking-rate baseline
├── vocal texture / timbre descriptors
├── default neutral style
├── pronunciation lexicon reference
├── provider model/version
└── provider-specific generation settings
```

Exact fields should remain capability-driven and should not force unsupported properties onto every provider.

## Voice reference role

Extend generic media-reference capabilities so requests can express an audio reference with a semantic technical role such as:

```text
reference_audio
role = voice
```

or an equivalent typed `voice_reference` slot.

This should compose with IG#0016 reference roles and capability metadata.

## Canonical reference audio

IG should support one or more canonical clean reference audio Artifacts for a voice where the provider supports reference-conditioned voice or video generation.

The reference should preserve:

- immutable Artifact identity;
- provenance;
- media metadata;
- checksum;
- provider/model/version association where relevant.

Reference audio should not be rewritten merely because a later performance differs.

## Provider voice handles

Where a provider exposes a durable voice/clone identifier, IG should be able to carry that handle without making the consuming application understand provider-specific request syntax.

The application may choose to persist the provider handle as part of its own voice-profile record.

IG must not assume provider voice IDs are portable across providers.

## Calibration / regression pack

Define a reusable calibration concept for important recurring voices.

A calibration pack may include approved short utterances covering a representative subset such as:

```text
neutral
happy
sad
angry
urgent
quiet
question
exclamation
slow
fast
```

The purpose is regression testing, not necessarily provider training.

When a provider/model/version/settings change, a consumer can regenerate the pack and compare results before accepting the new technical voice configuration.

IG may provide the execution primitives and result metadata; approval and creative judgment remain application-owned.

## Pronunciation consistency

Support a provider-neutral way to supply or reference pronunciation guidance where supported.

This may include:

- proper names;
- fictional places;
- brands;
- multilingual names;
- abbreviations;
- recurring terminology.

Do not assume one provider's lexicon format is universal. Adapters translate the generic intent when possible.

## Model/version pinning

Voice continuity should make the effective provider/model/version visible and reproducible.

A model upgrade must not silently mutate an approved recurring voice configuration.

Applications should be able to decide when to migrate a voice profile to a newer provider/model configuration.

## Interaction with generated video

Preferred consistency path:

```text
stable voice profile
→ generated/recorded dialogue audio
→ audio-conditioned video or lip sync
```

Where the video provider directly accepts a voice reference:

```text
stable voice profile/reference audio
→ speech-capable video provider
```

Pure descriptive prompting without a persistent voice handle/reference remains a fallback, not the strongest continuity mechanism.

## Architectural boundary

IG / `ig-core` owns:

- generic voice/reference contracts;
- capability discovery;
- technical validation;
- provider voice/reference translation;
- provider execution primitives;
- audio Artifact/media inspection;
- provider/model/version metadata.

IG does **not** own:

- Sandcastle Character identity;
- which character uses which voice;
- creative casting;
- emotional/story interpretation;
- dialogue generation;
- canon rules;
- campaign/persona semantics.

A consuming application such as Sandcastle may maintain its own persistent Character-linked Voice Profile and translate it into IG technical voice/reference requests.

## Security and rights

Voice cloning/reference workflows can carry meaningful rights and consent requirements.

The technical design should preserve provenance and leave room for:

- source/reference ownership metadata;
- provider consent/verification requirements;
- tenant policy;
- audit evidence;
- disabling cloning where not entitled.

Do not bypass provider verification requirements.

## Acceptance criteria

- Voice/reference capabilities are discoverable without model-name checks.
- A request can carry a stable provider voice handle and/or canonical voice-reference audio where supported.
- Voice identity and performance instructions are represented separately.
- Provider/model/version and effective settings are reproducible.
- Audio Artifacts used as references preserve provenance and authorization.
- A calibration/regression workflow can be constructed without Sandcastle-specific code.
- Sandcastle can map a Character-owned Voice Profile into these generic contracts.
- No IG core API requires a Sandcastle Character ID.

## Follow-ups

- IG#0017–IG#0020 provider milestones should adopt these contracts where their APIs support persistent voices, reference audio, native dialogue, or lip sync.
- Provider-specific voice cloning/TTS support may warrant later milestones if the existing video providers do not provide sufficient control.
