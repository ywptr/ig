# Local Docker development on 5480-deb13

**Docker is the canonical local runtime. Do not run database-backed administrative
commands casually in the host `.venv`; inherited cloud environment variables can
target production.** Run them inside `ig`. See
[database operations and safeguards](database-operations.md) for target verification,
interactive `exec` versus noninteractive `exec -T`, and deliberate production procedures.

The development stack uses Docker for the applications and the VM's existing
infrastructure:

- Database: host PostgreSQL 17, database and role `ig_dev`.
- Queue: host Redis with authentication, logical database 1, RQ queue `ig`.
- Artifacts: local filesystem, shared `./output` bind mount.
- Containers: `ig-web` (Vite), `ig` (FastAPI), `ig-worker` (RQ).

Prerequisites are Docker Engine, Docker Compose 2.24.4+ (for `!override`/`!reset`),
host PostgreSQL, host Redis, and the persistent external Docker network
`platform-dev`. On this VM Docker 29.9.3 and Compose v5.7.0 are installed.
The historical base Compose and production Dockerfiles remain unchanged. The
development-only backend `Dockerfile.dev` installs `pyproject.toml` dependencies,
including PyJWT for OIDC and the pinned Vercel packages.

## Local configuration

API and worker read the existing Git-ignored `.env`, not `.env.local`. Keep `.env`
owner-readable only (`chmod 600 .env`). Do not commit credentials, display a resolved
Compose configuration containing them, or pass credentials in shell arguments.

Required settings (supply credentials securely in `.env`, never in this document):

- `IG_DEV_DATABASE_URL`: local Docker database selection; keep the local
  `DATABASE_URL` consistent for explicitly selected tooling. SQLAlchemy `postgresql+psycopg` URL for host
  `host.docker.internal`, port 5432, database/role `ig_dev`.
- `REDIS_URL`: authenticated Redis URL for `host.docker.internal:6379/1`.
  URL-encode special characters in credentials.
- `TENANT_SECRET_ENCRYPTION_KEY`: a local Fernet key; preserve an existing local key
  if existing encrypted local provider configuration must remain readable.
- `IG_DEV_NETWORK=platform-dev`.
- `IG_DEV_HOST_GATEWAY`: the persistent `platform-dev` gateway that the host
  PostgreSQL/Redis services listen on. This VM's private gateway is configured in
  local `.env`; application code and committed Compose do not hard-code that IP.

The override adds `host.docker.internal` to API/worker via `extra_hosts`. It defaults
to Docker's `host-gateway` mechanism when `IG_DEV_HOST_GATEWAY` is absent. On this VM,
the explicit gateway override is necessary because the existing services listen
on `platform-dev`, rather than Docker's default bridge. This avoids changing the
Docker daemon or exposing host databases on public interfaces. Verify the network's
persistent gateway with `docker network inspect platform-dev` when moving hosts.

PostgreSQL must allow password authentication for `ig_dev` from this private Docker
subnet in `pg_hba.conf`; never use broad trust rules. Redis remains authenticated,
with protected mode enabled and bindings restricted to loopback/private gateway.
Do not flush shared Redis. Database 1 separates this development queue from the
default database, but is not an ACL/security boundary.

The override explicitly selects `JOB_QUEUE_PROVIDER=rq` and
`ARTIFACT_STORE_PROVIDER=local` for both applications. The worker CLI receives the same URL through `RQ_REDIS_URL`; application code
uses `REDIS_URL`. Credentials do not appear in command arguments.
Do not append `compose.cloud.yaml` for this local workflow: that file selects an
external cloud database. Historical `ig-db`, `ig-redis`, and phpMyAdmin definitions
remain available only under the `legacy-infrastructure` profile; do not enable it
for the host-infrastructure workflow.

## Development HTTPS

Vite terminates trusted HTTPS on port 5181 for both the frontend and same-origin
browser API calls. FastAPI stays HTTP internally at `ig:8000`; port 8011 remains a
direct HTTP diagnostic endpoint. No new reverse proxy or production change is
needed. Port 5181 now requires HTTPS rather than HTTP.

The existing mkcert CA signs a separate IG leaf certificate, stored outside the
repository under `/home/user/certs/ig`. The local ignored `.env` provides
`IG_DEV_TLS_CERT_FILE` and `IG_DEV_TLS_KEY_FILE`. Compose mounts only those leaf
files read-only into Vite, with missing source files treated as configuration
errors. The leaf private key is owner-only; no CA private key is mounted or copied.
`IG_DEV_TLS_CERT` and `IG_DEV_TLS_KEY` are the container-side paths consumed by
Vite. Without these variables, ordinary Vite/production builds remain unchanged.

The certificate covers `5480-deb13.lan.com`, `localhost`, `192.168.0.99`, and
`127.0.0.1`. To issue it again using the existing CA:

```sh
mkdir -p /home/user/certs/ig
mkcert -cert-file /home/user/certs/ig/dev-cert.pem -key-file /home/user/certs/ig/dev-key.pem 5480-deb13.lan.com localhost 192.168.0.99 127.0.0.1
chmod 600 /home/user/certs/ig/dev-key.pem
```

Keep the public development root trusted on each browser/client. Some Linux
browsers require the same public CA in their NSS store as well as the system store.
Never disable certificate verification, and never distribute the CA private key.
Resolve the exact registered hostname to the VM IP on browser clients; container
`extra_hosts` mappings do not provide browser DNS. Restart `ig-web` after renewing
the mounted leaf files. HMR uses WSS on the same HTTPS connection.

## Start and stop

From the repository root:

```sh
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
docker compose -f compose.yaml -f compose.dev.yaml ps
docker compose -f compose.yaml -f compose.dev.yaml logs -f ig ig-web ig-worker
```

If the current login predates Docker group membership, open a new login session.
For this VM, an authorized temporary alternative is:

```sh
sg docker -c 'docker compose -f compose.yaml -f compose.dev.yaml up --build -d'
```

Docker group membership grants privileged host access; the daemon has no remote
TCP listener. Do not weaken socket permissions.

URLs:

- Registered development origin: https://5480-deb13.lan.com:5181
- Frontend: https://localhost:5181
- Browser API/health: https://5480-deb13.lan.com:5181/health (API routes under `/v1` and `/v2`)
- Direct diagnostic API (HTTP): http://localhost:8011
- Health: http://localhost:8011/health
- API documentation: http://localhost:8011/docs
- LAN frontend: https://192.168.0.99:5181
- LAN API: http://192.168.0.99:8011

Ports 5173 (Vite) and 8000 (Uvicorn) remain internal. The historical application
bindings on 8888/8889 are replaced. Application ports bind to loopback and the VM LAN address `192.168.0.99` for
access from `192.168.0.0/24`. The VM currently obtains this address through DHCP;
keep it reserved, or update the development port bindings if the address changes.
TenantDomain mappings exist for `localhost`, `192.168.0.99`, and `5480-deb13.lan.com`. Browser API
requests use the same-origin Vite proxy on either address. IS login still requires
a compatible registered callback and browser cookie/HTTPS configuration.

Apply migrations only after verifying the target is the local development database:

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec ig alembic upgrade head
docker compose -f compose.yaml -f compose.dev.yaml exec ig alembic current
```

No production data is copied. The migrations create a default tenant, but a usable
tenant still needs a `TenantDomain` for `localhost`, admission settings and IS client
configuration. See the main README for bootstrap. IS development login uses the
registered callback `https://5480-deb13.lan.com:5181/v2/auth/is/callback`; do not use production
IS client credentials. Health alone does not verify database access or login.

Stop without deleting data:

```sh
docker compose -f compose.yaml -f compose.dev.yaml down
```

The shared external network and host PostgreSQL/Redis are not removed. Do not add
`--volumes` unless deliberately discarding the frontend dependency volume.

## Source edits and dependencies

Frontend source is mounted at `/app`; a named volume at `/app/node_modules` keeps
container-installed dependencies visible. Vite uses native filesystem watching and
HMR over port 5181, without polling. TSX/TS/CSS edits do not require rebuilds.

Python source is mounted at `/app/app`. Uvicorn uses `--reload --reload-dir /app/app`;
Python edits restart the API process, without rebuilding/recreating its container.
RQ workers do not automatically reload already imported application modules.
After worker-related Python edits, use:

```sh
docker compose -f compose.yaml -f compose.dev.yaml restart ig-worker
```

Backend dependency/Dockerfile changes require `up --build -d`. After frontend
package/lockfile changes, refresh the named dependency volume as well as the image:

```sh
docker compose -f compose.yaml -f compose.dev.yaml stop ig-web
docker compose -f compose.yaml -f compose.dev.yaml build ig-web
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps ig-web npm ci
docker compose -f compose.yaml -f compose.dev.yaml up -d ig-web
```

## Proxy and smoke checks

Browser API requests retain relative URLs. Vite proxies `/v1`, `/v2`, and `/health`
to `http://ig:8000`, preserving Host for tenant resolution. No CORS change is needed.
Nginx is outside this development path; its historical static-upstream IP caching
cannot affect it. If deliberately using the historical Nginx setup, reload/recreate
Nginx after backend recreation to avoid stale resolution.

```sh
curl --fail http://localhost:8011/health
curl --fail https://localhost:5181/health
docker compose -f compose.yaml -f compose.dev.yaml exec ig python -c 'from app.db.database import engine; from sqlalchemy import text; c = engine.connect(); assert c.execute(text("SELECT current_database()")).scalar() == "ig_dev"; c.close(); print("Database OK")'
docker compose -f compose.yaml -f compose.dev.yaml exec ig python -c 'from redis import Redis; import os; assert Redis.from_url(os.environ["REDIS_URL"]).ping(); print("Redis OK")'
docker compose -f compose.yaml -f compose.dev.yaml logs ig-worker
```

The worker should listen on `ig`. Check browser DevTools for the Vite WebSocket,
API responses, and proxy errors. Temporarily edit a visible frontend string and
verify HMR, then revert. Temporarily add a Python comment under `app/`, verify the
Uvicorn reload log and health response, then revert. Container IDs must stay the
same across these tests. No billable provider calls are needed.

## Validation on 2026-11-07

The actual VM stack was started with only `ig`, `ig-web`, and `ig-worker`.
PostgreSQL 17.11 and Redis 8.0.2 remained host services; their configuration was
not changed. Redis protected mode remained enabled. Private-network authenticated
access was verified from client containers and from both application containers.
All Alembic migrations completed on the empty local database without edits;
rollback-only CRUD checks passed, and `localhost` was mapped to the migration's
default development tenant.

The safe RQ arithmetic job completed through the real worker; only its own job
record was deleted. Local artifact write/read/cleanup passed. Frontend, direct and
proxied health, and proxied tenant-auth configuration all returned HTTP 200.
A browser verified the Vite WebSocket and a visible heading update/revert without
page navigation or frontend container recreation. Uvicorn reloaded after a Python
comment was added/reverted, using distinct child processes in the same container.
All three containers remained running with zero container restarts. No application
ports were published on host 5173 or 8000.

Backend discovery passed all 185 tests both in the host virtual environment and
inside the actual development image. Frontend build (including TypeScript checks)
passed; lint completed with its existing `AuthScreen.tsx` effect warning.
`git diff --check` passed. Temporary source edits were reverted. No live model
provider calls or production database access were used.

Shared-library packages for the temporary browser check were unpacked under `/tmp`
without system package installation. Privileged firewall/pg_hba file inspection
was unavailable in this session; actual authenticated private-network access was
verified instead. Full IS login still requires a configured development IS client;
provider generation additionally requires tenant-specific local provider setup.

For the complete IG OIDC contract, verified development discovery, and pending IS
client registration, see [IS/OIDC bootstrap](is-oidc-development.md).

HTTPS was subsequently verified with system certificate/hostname validation on
localhost, the LAN IP and the registered hostname. A Chromium browser with the
existing public CA trusted verified HTTPS rendering, API proxy, and visible WSS
HMR update/revert without page navigation or container recreation. Browser DNS was
explicitly mapped for that test; real client machines still need hostname resolution.
