# IG#0013 — Extract `ig-queue`

## Status

COMPLETE

## Objective

Extract queue-neutral work-delivery infrastructure from IG into a reusable Python package.

## Why this exists

IG development uses Redis/RQ while production can use Vercel Queue. Sandcastle and future applications need the same delivery abstraction without coupling business logic to one transport.

## Scope

`ig-queue` provides:

- a generic queue-backend contract;
- typed delivery envelopes and receipts;
- Redis/RQ delivery;
- Vercel Queue delivery;
- immediate and delayed delivery;
- transport capability metadata;
- provider-specific cancellation/retraction results.

## Architectural decisions

Critical invariant:

> The application database is the durable source of truth. The queue backend is only a delivery mechanism.

The queue package does not decide whether work is stale, terminal, retryable, or safe to repeat.

## Out of scope

The consuming application retains:

- durable Job/Execution records;
- idempotency;
- business-level retry policy;
- provider-operation safety;
- recovery scanning;
- worker business handlers.

## Acceptance criteria

- RQ and Vercel Queue are available behind one package-level contract.
- The package does not depend on IG application modules.
- A consumer can switch delivery backend through configuration.
- Sandcastle can consume the package without adopting IG Job ORM models.

## Validation

Independent package and consumer tests were completed during extraction and reinforced by IG#0015.

## Follow-ups

Future queue backends should use the same package contract rather than introducing application-specific delivery paths.
