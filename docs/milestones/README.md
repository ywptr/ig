# IG Milestones

This directory is the canonical engineering roadmap and milestone history for IG.

## Status legend

- **COMPLETE** — implemented and validated to the extent recorded in the milestone.
- **COMPLETE / RELEASE VALIDATION PENDING** — implementation is substantially complete, but one or more live environment release gates remain.
- **PLANNED** — approved future work; scope may be refined during implementation.
- **CANDIDATE / EVALUATION** — important enough to retain on the roadmap, but implementation is not yet committed.

## Reusable infrastructure

| Milestone | Title | Status | Depends on | Primary outcome |
|---|---|---|---|---|
| [IG#0012](IG-0012-ig-core.md) | Extract `ig-core` | COMPLETE | — | Reusable media/provider/capability primitives |
| [IG#0013](IG-0013-ig-queue.md) | Extract `ig-queue` | COMPLETE | IG#0012 | Queue-neutral delivery with RQ and Vercel Queue |
| [IG#0014](IG-0014-ig-storage.md) | Extract `ig-storage` | COMPLETE | IG#0012–0013 | Reusable object/byte storage across local, Blob, and Drive |
| [IG#0015](IG-0015-monorepo-packaging.md) | Unified monorepo packaging | COMPLETE / RELEASE VALIDATION PENDING | IG#0012–0014 | One workspace/install/deployment model and consumer-adoption procedures |

## Audio/video generation expansion

| Milestone | Title | Status | Depends on | Primary outcome |
|---|---|---|---|---|
| [IG#0016](IG-0016-audio-video-foundation.md) | Audio-capable video foundation | PLANNED | IG#0015 | First-class audio/speech capabilities for video |
| [IG#0017](IG-0017-wan-video.md) | Expanded Wan video support | PLANNED | IG#0016 | Broader Wan variants, audio, references, and current async workflows |
| [IG#0018](IG-0018-kling-video.md) | Kling provider | PLANNED | IG#0016 | Kling generation, Omni/reference workflows, and speech-aware capabilities |
| [IG#0019](IG-0019-audio-driven-video.md) | Audio-driven video and lip sync | PLANNED | IG#0016, IG#0018 | Provider-neutral lip-sync/audio-driven video operations |
| [IG#0020](IG-0020-veo-video.md) | Google Veo | PLANNED | IG#0016 | Veo video generation with synchronized audio |
| [IG#0021](IG-0021-vidu-evaluation.md) | Vidu drama/ad evaluation | CANDIDATE / EVALUATION | IG#0017–0020 | Evaluate Vidu specifically for drama and advertising workflows |

## Milestone documentation policy

1. Significant IG engineering initiatives receive an `IG#NNNN` milestone.
2. This README is the canonical milestone index.
3. Planned milestone documents describe intent, not guaranteed implementation.
4. During implementation, update architectural decisions and acceptance criteria when reality changes.
5. After completion, record actual validation and remaining release gates.
6. Do not rewrite completed history to make it look as though later architecture existed earlier; add follow-up notes instead.
7. Provider/model names in planned milestones are not API contracts. Reverify them against current official provider documentation during implementation.
8. Database migration requirements must be explicitly recorded.
9. Feature work is performed on a feature branch.
10. Preview validation is required before merging feature work into `main`.
