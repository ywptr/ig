"""Register/revoke service PUBLIC keys. Uses existing administrative target checks."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ops.database_safety import add_write_confirmation_arguments, require_safe_admin_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--service-id")
    parser.add_argument("--public-key-file", type=Path)
    parser.add_argument("--scope", action="append", default=[])
    parser.add_argument("--tenant-id", action="append", default=[])
    parser.add_argument("--actor-issuer", action="append", default=[])
    parser.add_argument("--editor-origin", action="append", default=[])
    add_write_confirmation_arguments(parser)
    args = parser.parse_args()
    try:
        require_safe_admin_target(os.environ.get("DATABASE_URL"),
            allow_production_write=args.allow_production_write, confirm_database_target=args.confirm_database_target)
        from app.db.database import SessionLocal
        from app.db.models import ServiceCredential
        from app.ops.service_credentials import register_credential
        with SessionLocal() as db:
            if args.disable:
                row = db.get(ServiceCredential, args.key_id)
                if row is None:
                    raise ValueError("Unknown key identity")
                row.enabled = False
            else:
                if not args.service_id or not args.public_key_file:
                    raise ValueError("Service ID and public key file are required")
                register_credential(db, key_id=args.key_id, service_id=args.service_id,
                    public_key=args.public_key_file.read_bytes(), scopes=args.scope,
                    tenant_ids=args.tenant_id, actor_issuers=args.actor_issuer, editor_origins=args.editor_origin)
            db.commit()
    except Exception:
        # Do not serialize SQL/library exceptions or key material to the terminal.
        parser.exit(2, "Service credential operation failed; check arguments and database target.\n")
    print("Service credential configuration updated.")


if __name__ == "__main__":
    main()
