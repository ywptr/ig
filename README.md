# IG — AI Media Generation Control Plane

**Version 3.2**

IG is a multi-tenant AI media generation control plane.

It provides a provider-neutral API for submitting media-generation requests, executing them asynchronously, resolving reference media, storing generated and uploaded artifacts, and managing tenant-specific provider configuration.

V3 currently implements **image generation**. The media interface is intentionally designed so that future video and audio capabilities can use the same control-plane concepts without exposing provider-specific implementation details to higher-level applications.

IG is also intended to serve as infrastructure for higher-level applications, such as **Sandcastle** and **Sociosight**.

---

## Overview

IG V3 includes:

- Multi-tenant backend architecture
- Tenant resolution by hostname
- Tenant membership and administrative roles
- Tenant-specific authentication policy
- Password and Google authentication support
- Tenant-specific model-provider configuration
- Encrypted provider credentials
- Provider-neutral media generation API
- OpenAI image provider adapter
- Semantic image styles
- Image quality and aspect-ratio preferences
- Single-image and panel-grid layouts
- Reference images with semantic roles and strengths
- Artifact upload
- Manual/external artifact import
- Persistent generation history
- Asynchronous jobs and executions
- Vercel Queue
- Redis/RQ as an optional queue backend
- Vercel Blob artifact storage
- Local artifact-storage implementation
- PostgreSQL persistence
- Alembic migrations
- React/Vite frontend
- FastAPI backend
- GitHub → Vercel CI/CD deployment

---

# Architecture

The standard V3 deployment is:

```text
Browser / API Client / 3rd Party Apps
               │
               ▼
        Vercel Frontend
         React / Vite
               │
               ▼
          FastAPI API
               │
       ┌───────┼──────────────┐
       │       │              │
       ▼       ▼              ▼
     Neon   Vercel Queue   Tenant Provider
 PostgreSQL                  Configuration
                                  │
                                  ▼
                           Provider Adapter
                                  │
                                  ▼
                                OpenAI
                                  │
                                  ▼
                           Generated Media
                                  │
                                  ▼
                            Vercel Blob
                                  │
                                  ▼
                               Artifact
```

The development machine is **not part of the deployed runtime**.

Normal development consists of editing, testing, committing, and pushing the repository. Vercel CI/CD deploys changes from GitHub.

---

# Core design principle

IG separates provider- or infrastructure-specific behavior from reusable control-plane primitives.

A useful design question is:

> Is this provider/infrastructure-specific, or is this a general control-plane primitive?

Reusable control-plane concepts include:

```text
Tenant
Provider
MediaType
Capability
Job
Execution
Artifact
Policy
Usage
```

Provider-specific behavior belongs inside provider adapters.

Storage-specific behavior belongs behind the artifact-storage abstraction.

Queue-specific behavior belongs behind the job-queue abstraction.

Higher-level applications such as Sandcastle and Sociosight should communicate with IG through semantic control-plane interfaces rather than directly through model-provider APIs.

---

# Media generation

The primary generation interface is provider-neutral.

A media request may contain concepts such as:

```text
media_type
intent
quality
style
aspect_ratio
layout
references
strategy
interface_level
advanced
```

Example image-generation request:

```json
{
  "media_type": "image",
  "interface_level": "easy",
  "intent": {
    "description": "A character walking through a rainy city street at night."
  },
  "output": {
    "quality": "standard",
    "aspect_ratio": "2:3",
    "style": "photoreal",
    "count": 1,
    "layout": {
      "type": "single"
    }
  },
  "references": []
}
```

Generation endpoint:

```text
POST /v2/media/generations
```

IG resolves the semantic request internally:

```text
MediaGenerationRequest
        ↓
semantic validation
        ↓
tenant resolution
        ↓
reference resolution
        ↓
provider configuration
        ↓
provider adapter
        ↓
model provider
```

Higher-level clients therefore do not need to know:

- OpenAI image sizes
- provider-specific API methods
- provider credential formats
- artifact storage implementation
- queue implementation

---

# Image styles

Image style is represented as a provider-neutral semantic preference.

Examples include:

```text
auto
photoreal
anime
3d
painterly
illustration
comic
cinematic
```

Provider adapters decide how each semantic style is represented to the underlying provider.

This allows a request such as:

```json
{
  "style": "anime"
}
```

to remain independent of any particular provider API.

---

# Reference media

Generation requests may reference existing artifacts.

Example:

```json
{
  "artifact_id": "<artifact-id>",
  "role": "character",
  "strength": "strong"
}
```

Supported reference roles currently include:

```text
character
outfit
style
pose
composition
environment
previous_output
```

Reference strength may be:

```text
loose
normal
strong
```

IG resolves the artifact, validates tenant/user access, retrieves its binary content from the configured artifact store, and passes the reference to the selected provider adapter.

Reference roles are request-specific.

An artifact itself is not permanently classified as a character, style, or previous output.

The same artifact may therefore be used as:

```text
character in request A
style in request B
previous_output in request C
```

---

# Artifact uploads

Images can be uploaded as artifacts independently of generation.

Supported upload formats:

```text
PNG
JPEG
WebP
```

Upload endpoint:

```text
POST /v2/artifacts/uploads
```

Uploaded artifacts may represent:

- reference material
- externally generated images
- manually produced images
- previous outputs imported into IG

An uploaded artifact may use origins such as:

```text
reference
external_generation
```

Artifact origin describes **where the artifact came from**.

Reference role describes **how a later generation uses it**.

These are intentionally separate concepts.

Artifact APIs include:

```text
GET  /v2/artifacts
GET  /v2/artifacts/{artifact_id}
GET  /v2/artifacts/{artifact_id}/content
POST /v2/artifacts/uploads
```

Storage implementation details are hidden from API consumers.

Clients work with artifact IDs and artifact-content endpoints rather than physical storage URLs.

---

# Provider adapters

IG uses provider adapters rather than calling model APIs directly from generation services.

Current image provider:

```text
OpenAI
```

Current OpenAI default image model:

```text
gpt-image-2
```

Model resolution follows:

```text
explicit request model
        ↓
tenant provider default
        ↓
provider adapter default
```

The provider-neutral request is resolved before the provider adapter performs generation.

Future providers can implement the same adapter interface.

---

# Tenant provider configuration

Model-provider credentials are **not global application environment variables**.

Each tenant has its own provider configuration.

Conceptually:

```text
Tenant
   │
   └── Provider Configuration
         ├── provider
         ├── enabled
         ├── encrypted credentials
         └── non-secret configuration
```

Credentials are represented as a generic key/value structure.

OpenAI currently requires:

```json
{
  "api_key": "..."
}
```

A future provider may instead require:

```json
{
  "api_key": "...",
  "api_token": "...",
  "account_id": "..."
}
```

or:

```json
{
  "client_id": "...",
  "client_secret": "...",
  "region": "..."
}
```

This prevents IG's database schema from being designed around the authentication requirements of any single provider.

---

# Credential encryption

Tenant provider credentials are encrypted before being stored in PostgreSQL.

The deployment requires:

```text
TENANT_SECRET_ENCRYPTION_KEY
```

Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store this value securely in the deployment environment.

Do not commit it to Git.

The encryption key is a platform infrastructure secret.

Individual provider credentials are tenant data and should be configured through the tenant provider administration API.

---

# Model-provider administration API

Tenant administrators can configure model providers.

List providers:

```text
GET /v2/tenant/providers
```

Read one provider:

```text
GET /v2/tenant/providers/{provider}
```

Create or update a provider:

```text
PUT /v2/tenant/providers/{provider}
```

Example:

```text
PUT /v2/tenant/providers/openai
```

```json
{
  "enabled": true,
  "credentials": {
    "api_key": "<openai-api-key>"
  },
  "config": {
    "default_model": "gpt-image-2"
  }
}
```

Credentials are encrypted before persistence.

GET responses expose only safe metadata.

Example:

```json
{
  "provider": "openai",
  "enabled": true,
  "has_credentials": true,
  "config": {
    "default_model": "gpt-image-2"
  }
}
```

Provider credentials are never returned through these APIs.

---

# Multi-tenancy

IG V3 is tenant-aware throughout the backend.

An incoming request resolves approximately as:

```text
hostname
   ↓
TenantDomain
   ↓
Tenant
   ↓
authenticated User
   ↓
TenantMembership
   ↓
TenantContext
```

Because hostname resolution occurs before most tenant-aware operations, every usable tenant requires at least one `TenantDomain` mapping.

Tenant-scoped resources include:

- memberships
- authentication configuration
- provider configuration
- jobs
- media-generation requests
- executions
- artifacts

---

# Authentication and tenant policy

Tenant authentication settings currently include:

```text
allow_self_registration
allow_password_authentication
allow_google_authentication
```

Public self-registration may be enabled or disabled per tenant.

Password and Google authentication can also be enabled or disabled independently, subject to application validation.

Tenant administrators have access to tenant-management operations that ordinary members do not.

---

# Tenant authentication APIs

Public tenant authentication configuration:

```text
GET /v2/tenant/auth-config
```

Administrative authentication-provider configuration:

```text
GET /v2/tenant/auth-providers
PUT /v2/tenant/auth-providers/{provider}
```

Currently recognized authentication providers include:

```text
google
entra
```

Example:

```text
PUT /v2/tenant/auth-providers/google
```

```json
{
  "client_id": "<google-client-id>",
  "issuer": null,
  "metadata": {}
}
```

---

# Tenant settings API

Read tenant settings:

```text
GET /v2/tenant/settings
```

Update tenant settings:

```text
PATCH /v2/tenant/settings
```

Example:

```json
{
  "allow_self_registration": false,
  "allow_password_authentication": true,
  "allow_google_authentication": true
}
```

Admin membership is required.

The current API updates an existing `TenantSettings` row.

It does not currently create the first `TenantSettings` row during installation bootstrap.

---

# Tenant member API

List members:

```text
GET /v2/tenant/members
```

Update member role/status:

```text
PATCH /v2/tenant/members/{user_id}
```

Example:

```json
{
  "role": "admin",
  "status": "active"
}
```

Supported roles:

```text
admin
member
```

Supported statuses:

```text
active
inactive
```

IG prevents removal or deactivation of the tenant's last active administrator.

---

# Tenant invitations

Once the first administrator exists, invitations are the preferred way to add users when public registration is disabled.

Create invitation:

```text
POST /v2/tenant/invitations
```

Example:

```json
{
  "email": "new-user@example.com",
  "role": "member"
}
```

Supported invitation roles:

```text
member
admin
```

During V3 development, the returned invitation response includes the invitation token directly.

Future versions may deliver this token through email or another notification mechanism.

List invitations:

```text
GET /v2/tenant/invitations
```

Revoke pending invitation:

```text
DELETE /v2/tenant/invitations/{invitation_id}
```

Existing authenticated user accepts invitation:

```text
POST /v2/tenant/invitations/accept
```

```json
{
  "token": "<invitation-token>"
}
```

New user registers from invitation:

```text
POST /v2/tenant/invitations/register
```

```json
{
  "token": "<invitation-token>",
  "password": "<password>",
  "name": "Example User"
}
```

This flow creates:

```text
User
TenantMembership
Session
```

---

# Self-registration

When enabled by tenant policy:

```text
POST /v2/auth/register
```

Example:

```json
{
  "email": "user@example.com",
  "password": "<password>",
  "name": "Example User"
}
```

This creates:

```text
User
TenantMembership(role=member)
```

for the tenant resolved from the request hostname.

Self-registration requires:

```text
TenantDomain resolves successfully
TenantSettings exists
allow_password_authentication = true
allow_self_registration = true
```

Therefore self-registration cannot bootstrap an entirely empty IG installation.

---

# Current tenant API

Authenticated clients can inspect their current tenant context:

```text
GET /v2/tenant
```

Example response:

```json
{
  "tenant_id": "...",
  "slug": "example",
  "name": "Example Tenant",
  "status": "active",
  "membership": {
    "role": "admin",
    "status": "active"
  }
}
```

This is useful for verifying:

```text
hostname
→ TenantDomain
→ Tenant
→ TenantMembership
```

resolution.

---

# Deployment

## Recommended V3 architecture

The supported primary deployment architecture is:

```text
Vercel
    React / Vite frontend
    FastAPI backend
    Vercel Queue
    Vercel Blob

Neon
    PostgreSQL

Model providers
    OpenAI
```

Redis/RQ remains available as an alternative queue implementation, but it is not required for the normal Vercel deployment.

The historical Docker deployment remains in the repository for reference but is not treated as the primary or fully validated V3 deployment path.

---

# Deploying IG from scratch

## 1. Fork or clone the repository

```bash
git clone https://github.com/<your-account>/ig.git
cd ig
```

Upstream repository:

```text
github.com/ywptr/ig
```

---

## 2. Create a Vercel project

Import the GitHub repository into Vercel.

IG contains Vercel project configuration for the React/Vite frontend and FastAPI backend.

GitHub pushes may be used as the normal CI/CD workflow.

Typical routing includes:

```text
/health    → FastAPI
/v1/*      → FastAPI
/v2/*      → FastAPI
/*         → frontend
```

---

## 3. Provision PostgreSQL

The recommended database is Neon PostgreSQL.

Configure:

```text
DATABASE_URL
```

Example:

```text
postgresql+psycopg://user:password@host/database?sslmode=require
```

---

## 4. Provision artifact storage

The standard deployment uses Vercel Blob.

Configure:

```text
ARTIFACT_STORE_PROVIDER=vercel
```

The required Vercel Blob credentials should be supplied by the Vercel project/runtime.

---

## 5. Configure the queue

The standard V3 queue is:

```text
JOB_QUEUE_PROVIDER=vercel
```

This does not require Redis or a separately hosted RQ worker.

---

## 6. Configure tenant-secret encryption

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Configure the resulting value as:

```text
TENANT_SECRET_ENCRYPTION_KEY
```

in the deployment environment.

Do not commit the key.

---

## 7. Configure environment variables

Typical V3 production configuration:

```text
DATABASE_URL=<postgresql-url>

TENANT_SECRET_ENCRYPTION_KEY=<fernet-key>

JOB_QUEUE_PROVIDER=vercel

ARTIFACT_STORE_PROVIDER=vercel

LOG_LEVEL=INFO

IG_TRACE_JOBS=false
```

Provider API credentials such as OpenAI API keys do **not** belong in the normal deployment environment.

They are configured per tenant after tenant initialization.

---

## 8. Prepare a development environment

A normal Linux development environment requires:

```text
Git
Python
Node.js / npm
Vercel CLI
```

Create a Python environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install backend dependencies:

```bash
pip install -e .
```

Install frontend dependencies:

```bash
cd frontend
npm install
cd ..
```

---

## 9. Apply database migrations

Ensure `DATABASE_URL` points to the intended database.

Run:

```bash
alembic upgrade head
```

Verify:

```bash
alembic current
```

A fresh database must be migrated before IG can be initialized.

---

## 10. Deploy

The normal V3 workflow uses GitHub-connected Vercel CI/CD.

```bash
git add .
git commit -m "..."
git push
```

Vercel automatically builds and deploys the connected repository.

Manual Vercel deployment may also be used when necessary.

---

# Initial tenant bootstrap

A completely fresh IG installation currently requires an administrative database bootstrap before normal tenant APIs can be used.

This is necessary because the tenant itself is resolved from the request hostname.

The required bootstrap order is:

```text
1. Tenant
2. TenantDomain
3. User
4. TenantMembership with role=admin
5. TenantSettings
```

The first `TenantDomain.hostname` must match the hostname through which the deployment is accessed.

For example, if the deployment is accessed through:

```text
ig.example.com
```

then a corresponding tenant-domain record must exist for:

```text
ig.example.com
```

before tenant-aware APIs can resolve the tenant.

---

## Initial bootstrap API availability

The following first-installation capabilities are **not currently exposed as complete bootstrap APIs**:

| Required object | Creation API available for initial bootstrap? | Notes |
|---|---:|---|
| `Tenant` | No | Must currently be created administratively |
| `TenantDomain` | No | Must exist before hostname-based tenant resolution works |
| Initial admin `User` | No complete bootstrap API | Normal registration requires an already resolved/configured tenant |
| Initial admin `TenantMembership` | No complete bootstrap API | Invitations/member APIs require an existing admin |
| Initial `TenantSettings` | No | Existing settings API updates an existing row |

Therefore the very first tenant requires one administrative database bootstrap operation.

Once these objects exist, normal tenant administration can proceed through application APIs.

---

## Verify initial tenant resolution

After bootstrapping the first tenant and admin, authenticate and call:

```text
GET /v2/tenant
```

This confirms:

```text
hostname
→ TenantDomain
→ Tenant
→ User
→ TenantMembership
```

resolution.

---

## Configure the initial provider

Once the tenant administrator can authenticate, configure OpenAI:

```text
PUT /v2/tenant/providers/openai
```

```json
{
  "enabled": true,
  "credentials": {
    "api_key": "<openai-api-key>"
  },
  "config": {
    "default_model": "gpt-image-2"
  }
}
```

The credential is encrypted before being written to PostgreSQL.

A global `OPENAI_API_KEY` environment variable is not required for the V3 runtime.

---

# Verify the deployment

Open:

```text
https://<production-domain>/health
```

A healthy deployment should return something similar to:

```json
{
  "status": "ok",
  "queue_provider": "vercel"
}
```

Then verify:

1. tenant resolution
2. login
3. standard image generation
4. semantic style
5. artifact upload
6. artifact retrieval
7. image generation using an uploaded reference
8. tenant provider configuration
9. provider generation with no global provider API key configured

---

# Environment variables

The repository includes `.env.example`.

Primary V3 variables include:

```text
DATABASE_URL
TENANT_SECRET_ENCRYPTION_KEY
JOB_QUEUE_PROVIDER
ARTIFACT_STORE_PROVIDER
LOG_LEVEL
IG_TRACE_JOBS
```

Optional queue-development variables include:

```text
REDIS_URL
IG_QUEUE_BASE_URL
```

Provider API credentials should not normally be placed in `.env`.

---

# Job queues

## Vercel Queue

Vercel Queue is the standard V3 execution backend.

```text
FastAPI
   ↓
Vercel Queue
   ↓
subscriber
   ↓
provider adapter
   ↓
artifact persistence
```

Use:

```text
JOB_QUEUE_PROVIDER=vercel
```

---

## Redis / RQ

Redis/RQ remains available as an optional queue implementation.

Use:

```text
JOB_QUEUE_PROVIDER=rq
```

and configure:

```text
REDIS_URL
```

An RQ deployment also requires a persistent RQ worker.

This execution path is retained as an alternative architecture but is not required for the standard V3 deployment.

---

# Artifact storage

Supported storage implementations currently include:

```text
Vercel Blob
local filesystem
```

Use:

```text
ARTIFACT_STORE_PROVIDER=vercel
```

for the standard cloud deployment.

Artifacts expose storage-independent IDs to API consumers.

---

# Legacy Docker deployment

Earlier IG versions used Docker Compose with:

```text
Nginx
FastAPI
MariaDB
Redis / RQ
local artifact storage
```

Docker-related files remain in the repository for historical reference and experimentation.

The Docker deployment is **not currently treated as the primary or fully validated V3 deployment path**.

V3 development and deployment are centered on:

```text
Vercel
Neon
Vercel Queue
Vercel Blob
```

No effort is currently being spent maintaining feature parity between the V3 cloud deployment and the legacy Docker architecture.

---

# Security notes

Do not commit:

```text
DATABASE_URL
TENANT_SECRET_ENCRYPTION_KEY
provider credentials
session credentials
Blob credentials
Redis credentials
```

Tenant provider credentials are encrypted before database persistence.

Provider adapters must be instantiated using credentials belonging to the current tenant.

Provider instances containing tenant credentials must not be globally shared between tenants.

Artifact access is tenant-scoped.

Administrative tenant configuration requires tenant-admin membership.

Provider credential APIs never return stored secrets.

At least one active tenant administrator must be retained.

---

# V3 status

V3 establishes a cloud-hosted, multi-tenant media control plane.

Validated primary runtime:

```text
Vercel
    frontend
    FastAPI
    Queue
    Blob

Neon
    PostgreSQL

OpenAI
    image generation
```

Implemented V3 capabilities include:

- cloud-native runtime
- multi-tenancy
- tenant-domain resolution
- tenant memberships
- tenant-specific authentication policy
- invitations
- Google authentication
- encrypted tenant provider credentials
- provider-neutral media-generation API
- semantic image styles
- reference-image generation
- artifact upload/import
- pluggable provider architecture
- pluggable storage
- pluggable queue execution

The development workstation is not part of the deployed runtime.

---

# V4 / Future wishlist

V3 intentionally stops short of implementing every possible control-plane feature. Its primary goals:
- Multitenant Ready
- Multiprovider Ready
- Capable of single and multipanel image generation
- Dynamic and future-proof media generation API

## Bootstrap and tenant administration

The following currently unexposed or partially exposed administrative capabilities should become first-class platform APIs and/or administrative UI in a future release:

- Create tenant
- Update tenant metadata/status
- Create tenant-domain mapping
- List tenant domains
- Update tenant-domain mapping
- Delete tenant-domain mapping
- Create the initial tenant administrator
- Create an initial tenant membership
- Create initial tenant settings
- Fully API-driven first-installation bootstrap
- Tenant lifecycle administration
- Tenant management UI

These should eventually remove the need for direct database manipulation during first deployment.

---

## Storage

- Google Drive integration
- Google Drive as a tenant-selectable artifact backend
- User-owned Drive storage
- Additional cloud object-storage adapters
- Storage quotas and policies
- Artifact retention policies
- Tenant/shared artifact visibility rules

---

## Model providers

- Additional image provider adapters
- Gemini / Google model adapters
- Other hosted model APIs
- Self-hosted inference providers
- Tenant-provided model endpoints
- BYO model/inference infrastructure
- Video generation providers
- Audio generation providers
- Provider capability discovery
- Provider-specific health/status checks
- Provider fallback/routing policies

---

## Billing and usage

- Usage metering
- Provider-cost accounting
- Platform-managed provider credentials
- BYOK versus managed-provider modes
- Tenant billing
- Usage quotas
- Credit balances
- Plan enforcement
- Regional/commercial billing rules

---

## Authentication and administration

- Richer tenant-administration UI
- Invitation delivery by email (partially completed; need to interface with a proper mail server)
- Additional identity providers and Enterprise SSO
- Domain-based onboarding
- Tenant-level identity policy
- Administrative audit logs
- Session/security management

---

## Artifacts and media

- Tenant-shared artifacts
- Explicit artifact visibility:
  - private
  - tenant
- Reference libraries
- Named characters/styles/assets
- Artifact lineage
- Derived-artifact relationships
- Richer provenance
- Additional image formats where useful
- Video artifacts
- Audio artifacts

---

## Control plane

- Provider capability registry
- Model registry
- Capability routing
- Usage policies
- Quotas
- Execution tracing
- Observability
- Rate limiting
- Runtime policy enforcement
- Integration with authority/policy controls

---

# License

Licensed under the **Apache License 2.0**.

Copyright © 2026 Yogi Wiputra

Repository:

```text
github.com/ywptr/ig
```

See `LICENSE` for the complete license text.

---

# User content

User-provided prompts, uploaded content, and generated content remain the property of their respective users, subject to applicable provider terms, licenses, and law.

IG does not claim ownership of user-generated content.

Users are responsible for ensuring that their inputs and generated outputs comply with applicable law, provider policies, and third-party rights.

---

# Current status

**V3 — Multi-tenant cloud media control plane**

The core V3 platform is operational.
The frontend still requires work and currently is limited to current-tenant functions.