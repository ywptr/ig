# IG#0021 — Audio-driven video and lip-sync operations

## Status

PLANNED

## Depends on

IG#0016–IG#0020

## Objective

Represent lip sync and other audio-driven video transformations as explicit provider-neutral media operations.

## Why this exists

A generated visual and generated/recorded dialogue may come from different providers. Sandcastle and advertising workflows need to combine these assets without pretending the operation is ordinary text-to-video generation.

## Scope

Extend the operation vocabulary where appropriate:

```text
generate
edit
extend
lip_sync
```

A generic lip-sync request may include:

```text
source_video
reference/generated audio
speaker or face selection
timing controls where supported
```

The output remains a video Artifact.

Initial implementation may use Kling's supported workflow, but the operation contract should remain provider-neutral where practical.

## Cross-provider workflow

A target workflow is:

```text
Veo / Wan / Kling visual output
        +
external TTS / recorded dialogue
        ↓
lip_sync
        ↓
final spoken video
```

## Architectural decisions

IG owns:

- technical operation contracts;
- capability discovery;
- validation;
- provider execution;
- Artifact/result handling.

The consuming application owns:

- dialogue text;
- TTS/voice strategy;
- which face/speaker should speak;
- creative sequencing.

## Out of scope

- TTS product strategy;
- dialogue generation;
- character voice casting;
- dubbing workflow UI inside Sandcastle.

## Acceptance criteria

- Lip sync is represented as an explicit operation.
- Audio/video inputs use Artifact/reference contracts.
- Provider-specific details remain behind adapters.
- A cross-provider source-video + audio workflow is technically possible.
