# IG#0014 — Extract `ig-storage`

## Status

COMPLETE

## Objective

Extract reusable object/byte storage infrastructure from IG.

## Scope

`ig-storage` provides reusable storage mechanics for:

- local filesystem;
- Vercel Blob;
- Google Drive;
- provider-neutral locators;
- read/write/stat/exists/delete operations;
- streaming and backend-specific behavior;
- resumable/provider-specific upload behavior where supported.

## Architectural decisions

Critical invariant:

> The application decides **which** configured store to use. `ig-storage` decides **how** to use that store.

Runtime configuration is supplied explicitly by the consumer.

## Out of scope

IG retains:

- Artifact ORM;
- tenant/user ownership and authorization;
- retention policy;
- provenance and lineage persistence;
- preview database state;
- user storage preference;
- Drive OAuth connection persistence and refresh-token encryption;
- tenant storage governance.

## Google Drive boundary

The consuming application resolves the correct user/tenant connection and supplies runtime credentials/configuration to `ig-storage`. The package does not own application OAuth records.

## Acceptance criteria

- Local, Vercel Blob, and Google Drive storage are usable through the extracted package.
- Existing IG locators remain compatible.
- IG consumes the package.
- The package can be used independently by Sandcastle.

## Validation

Independent storage-consumer tests and package builds were completed during extraction and later validated in IG#0015.

## Follow-ups

The package contract should support future storage migration/copy workflows without taking ownership of application database records.
