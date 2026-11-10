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

For fresh installs, first run the [tenant bootstrap and first-admin commands](tenant-bootstrap.md).
Both modes use the same safeguards described below, including `--dry-run`.

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

## Temporary-Shell Production Migration Procedure

This is an approved alternative operational procedure for an intentional production
migration. Production intent is established by a separate shell, an explicit
Production environment pull, masked target verification, migration-state
inspection, and immediate shell exit. It does not change or bypass the existing
database safeguards. The existing target acknowledgment is already
password/user/query-free; neither procedure requires manually providing or
confirming a database username or password. Secret handling, not repeating a
credential, protects those values.

1. **Open a separate temporary terminal.** Do not reuse the normal IG development
   shell. Dedicate this terminal solely to the production operation and exit it
   immediately afterward. Start without inherited cloud/database exports; close
   or clear other shells containing stale exports before ordinary development.
   Disable shell tracing or command recording that could expose sourced secrets.

2. **Enter the IG repository.**

   ```bash
   cd ~/src/ig
   ```

3. **Confirm the checkout and migrations.**

   ```bash
   git status --short
   git rev-parse --short HEAD
   ```

   Know exactly which reviewed checkout and migration files will be applied.
   Stop if the working tree contains unreviewed changes; do not migrate from an
   unreviewed working tree.

4. **Explicitly pull the Vercel Production environment.** Verify that the repository
   is linked to the intended Vercel project/team. If linking is required first:

   ```bash
   vercel link
   ```

   Confirm the production filename is ignored before creating it:

   ```bash
   git check-ignore --quiet .env.production.local
   ```

   Stop if that check fails. Pull into the visually distinct production file:

   ```bash
   umask 077
   vercel env pull .env.production.local --environment=production
   chmod 600 .env.production.local
   ```

   > **Warning:** For this procedure, never use an environment-defaulting
   > `vercel env pull` command. Use exactly
   > `vercel env pull .env.production.local --environment=production`.
   > Do not omit `--environment=production` or substitute `.env.local`.

   `.env.production.local` must remain Git-ignored, secret, and uncommitted.
   Vercel may omit Production values stored as Secrets from an environment pull.
   If required values are unavailable, stop; do not substitute development values,
   inherited credentials, or a different environment.

5. **Load the production values only into this shell.** Clear the inherited target
   and deployment markers so missing pulled values cannot silently reuse them:

   ```bash
   unset DATABASE_URL VERCEL_ENV VERCEL_TARGET_ENV
   set -a
   source .venv/bin/activate
   source .env.production.local
   set +a
   export VERCEL_ENV=production
   ```

   Stop if activation or sourcing fails, or the required `DATABASE_URL` is absent.
   The explicit production marker also preserves the safeguard for tunneled
   targets. These exports apply only to this terminal and its child processes;
   they do not configure other shells and disappear when this shell exits.
   Never print the environment, file contents, raw URL, or credentials.

6. **Verify the masked target.** This check creates the engine without opening a
   database connection. Mask the username and password and omit query parameters:

   ```bash
   python - <<'PY'
   from app.db.database import engine
   print(engine.url.set(username="***", query={}).render_as_string(hide_password=True))
   PY
   ```

   Verify that the hostname is the expected production database, the database name
   is correct, the target is not `ig_dev`, and credentials remain masked. Stop on
   any mismatch. Do not enable password output.

   Capture the credential-free target label from that same environment for the
   existing Alembic safeguard; do not retype a username or password:

   ```bash
   ig_production_migration_target=$(python - <<'PY'
   import os
   from app.ops.database_safety import database_target_label
   print(database_target_label(os.environ["DATABASE_URL"]))
   PY
   )
   readonly ig_production_migration_target
   ```

   Stop if capture fails or the label is empty. Do not change the loaded database
   target after verification.

7. **Inspect migration state before writing.**

   ```bash
   alembic current
   alembic heads
   ```

   Verify the current production revision, the reviewed repository head, and the
   expected migration path. If any result is unexpected, stop rather than
   proceeding automatically.

8. **Deliberately apply the migration.** An unwrapped `alembic upgrade head` still
   refuses production writes under the existing safeguard. Define this temporary,
   shell-local wrapper to supply its existing acknowledgment arguments using the
   verified credential-free label:

   ```bash
   alembic() {
     command alembic -x allow-production-write=yes \
       -x "confirm-database-target=${ig_production_migration_target}" "$@"
   }
   ```

   This wrapper changes no application, Alembic, script, or safeguard files. The
   safeguard still checks the actual URL against the captured target. Only after
   completing the checks above, explicitly run:

   ```bash
   alembic upgrade head
   ```

   Stop on failure; do not automatically downgrade, stamp, or retry against a
   different target.

9. **Verify completion.**

   ```bash
   alembic current
   ```

   Confirm production is at the intended repository head. Investigate any mismatch
   rather than assuming the migration succeeded.

10. **Exit immediately.**

    ```bash
    exit
    ```

    Do not continue ordinary development in this terminal. Its lifecycle is part
    of the safety boundary: exiting destroys the production-loaded shell context
    and its temporary wrapper. It does not delete `.env.production.local`; that
    file remains a protected, untracked secret and must never enter Git,
    documentation, command output, or logs.

Normal development administration still belongs in the development container/local
`ig_dev` environment described above. Production credentials must not remain loaded
in normal development shells. This procedure replaces manual target-label entry
with deliberate session isolation and verification, not with weaker credential
handling or a change to IG's production-write checks.
