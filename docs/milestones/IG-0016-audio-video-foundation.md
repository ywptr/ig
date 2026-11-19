# IG#0016 — Audio-capable video foundation

## Status

PLANNED

## Objective

Make speech/audio a first-class technical capability of video generation in `ig-core`.

Video remains:

```text
media_type = video
```

Do not introduce a separate `speech_video` media type.

## Why this exists

Micro dramas and advertising increasingly depend on generated dialogue, synchronized speech, music, and sound effects. IG needs a provider-neutral capability model so applications such as Sandcastle can reason about these features without checking model names.

## Scope

Extend technical capability metadata to represent concepts such as:

- output audio;
- native generated audio;
- generated dialogue;
- generated sound effects;
- generated music;
- audio-reference input;
- voice-reference input;
- lip sync;
- multi-speaker support;
- source-audio preservation.

Prepare generic reference-input roles such as:

```text
first_frame
last_frame
reference_image
reference_video
reference_audio
voice_reference
source_video
```

Where useful, audio-reference roles may distinguish:

```text
dialogue
voice
music
sound_effect
general_reference
```

## Media inspection

Extend deterministic video inspection to expose useful audio metadata such as:

```text
has_audio
audio_codec
audio_channels
audio_sample_rate
```

The resulting Artifact should be inspected rather than assuming audio exists merely because a provider/model advertised audio support.

## Architectural decisions

Applications must use capability metadata rather than provider/model-name checks.

Bad:

```python
if model == "wan2.7":
    ...
```

Preferred:

```python
if capabilities.generated_dialogue:
    ...
```

## Boundary

Sandcastle owns:

- story/dialogue reasoning;
- character and voice selection;
- scene composition;
- provider/model strategy.

IG / `ig-core` owns:

- technical capability description;
- request validation;
- provider execution primitives;
- deterministic media inspection.

## Out of scope

- Sandcastle narrative structures;
- TTS strategy;
- voice casting;
- provider-selection policy tied to creative semantics.

## Acceptance criteria

- Audio/speech capabilities are represented provider-neutrally.
- Reference-input contracts support audio/voice/video roles.
- Generated video inspection records whether audio is actually present.
- Existing silent-video behavior remains compatible.
