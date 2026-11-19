# IG#0019 — Expanded Wan video support

## Status

PLANNED

## Depends on

IG#0016

## Objective

Expand the existing Alibaba/Wan adapter toward current audio-capable, reference-driven, and longer-form video workflows.

## Scope

During implementation, reverify current official Alibaba model identifiers and capabilities. Evaluate/support the current useful Wan families, including:

```text
Wan 2.7
Wan 3.x
additional useful Wan 2.6 variants
```

Potential workflows include:

- text-to-video;
- image-to-video;
- reference-to-video;
- first/last-frame workflows;
- audio-conditioned generation;
- native generated dialogue/audio;
- video editing;
- video extension.

## Architectural decisions

Preserve IG's durable asynchronous execution model:

```text
submission
→ provider-operation checkpoint
→ polling / continuation
→ result retrieval
→ Artifact finalization
```

Provider-specific operation identifiers may be represented in `ig-core`, but durable Job/Execution state remains application-owned.

Preview/experimental models must be marked as such in capability metadata where applicable.

## Sandcastle relevance

Wan is a likely workhorse provider for:

- micro-drama scenes;
- recurring-character references;
- longer clips;
- generated dialogue;
- lower-cost iteration;
- ad creative variants.

Sandcastle still owns scene-to-media reasoning and passes normalized technical inputs to IG.

## Out of scope

- Sandcastle story/canon logic;
- creative model-routing strategy;
- provider-specific UI branching.

## Acceptance criteria

- Current supported Wan variants are discoverable through capability metadata.
- Audio/reference features are validated through generic contracts.
- Async operations recover through the existing queue/job architecture.
- Generated outputs and their audio presence are recorded correctly.
