# Local Docker development

Use Docker Engine and Docker Compose **2.24.4 or newer** (required for `!override`).
Run all commands from the repository root. Production remains the existing Vercel
path; the production Dockerfiles and historical base Compose configuration are unchanged.

## Configuration

The base Compose file reads `.env` for the API and worker. It does **not** load
`.env.local` into those containers. Use local-development credentials in `.env`;
do not copy production credentials into the frontend or commit secrets.

For the existing local MariaDB service, configure these values in `.env`:

```dotenv
IG_DB_PASSWORD=<local database password>
IG_DB_ROOT_PASSWORD=<local database root password>
DATABASE_URL=mysql+pymysql://ig:<URL-encoded local database password>@ig-db:3306/ig
TENANT_SECRET_ENCRYPTION_KEY=<local Fernet key>
```

Generate the encryption key using the instructions in the main README. Preserve
an existing local key if you need to read previously encrypted local credentials.
MariaDB initialization passwords only apply to an empty data directory; use the
credentials already associated with an existing `db-data` directory.

The override selects `JOB_QUEUE_PROVIDER=rq`, `REDIS_URL=redis://ig-redis:6379/0`,
and `ARTIFACT_STORE_PROVIDER=local` for both API and worker. Both share `./output`.
MariaDB data (`./db-data`) and Redis data (`./redis-data`) retain their existing mounts.
No services or data are removed. phpMyAdmin retains its historical port `8890`.

An existing PostgreSQL development database can still be selected through the
existing cloud override, appended **last**, with `CLOUD_DATABASE_URL` configured:

```sh
docker compose -f compose.yaml -f compose.dev.yaml -f compose.cloud.yaml up --build -d
```

Use only an intended development database. No migration is run automatically.
The MariaDB service remains present with this override, as in the existing setup.

## Start and stop

```sh
docker compose -f compose.yaml -f compose.dev.yaml config --quiet
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
docker compose -f compose.yaml -f compose.dev.yaml ps
docker compose -f compose.yaml -f compose.dev.yaml logs -f ig ig-web ig-worker
```

Open:

- Frontend: http://localhost:5181
- API: http://localhost:8011
- Health: http://localhost:8011/health
- API documentation: http://localhost:8011/docs

Vite listens inside Docker on `5173`; Uvicorn listens on `8000`. Neither port is
published on the host. `!override` replaces the historical `8888`/`8889` mappings
rather than adding more port bindings. Bindings are localhost-only.

Before first use, wait for MariaDB readiness in its logs, then apply migrations
to the intended **development** database:

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec ig alembic upgrade head
```

Follow the main README's tenant bootstrap instructions. A `TenantDomain` matching
`localhost` is required for tenant-aware requests. Health does not test database
connectivity. IS login also requires a development IS client with the callback
`http://localhost:5181/v2/auth/is/callback` registered and configured in `.env`.
Use `localhost` consistently for browser cookies. Login/provider configuration
still requires the existing application setup; the override does not provision it.

Stop using the same file selection:

```sh
docker compose -f compose.yaml -f compose.dev.yaml down
```

Do not add `--volumes` unless deliberately discarding named development volumes.
The existing fixed container names mean this stack cannot run simultaneously
with another copy of the historical IG stack using those names.

## Source edits and dependencies

Frontend source is bind-mounted at `/app`. A named volume at `/app/node_modules`
keeps container dependencies visible even if the host has its own dependencies.
Native filesystem watching is used, without polling. TSX/TS/CSS edits use Vite
HMR over the frontend connection on port `5181`; no custom HMR host port is needed.

The existing backend mount exposes `./app` at `/app/app`. Uvicorn runs with
`--reload --reload-dir /app/app` and detects Python changes without an image rebuild.
The RQ worker also sees source changes but should be restarted after Python edits
to clear already imported worker modules:

```sh
docker compose -f compose.yaml -f compose.dev.yaml restart ig-worker
```

Rebuild after backend dependency or Dockerfile changes (`up --build -d`). After
frontend dependency/lockfile changes, refresh the dependency volume and rebuild
its image so future new volumes also contain the current dependencies:

```sh
docker compose -f compose.yaml -f compose.dev.yaml stop ig-web
docker compose -f compose.yaml -f compose.dev.yaml build ig-web
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps ig-web npm ci
docker compose -f compose.yaml -f compose.dev.yaml up -d ig-web
```

## Proxy and validation

Browser API requests retain their existing relative URLs. In this override Vite
proxies `/v1`, `/v2`, and `/health` to `http://ig:8000`, preserving the browser Host
for tenant resolution. This avoids cross-origin API calls and requires no CORS
change. Docker service names/internal ports are used inside the network.
Nginx is outside this development path, so its historical static upstream IP
resolution cannot affect this workflow. The unchanged historical Nginx configuration
can still retain an old backend IP; reload/recreate Nginx if using that older setup.

Check both direct and proxied API connectivity:

```sh
curl --fail http://localhost:8011/health
curl --fail http://localhost:5181/health
docker compose -f compose.yaml -f compose.dev.yaml exec ig python -c 'from app.db.database import engine; from sqlalchemy import text; connection = engine.connect(); assert connection.execute(text("SELECT 1")).scalar() == 1; connection.close(); print("Database OK")'
docker compose -f compose.yaml -f compose.dev.yaml exec ig-redis redis-cli ping
docker compose -f compose.yaml -f compose.dev.yaml exec ig python -c 'from redis import Redis; import os; assert Redis.from_url(os.environ["REDIS_URL"]).ping(); print("Redis OK")'
docker compose -f compose.yaml -f compose.dev.yaml logs ig-worker
```

The worker should register and listen on queue `ig`. Check browser DevTools for a
successful Vite WebSocket connection and successful API responses. Edit a visible
frontend string and verify HMR, then revert the edit. Add and remove a harmless
Python comment under `app/` and check the Uvicorn reload logs after each edit.
Check both health URLs again, and verify tenant-aware UI calls with a configured
tenant. Review logs for reload errors, proxy failures, and HMR errors. These checks
do not require submitting a billable generation request.

The container validation requires a working local Docker daemon. A successful
frontend build alone does not establish container networking or HMR correctness.
