# Database operations: development and production boundary

Docker containers are the canonical IG local development runtime. Host `.venv`
execution is for isolated tests and tooling, not database-backed development
administration. A host shell can retain cloud credentials after sourcing a file or
running cloud tooling. Activating a Python virtual environment does not select a
database. Never infer the target from the current directory or active virtualenv.

## Verify before operating

Use the running development API container. This command prints a password-masked
SQLAlchemy URL without opening a database connection:

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec -T ig \
python - <<'PY'
from app.db.database import engine
print(engine.url.render_as_string(hide_password=True))
PY
```

Expect the local PostgreSQL database `ig_dev` through `host.docker.internal`.
Do not use a production/cloud URL for a development operation. Do not print a raw
`DATABASE_URL`, a resolved full Compose configuration, or an environment dump.

## Canonical development administration

Keep the default interactive TTY for credential prompts:

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec ig \
  python scripts/configure-tenant-provider.py \
  --tenant-slug default \
  --provider openai
```

Provider credential JSON is read with a hidden prompt. Do not pipe secrets through
shell command arguments. The development Compose mounts `scripts/` read-only into
`ig`; no script copy/rebuild is needed for ordinary edits.

Use `exec -T` only for piped/heredoc/noninteractive stdin. For example, the read-only
reconciliation report can run noninteractively:

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec -T ig \
  python scripts/audit-tenancy.py --is-issuer '<verified-issuer>'
```

Development migrations:

```sh
docker compose -f compose.yaml -f compose.dev.yaml exec ig alembic current
docker compose -f compose.yaml -f compose.dev.yaml exec ig alembic upgrade head
```

## Environment separation

- `.env` contains the local Docker settings and is Git-ignored and owner-only.
- `IG_DEV_DATABASE_URL` in `.env` selects the database for **both** API and worker.
  It is deliberately separate from the commonly inherited `DATABASE_URL`; exporting
  a cloud `DATABASE_URL` in the host shell no longer changes the worker target.
- The old `.env.local` symlink exported shared production Vercel values, including
  `DATABASE_URL`, and was automatically sourced by the legacy cloud launchers. It
  has been renamed to `.env.cloud-operations`, an explicit operations filename.
  The shared parent file itself was not moved, edited, or deleted because other
  projects may rely on it. The renamed symlink is also ignored by Git.
- Existing shells/processes retain exported variables. Open a clean shell or unset
  cloud database/deployment variables before normal development. This task cannot
  remove environment variables from another already-running shell. Do not source
  the cloud operations file during ordinary development.
- `app.db.database` reads `os.environ['DATABASE_URL']`; it does not load dotenv files
  or connect when creating its engine. `app.main` calls `load_dotenv()` without
  overriding existing exports. Thus previously exported values can win over `.env`.
- Vercel production receives platform-managed environment variables. Those settings
  and application request/worker behavior are unchanged. `compose.cloud.yaml`
  deliberately selects `CLOUD_DATABASE_URL` when explicitly included; it is not part
  of the canonical local command.

## Write safeguards

`app/ops/database_safety.py` checks a target **without connecting**. The provider
configuration script refuses before engine import, connection, or credential
prompts. Online Alembic writes (upgrade, downgrade, stamp, and unclassified
programmatic operations) check before connection. CLI `current`, `check`, and
revision diagnostics, plus offline `--sql` generation, are not blocked. The
read-only tenancy audit does not require a write override.

Existing Vercel metadata is authoritative: either `VERCEL_ENV=production` or
`VERCEL_TARGET_ENV=production` requires an explicit production acknowledgment,
even through a localhost database tunnel. Without that marker, only known local
SQLAlchemy SQLite/loopback/Docker service destinations are automatically allowed.
Unknown remote databases also require acknowledgment, including unlabelled Neon
URLs. This does not rely on matching a Neon hostname substring.

The override requires two explicit arguments and an exact password/user/query-free
`HOST[:PORT]/DATABASE` confirmation. A boolean environment flag is insufficient.
These checks protect administrative entry points, not arbitrary Python/SQL or
application API writes. A loopback tunnel without deployment metadata cannot be
identified as production solely from its URL; keep production metadata in trusted
operations environments and verify the target. This is an operational safeguard,
not a database authorization boundary.

## Deliberate production operations

Use an isolated trusted operations session with the intended platform-managed
`DATABASE_URL`, appropriate encryption key, and production deployment metadata.
Verify the effective target before making changes. Do not put credentials into
commands or documentation. Production credentials must not be needed for normal
local development.

An intentionally selected remote/production provider operation uses:

```sh
python scripts/configure-tenant-provider.py \
  --tenant-slug '<tenant>' --provider openai \
  --allow-production-write \
  --confirm-database-target '<verified-host[:port]/database>'
```

Production migrations use Alembic's `-x` arguments **before** its command:

```sh
alembic -x allow-production-write=yes \
  -x 'confirm-database-target=<verified-host[:port]/database>' upgrade head
```

Do not append these flags to routine development commands. No production
migration or provider configuration was executed while implementing these checks.

Legacy cloud launchers can start consumers and therefore mutate their selected
database. They now require an explicit cloud file selection and the same write
check before starting services. For an intentional cloud operation:

```sh
IG_CLOUD_ENV_FILE=.env.cloud-operations bash scripts/dev-cloud-redis.sh \
  --allow-production-write \
  --confirm-database-target '<verified-host[:port]/database>'
```

The three legacy cloud launchers remain separate from Docker development and
retain their existing runtime/tool prerequisites. Cloud file selection is not a
recommendation to run production workers casually on this VM.
