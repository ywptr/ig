"""Offline target checks for administrative writes, never a database connection."""
import argparse
import os
from collections.abc import Mapping

from sqlalchemy.engine import make_url


class UnsafeDatabaseTarget(RuntimeError):
    pass


def database_target_label(database_url: str) -> str:
    """Password/user/query-free confirmation label for a database destination."""
    try:
        url = make_url(database_url)
        host = url.host or "local"
        port = f":{url.port}" if url.port is not None else ""
        return f"{host}{port}/{url.database or ''}"
    except Exception:
        raise UnsafeDatabaseTarget("DATABASE_URL is missing or invalid; value withheld") from None


def require_safe_admin_target(
    database_url: str | None,
    *,
    allow_production_write: bool = False,
    confirm_database_target: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    env = os.environ if environ is None else environ
    label = database_target_label(database_url)
    url = make_url(database_url)
    production = any(env.get(key, "").lower() == "production" for key in (
        "VERCEL_ENV", "VERCEL_TARGET_ENV",
    ))
    # Production metadata takes precedence, even for loopback/forwarded targets.
    # Unknown remote targets require acknowledgment too; no Neon hostname heuristic.
    local = url.get_backend_name() == "sqlite" or (
        url.get_backend_name() in {"postgresql", "mysql", "mariadb"}
        and url.host in {"localhost", "127.0.0.1", "::1", "host.docker.internal", "ig-db"}
    )
    if not production and local:
        return
    if allow_production_write and confirm_database_target == label:
        return
    raise UnsafeDatabaseTarget(
        "Refusing administrative writes: production or unclassified remote database target. "
        f"Target: {label}. Verify the intended database, then explicitly supply both "
        "--allow-production-write and --confirm-database-target with this exact target. "
        "For local development use docker compose -f compose.yaml -f compose.dev.yaml exec ig."
    )


def add_write_confirmation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--allow-production-write", action="store_true",
                        help="Explicitly acknowledge writes to production/remote infrastructure")
    parser.add_argument("--confirm-database-target", help="Exact host[:port]/database being acknowledged")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_write_confirmation_arguments(parser)
    args = parser.parse_args()
    try:
        require_safe_admin_target(
            os.environ.get("DATABASE_URL"),
            allow_production_write=args.allow_production_write,
            confirm_database_target=args.confirm_database_target,
        )
    except UnsafeDatabaseTarget as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
