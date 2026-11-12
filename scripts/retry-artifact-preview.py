"""Inspect or retry one explicitly selected failed preview; never call a provider."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ops.database_safety import add_write_confirmation_arguments, require_safe_admin_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--execute", action="store_true", help="Retry the selected failed preview; default is inspection")
    add_write_confirmation_arguments(parser)
    args = parser.parse_args()
    if args.execute:
        require_safe_admin_target(os.environ.get("DATABASE_URL"),
            allow_production_write=args.allow_production_write,
            confirm_database_target=args.confirm_database_target)
    from sqlalchemy import select
    from app.db.database import SessionLocal
    from app.db.models import Artifact, ArtifactPreview
    from app.artifacts.previews import retry_failed_preview, kind_for, VERSION

    with SessionLocal() as db:
        artifact = db.scalar(select(Artifact).where(Artifact.artifact_id == args.artifact_id,
            Artifact.tenant_id == args.tenant_id))
        if artifact is None:
            parser.exit(1, "Artifact not found in selected tenant\n")
        row = db.get(ArtifactPreview, (artifact.artifact_id, kind_for(artifact.media_type, artifact.mime_type), VERSION))
        print(f"artifact_id={artifact.artifact_id} version={VERSION} status={row.status if row else 'missing'}")
        if args.execute:
            if row is None or row.status != "failed":
                parser.exit(1, "Selected preview is not failed; no preview was reset\n")
            row = retry_failed_preview(db, artifact)
            print(f"status={row.status if row else 'missing'}")
            if row is None or row.status != "ready":
                parser.exit(1, "Preview did not become ready; inspect sanitized application diagnostics\n")


if __name__ == "__main__":
    main()
