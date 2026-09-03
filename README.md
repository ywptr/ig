# IG — AI Image Generator

**Version 1.0.2**

A small, self-hosted AI image generation control plane.

IG provides a web interface and API for submitting image-generation requests, tracking their execution, storing generation metadata, and serving generated images. V1 currently uses OpenAI as its image-generation provider.

The project is intentionally designed as a foundation for a more general **AI control-plane framework**, rather than as a provider-specific image-generation application.

---

## Overview

IG currently provides:

* A browser-based image generation interface
* Persistent generation history
* Asynchronous image-generation requests
* Generation status tracking
* Generated image storage
* REST API access
* MariaDB/MySQL-compatible persistence
* Docker-based deployment
* Nginx reverse proxy and static frontend hosting
* Separate operational and diagnostic access paths

V1 is intentionally simple and currently supports a single image-generation provider.

---

## Architecture

```text
                    Browser
                       │
                       ▼
                ┌─────────────┐
                │    Nginx    │
                │  React UI   │
                └──────┬──────┘
                       │
                       │ /v1/*
                       ▼
                ┌─────────────┐
                │   FastAPI   │
                │     API     │
                └──────┬──────┘
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
       ┌───────────┐       ┌──────────────┐
       │  MariaDB  │       │   OpenAI     │
       │  Database │       │ Image API    │
       └───────────┘       └──────────────┘
             │
             ▼
       Image artifacts
       /app/output
```

### Components

**Backend**

* Python
* FastAPI
* SQLAlchemy
* Alembic

**Database**

* MariaDB 11
* MySQL-compatible

**Frontend**

* TypeScript
* React
* Vite

**Web server**

* Nginx

**Deployment**

* Docker
* Docker Compose

---

## Features

### Image generation

Submit a prompt through the web UI or REST API.

The backend creates a persistent generation record before contacting the provider and updates the record as generation progresses.

### Persistent history

Each generation records:

* Request ID
* Creation time
* Prompt
* Model
* Status
* Generated filename
* MIME type
* File size
* Generation time

### Asynchronous generation

The browser does not need to remain in a generation state while the provider processes the request.

Once a generation request has been accepted, the backend continues processing it while the UI remains available for additional work.

This is an important architectural distinction: the **control plane owns the lifecycle of the request**, rather than the browser.

### Error handling

Provider and generation failures are recorded against the request where possible, allowing failed operations to remain visible rather than disappearing as transient browser errors.

### API

The current API includes:

```text
POST /v1/images/generations
GET  /v1/images
GET  /v1/images/{request_id}
GET  /v1/images/{request_id}/content
GET  /health
```

The FastAPI application also exposes its OpenAPI documentation.

---

## Deployment

V1 is designed to run as a small Docker Compose deployment.

The production-style operational interface is exposed through Nginx.

The current deployment is **unsecured** and is intended for trusted LAN/private-network use only.

Do not expose the current V1 deployment directly to the public Internet.

A future version will introduce configurable deployment modes and authentication as part of the multi-user/multi-tenant architecture.

Procedure (assuming Git and Docker are already installed and running):

git clone https://github.com/ywptr/ig.git
cd ig
create .env (as per configuration section below)
docker compose build
docker compose up -d
docker compose ps
docker compose exec ig alembic upgrade head
docker compose exec ig alembic current

---

## Configuration

Create a `.env` file containing the required provider and database configuration.

At minimum:

```text
OPENAI_API_KEY=<your-openai-api-key>

IG_DB_PASSWORD=<database-password>
IG_DB_ROOT_PASSWORD=<database-root-password>
DATABASE_URL=mysql+pymysql://ig:<database-password>@ig-db:3306/ig
```

Do not commit `.env` or API credentials to the repository.

---

## API Provider

V1 currently supports:

```text
Provider: OpenAI
Media type: Image
Capability: Image generation
Model: gpt-image-2
```

The provider integration is deliberately isolated from the rest of the application so that additional providers can be introduced later.

---

## Database

MariaDB 11 is used for persistent application metadata.

Alembic manages database schema migrations.

The database stores generation metadata rather than the generated image binary itself.

Generated images are stored separately under:

```text
/app/output
```

The Docker Compose deployment persists both database data and generated artifacts outside the containers.

---

## Operational / Debug Ports

The current development/lab deployment exposes separate paths for operational and diagnostic access.

| Port     | Purpose                | Access                        |
| -------- | ---------------------- | ----------------------------- |
| **8888** | Normal web application | LAN / operational             |
| **8889** | Direct FastAPI backend | Local / debugging             |
| **8890** | phpMyAdmin             | LAN / database administration |

Port **8889** bypasses Nginx and is intended for troubleshooting the FastAPI application.

Port **8890** provides direct database administration through phpMyAdmin.

These ports are development/lab conveniences and should not automatically be exposed in a production deployment.

---

## Development

### Frontend

The frontend is developed using Vite and React.

During development:

```bash
cd frontend
npm install
npm run dev
```

For a production build:

```bash
npm run build
```

The resulting static files are placed in:

```text
frontend/dist
```

The production Docker image builds the frontend and copies the resulting assets into Nginx.

Therefore, Vite is **not required at runtime** for the production deployment.

### Backend

The FastAPI application can be run directly during development with Uvicorn.

The normal deployment uses the Docker image defined by the project.

---

## Design Principle

A central design principle of IG is the separation between **application-specific functionality** and **general control-plane primitives**.

When introducing a new component, we ask:

> Is this infrastructure-specific, or is this a general control-plane primitive?

For example:

### Infrastructure / application domain

```text
image generation
image prompt
image provider
future Zabbix host
future infrastructure event
```

### Control-plane domain

```text
Provider
Model
MediaType
Capability
Workflow
WorkflowStep
Job
Execution
ExecutionTrace
Artifact
Quota
Usage
Policy
Tenant
User
```

This distinction is intended to make the eventual extraction of the control-plane framework possible without coupling it to image generation.

---

## Roadmap

### V2 — Control Plane Foundation

Extract and formalize reusable control-plane primitives.

Planned areas include:

* Multi-provider architecture
* Provider registry
* Model abstraction
* Media-type abstraction
* Capability abstraction
* Execution model
* Workflow and workflow-step model
* Execution tracing
* Artifact management
* Quota and usage primitives
* Multi-tenancy
* Multi-user architecture
* Authentication
* Provider credential management

Authentication is expected to become part of the multi-tenant/multi-user architecture rather than a separate standalone feature.

### V3 — Creative Application Framework

Build the higher-level creative domain on top of the control plane:

```text
Story
 ├── Canon
 ├── Characters
 │    └── Outfits
 ├── Locations
 ├── Assets
 └── Storyboards
```

The objective is to maintain continuity across generations rather than treating each image request as an isolated operation.

### Future — BYO Models

Support multiple forms of Bring Your Own Model:

* External provider APIs
* Tenant-provided API endpoints
* Self-hosted inference endpoints
* Locally hosted models
* Containerized model runtimes
* Potentially tenant-managed inference infrastructure

The control plane should treat these as different execution targets behind a common provider/model abstraction.

### Future — Billing & Publishing

Billing will be introduced only after the multi-tenant and provider abstractions are mature.

The eventual architecture is intended to allow tenant-specific commercial rules, including:

* Platform fees
* Model/inference costs
* Usage-based charging
* Tenant-defined user pricing
* Regional/country-specific billing mechanisms
* Publishing/deployment options

---

## License / Copyright

Licensed under the **Apache License 2.0**.

Copyright © 2026 Yogi Wiputra

Repository:

`github.com/ywptr`

See `LICENSE` for the complete license text.

---

## User Content

User-provided prompts and generated content remain the property of their respective users, subject to the applicable terms, licenses, and policies of the underlying model/provider.

IG does not claim ownership of user-generated content.

Users are responsible for ensuring that their prompts, inputs, and generated content comply with applicable laws, provider terms, and third-party rights.

---

## Status

**V1 — Initial working release**

V1 is a self-hosted laboratory/control-plane implementation intended for experimentation and architectural development.

The architecture will evolve substantially in V2 as reusable control-plane primitives are extracted from the initial application.

