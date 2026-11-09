"""Read-only report; run through docker compose exec -T ig in development."""
import argparse
import json

from app.db.database import SessionLocal
from app.tenancy.reconciliation import reconcile_tenancy


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only local tenancy/identity readiness report")
    parser.add_argument("--is-issuer", required=True, help="Exact verified IS issuer URL")
    args = parser.parse_args()
    with SessionLocal() as db:
        try:
            print(json.dumps(reconcile_tenancy(db, is_issuer=args.is_issuer), indent=2))
        finally:
            db.rollback()


if __name__ == "__main__":
    main()
