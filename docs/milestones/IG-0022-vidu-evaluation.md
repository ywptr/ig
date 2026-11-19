# IG#0022 — Vidu drama/ad evaluation

## Status

CANDIDATE / EVALUATION

## Depends on

IG#0018–IG#0021 being sufficiently functional to provide a meaningful benchmark.

## Objective

Evaluate current Vidu video APIs/models specifically for Sandcastle's micro-drama and advertising use cases.

## Why this stays on the roadmap

Vidu has surfaced offerings oriented toward drama/reference-to-video and advertising/reference-to-video workflows, including audio-capable variants.

That product direction is unusually aligned with Sandcastle:

```text
micro dramas
short episodic content
advertising campaigns
recurring characters
reference-driven generation
```

It should therefore remain visible even if Wan, Kling, and Veo are implemented first.

## Evaluation scope

Reverify all current official model names and commercial/API availability at evaluation time.

Compare Vidu against the implemented Wan/Kling/Veo stack on:

- native dialogue/audio;
- lip synchronization;
- reference consistency;
- recurring-character consistency;
- multi-character scenes;
- drama-oriented generation;
- ad-oriented generation;
- duration;
- supported resolutions/aspect ratios;
- latency;
- cost;
- quality;
- API stability;
- asynchronous behavior;
- commercial terms and restrictions.

## Decision outcomes

Possible outcomes:

1. **Do not adopt** — existing providers are stronger or Vidu adds insufficient value.
2. **Add targeted support** — adopt only a specialized drama/ad operation.
3. **Promote to provider milestone** — create a full Vidu implementation milestone.

## Out of scope

This milestone does not itself commit IG to implementing Vidu.

## Acceptance criteria

- Current Vidu offerings are evaluated against actual Sandcastle requirements.
- Results are compared against working Wan/Kling/Veo implementations.
- A clear adopt/defer/reject recommendation is recorded.
