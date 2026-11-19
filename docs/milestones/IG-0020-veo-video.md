# IG#0020 — Google Veo

## Status

PLANNED

## Depends on

IG#0016

## Objective

Add Google's current Veo generation family as a first-class video provider, including synchronized audio where supported.

## Scope

During implementation, verify current official Google model identifiers, API surface, and availability.

Evaluate current relevant tiers such as:

```text
Veo Standard
Veo Fast
Veo Lite / lower-cost tiers where available
```

Capabilities of interest:

- synchronized native audio/dialogue;
- text-to-video;
- image/reference workflows;
- short high-quality video;
- duration and resolution constraints;
- aspect-ratio controls;
- async operation handling;
- quality/latency/cost differentiation.

## Model metadata

IG may expose neutral model-profile metadata such as:

```text
quality_tier
latency_tier
cost_tier
stability / preview status
```

Do not encode creative policy such as "always use Veo for final shots" in IG.

Sandcastle may use neutral metadata when choosing a generation strategy.

## Architectural decisions

Veo remains another provider behind the same capability and Job/Execution abstractions.

Google-specific operation polling, request fields, and response handling belong in the adapter.

## Out of scope

- Sandcastle provider-selection rules;
- campaign strategy;
- creative quality judgments encoded in IG.

## Acceptance criteria

- Veo models are discoverable through generic capability metadata.
- Native audio support is represented through IG#0016 contracts.
- Async execution works through the existing durable workflow.
- Consumers need no Google-specific request branching for ordinary supported operations.
