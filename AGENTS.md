+++ AGENTS.md
# IG — Codex Working Instructions

## Project

IG is a multi-tenant AI media generation control plane.

Current release line:

```text
V4.0
```

Current primary capabilities:

- FastAPI backend
- React/Vite frontend
- PostgreSQL via Neon
- SQLAlchemy + Alembic
- Vercel deployment
- Vercel Queue
- Vercel Blob
- provider-neutral media generation API
- OpenAI image generation
- tenant-specific encrypted provider credentials
- IS OIDC authentication
- JIT local user and tenant-membership provisioning

The next major objective is video-generation support for Sandcastle.

---

## Architectural principle

Always ask:

> Is this provider/infrastructure-specific, or is this a reusable
> control-plane primitive?

Reusable control-plane concepts belong in generic IG layers.

Provider-specific API behavior belongs inside provider adapters.

Do not leak provider-specific concepts into generic API schemas,
job orchestration, artifact storage, or tenant-management code unless
there is a strong technical reason.

---

## Important boundaries

### IS

IS owns:

- identity
- authentication
- login
- OIDC
- identity admission

IG owns:

- local application users
- external identity mapping
- tenant membership
- tenant roles and authorization
- jobs
- executions
- artifacts
- provider configuration

Do not move IG-specific tenant authorization into IS.

### Sandcastle

Sandcastle is a consumer of IG.

Do not modify Sandcastle from this repository.

IG should expose reusable media-generation capabilities that
Sandcastle can consume.

### Axtor / Nexus / other applications

Do not modify other applications from the IG repository.

If a requested implementation requires a change in another application,
identify that dependency rather than implementing cross-project changes
inside IG.

---

## Current authentication architecture

Interactive authentication is IS OIDC.

IG uses:

```text
Authorization Code
+ PKCE S256
```

External identities are mapped using:

```text
issuer + subject
```

Do not automatically link accounts by email.

Email is profile data, not the identity key.

IG creates its own application session after successful IS
authentication.

Legacy password/direct-Google authentication remains temporarily but is
deprecated.

Do not expand legacy authentication functionality.

---

## Tenant behavior

Tenant resolution begins with:

```text
hostname
→ TenantDomain
→ Tenant
```

Every usable tenant requires a matching TenantDomain.

`allow_self_registration` in V4 primarily means:

> allow a valid IS-authenticated identity to receive a tenant
> membership automatically.

It is tenant-level admission control, not public unauthenticated account
registration.

---

## Provider configuration

Provider credentials are tenant-specific.

Do not add provider API keys as normal global application environment
variables.

Credentials are stored encrypted using:

```text
TENANT_SECRET_ENCRYPTION_KEY
```

Use the existing provider-config service rather than directly writing
encrypted credential fields.

The generic administrative helper is:

```text
scripts/configure-tenant-provider.py
```

---

## Database

Primary database:

```text
PostgreSQL / Neon
```

ORM / migrations:

```text
SQLAlchemy
Alembic
```

Do not make destructive database changes unless explicitly requested.

Schema changes require Alembic migrations.

Prefer request-scoped database sessions for HTTP request handling.

Background workers may create their own database sessions.

Avoid unnecessary polling or repeated database reads.

---

## Queue and asynchronous work

Primary queue:

```text
Vercel Queue
```

Pinned dependencies currently include:

```text
vercel-queue==0.9.1
vercel==0.11.5
```

Do not upgrade these casually.

They were pinned because newer versions previously broke Vercel
subscriber discovery/deployment.

Redis/RQ remains an alternative queue implementation.

Generic job/execution logic should remain queue-neutral.

---

## Artifact storage

Primary cloud artifact store:

```text
Vercel Blob
```

Local storage also exists.

API consumers should work with artifact IDs rather than physical storage
URLs.

Storage-specific behavior belongs behind the artifact-storage
abstraction.

---

## Media generation

Primary endpoint:

```text
POST /v2/media/generations
```

Current implemented media type:

```text
image
```

The design is intended to support:

```text
image
video
audio
```

Future video support should reuse the same generic control-plane path
where practical:

```text
MediaGenerationRequest
→ validation
→ tenant/provider resolution
→ Job
→ Execution
→ provider adapter
→ Artifact
```

Do not create a completely separate video subsystem unless the existing
architecture genuinely cannot support the required lifecycle.

---

## Image behavior

Existing image generation is production-like and must not regress.

Current OpenAI image model:

```text
gpt-image-2
```

Existing functionality includes:

- text-to-image
- semantic styles
- aspect ratio and quality preferences
- single and panel layouts
- uploaded/generated artifact references
- semantic reference roles
- reference strength

Any video-related change must preserve existing image behavior and tests.

---

## Frontend

Frontend:

```text
React
TypeScript
Vite
```

Do not hard-code provider/model choices in React when those values can be
obtained from backend capability metadata.

Avoid continuous idle polling.

Polling should run only while an operation is actively pending/running
and stop when terminal.

---

## Code-change rules

- Inspect existing abstractions before adding new ones.
- Prefer small focused changes.
- Avoid broad refactors unless required by the task.
- Preserve backwards compatibility unless explicitly told otherwise.
- Do not silently remove APIs.
- Do not modify production infrastructure.
- Do not rotate or expose secrets.
- Never print provider credentials.
- Never commit secrets.
- Add or update tests for behavior changes.
- Run the relevant focused tests first.
- Then run the full backend/frontend test/build suite where available.

---

## Audit expectations

When asked to audit:

- do not modify files unless explicitly authorized;
- identify actual code paths, not just README claims;
- separate confirmed findings from assumptions;
- cite file paths and relevant functions/classes;
- prioritize findings by severity;
- identify dead code, duplicated abstractions, architectural leakage,
  security risks, and missing tests;
- pay special attention to the media/provider/job/execution/artifact
  path because it will soon gain video support.

---

## Current next objective

Prepare IG for video generation required by Sandcastle Milestone 5.

Before implementation, audit whether the current abstractions can
support:

```text
media_type = video
```

without duplicating the image pipeline.