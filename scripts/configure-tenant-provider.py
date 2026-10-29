import argparse
import json
from getpass import getpass

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import Tenant
from app.tenancy.provider_configs import (
    provider_config_to_dict,
    set_provider_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Configure a model/media provider for an IG tenant."
        )
    )

    tenant_group = (
        parser.add_mutually_exclusive_group(
            required=False
        )
    )

    tenant_group.add_argument(
        "--tenant-id",
        help="Tenant UUID",
    )

    tenant_group.add_argument(
        "--tenant-slug",
        help="Tenant slug",
    )

    parser.add_argument(
        "--provider",
        required=True,
        help=(
            "Provider name, for example 'openai'"
        ),
    )

    parser.add_argument(
        "--disable",
        action="store_true",
        help=(
            "Store the provider configuration as disabled."
        ),
    )

    return parser.parse_args()


def resolve_tenant(
    db,
    *,
    tenant_id: str | None,
    tenant_slug: str | None,
) -> Tenant:
    if tenant_id is not None:
        tenant = db.get(
            Tenant,
            tenant_id,
        )

        if tenant is None:
            raise RuntimeError(
                f"Tenant not found: "
                f"{tenant_id}"
            )

        return tenant

    if tenant_slug is not None:
        tenant = db.scalar(
            select(Tenant).where(
                Tenant.slug
                == tenant_slug
            )
        )

        if tenant is None:
            raise RuntimeError(
                f"Tenant not found: "
                f"{tenant_slug}"
            )

        return tenant

    tenants = list(
        db.scalars(
            select(Tenant)
            .order_by(
                Tenant.created_at.asc()
            )
        ).all()
    )

    if not tenants:
        raise RuntimeError(
            "No tenants exist"
        )

    if len(tenants) > 1:
        raise RuntimeError(
            "Multiple tenants exist. Specify --tenant-id or --tenant-slug."
        )

    return tenants[0]


def read_json_object(
    *,
    prompt: str,
    secret: bool = False,
    allow_empty: bool = True,
) -> dict | None:
    reader = (
        getpass
        if secret
        else input
    )

    raw_value = reader(
        prompt
    ).strip()

    if not raw_value:
        if allow_empty:
            return None

        raise RuntimeError(
            "Value is required"
        )

    try:
        value = json.loads(
            raw_value
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Invalid JSON"
        ) from exc

    if not isinstance(
        value,
        dict,
    ):
        raise RuntimeError(
            "Expected a JSON object"
        )

    return value


def normalize_credentials(
    credentials: dict | None,
) -> dict[str, str] | None:
    if credentials is None:
        return None

    result: dict[str, str] = {}

    for key, value in (
        credentials.items()
    ):
        if not isinstance(
            value,
            (
                str,
                int,
                float,
                bool,
            ),
        ):
            raise RuntimeError(
                "Credential values must be scalar values"
            )

        result[
            str(key)
        ] = str(value)

    return result


def main() -> None:
    args = parse_args()

    provider = (
        args.provider
        .strip()
        .lower()
    )

    if not provider:
        raise RuntimeError(
            "Provider is required"
        )

    print()
    print(
        "Enter provider credentials as JSON."
    )
    print(
        "Example:"
    )
    print(
        '{"api_key":"..."}'
    )
    print(
        "Leave blank to preserve existing credentials."
    )

    credentials = (
        normalize_credentials(
            read_json_object(
                prompt=(
                    "Credentials JSON: "
                ),
                secret=True,
                allow_empty=True,
            )
        )
    )

    print()
    print(
        "Enter non-secret provider configuration as JSON."
    )
    print(
        "Example:"
    )
    print(
        '{"default_model":"gpt-image-2"}'
    )
    print(
        "Leave blank to preserve existing configuration."
    )

    config = read_json_object(
        prompt="Config JSON: ",
        secret=False,
        allow_empty=True,
    )

    db = SessionLocal()

    try:
        tenant = resolve_tenant(
            db,
            tenant_id=args.tenant_id,
            tenant_slug=args.tenant_slug,
        )

        provider_config = (
            set_provider_config(
                db,
                tenant_id=(
                    tenant.tenant_id
                ),
                provider=provider,
                enabled=(
                    not args.disable
                ),
                credentials=credentials,
                config=config,
            )
        )

        result = (
            provider_config_to_dict(
                provider_config
            )
        )

        print()
        print(
            "Provider configured."
        )
        print(
            f"Tenant: "
            f"{tenant.name} "
            f"({tenant.slug})"
        )
        print(
            f"Provider: "
            f"{result['provider']}"
        )
        print(
            f"Enabled: "
            f"{result['enabled']}"
        )
        print(
            f"Has credentials: "
            f"{result['has_credentials']}"
        )
        print(
            "Config:",
            json.dumps(
                result["config"],
                indent=2,
                sort_keys=True,
            ),
        )

    finally:
        db.close()


if __name__ == "__main__":
    main()