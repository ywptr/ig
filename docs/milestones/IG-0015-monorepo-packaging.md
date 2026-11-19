# IG#0015 — Unified monorepo packaging and deployment hardening

## Status

COMPLETE / RELEASE VALIDATION PENDING

## Objective

Make IG, `ig-core`, `ig-queue`, and `ig-storage` use one coherent monorepo dependency, development, build, and deployment model while preserving independent wheel installability.

## Scope

- uv workspace integration;
- backend dependencies on all three internal libraries;
- editable development installs;
- non-editable production installs;
- API/worker dependency parity;
- Docker/Vercel build procedures;
- independently buildable wheels;
- clean-environment validation;
- reusable-package adoption documentation.

## Architectural decisions

The packages remain independent:

```text
ig-core     independent
ig-queue    independent
ig-storage  independent
```

The backend depends on all three.

All packages should accept explicit runtime configuration and have no mandatory package-owned environment variables where practical.

For early Sandcastle adoption, pin the three libraries to the same reviewed IG repository commit and record wheel hashes.

## Consumer documentation

See, where present:

- `docs/reusable-packages-adoption.md`
- `docs/monorepo-packaging.md`

These documents cover independent wheel adoption, workspace use, deployment, and adding future workspace members.

## Validation

The implementation report recorded:

- independent wheel builds for all three libraries and backend;
- clean editable and non-editable workspace installs;
- 547 backend tests passed, 52 skipped;
- frontend tests/build/typecheck passed;
- combined three-package consumer example passed;
- no application-module dependency from the reusable packages.

## Remaining release gates

At completion of the implementation report, live Docker daemon validation and live Vercel Preview deployment remained unverified. These must be treated as release gates until confirmed in the actual target environments.

## Follow-ups

IG#0016 begins the next capability expansion: first-class audio-capable video.
