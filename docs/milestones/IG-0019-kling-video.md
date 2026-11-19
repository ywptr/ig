# IG#0019 — Kling provider

## Status

PLANNED

## Depends on

IG#0016

## Objective

Add Kling as a first-class video provider with strong support for reference-driven and speech-aware generation.

## Scope

During implementation, verify current official Kling API model identifiers and availability. Prioritize useful current families such as:

```text
Kling 4.x
Kling Flash variants
Kling 3.x
Kling Omni / multimodal-reference variants
```

Capabilities of interest include:

- text-to-video;
- image-to-video;
- multimodal/reference-driven generation;
- native audio/dialogue;
- voice-reference workflows;
- recurring-character/reference workflows;
- duration, resolution, and aspect-ratio controls;
- asynchronous operation handling.

## Architectural decisions

Kling-specific details stay inside the adapter.

The shared capability layer should describe what a model can do without forcing callers to identify Kling model names.

Omni/reference workflows are technically important to Sandcastle, but the adapter must not know Sandcastle concepts such as Character, Scene, Episode, or Canon.

## Sandcastle relevance

Potential Sandcastle mapping:

```text
Character visual references
Character voice reference
Scene prompt / dialogue
        ↓
normalized IG technical request
        ↓
Kling reference / Omni workflow
```

Sandcastle chooses the references. IG performs capability validation and provider execution.

## Out of scope

- narrative reasoning;
- character persistence semantics;
- voice ownership/policy;
- Sandcastle-specific provider routing.

## Acceptance criteria

- Kling appears through normal capability discovery.
- Reference and speech capabilities use generic contracts.
- Async jobs use existing durable Job/Execution infrastructure.
- No Kling model-name branching is required in consumer applications.
