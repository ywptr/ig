# IG#0012 — Extract `ig-core`

## Status

COMPLETE

## Objective

Extract reusable media-generation and provider primitives from the IG application into an independently installable Python package.

## Why this exists

IG is both a product and a reference implementation for reusable AI media infrastructure. Sandcastle and future applications should be able to reuse generation primitives without importing IG's application, database, tenancy, or HTTP layer.

## Scope

`ig-core` owns reusable primitives including:

- media-generation request contracts;
- technical capability contracts;
- model/control catalogs;
- capability-driven validation;
- prompt translation;
- provider-neutral generation outcomes;
- reference-input definitions;
- bounded media inspection;
- deterministic preview generation;
- provider adapters that do not depend on IG application state.

Initial extracted adapters include image-provider support and the reusable Alibaba/Wan video adapter foundation.

## Out of scope

IG retains:

- FastAPI routes;
- IS/Nexus integration;
- tenant/user policy;
- SQLAlchemy/ORM/session handling;
- durable Job and Execution state;
- fencing/cancellation/recovery policy;
- Artifact authorization, ownership, lineage, and retention;
- credential persistence.

## Architectural decisions

`ig-core` accepts explicit runtime configuration. It must not require a consuming application to adopt IG's tenant model or database.

Provider operation outcomes may be represented in core, while durable workflow state remains application-owned.

## Acceptance criteria

- Package builds and installs independently.
- IG consumes the package rather than maintaining a duplicate implementation.
- Package does not depend on IG application modules.
- Sandcastle can use the package directly for media/provider primitives.

## Validation

Independent package build/install and consumer tests were completed during extraction and later reinforced by IG#0015.

## Follow-ups

IG#0016 extends the capability model for audio-capable video.
