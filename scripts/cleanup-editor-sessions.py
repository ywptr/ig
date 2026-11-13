"""Remove expired editor delegation rows after seven days; never changes Jobs.

Run with the normal explicit administrative database target safeguards.
"""
import argparse
from datetime import timedelta
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ops.database_safety import add_write_confirmation_arguments, require_safe_admin_target

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_write_confirmation_arguments(parser)
    args = parser.parse_args()
    require_safe_admin_target(os.getenv("DATABASE_URL"), allow_production_write=args.allow_production_write,
                             confirm_database_target=args.confirm_database_target)
    from sqlalchemy import delete, select
    from app.db.database import SessionLocal
    from app.db.models import EditorAttempt, EditorSession
    from app.core.time import utc_now
    with SessionLocal() as db:
        ids = list(db.scalars(select(EditorSession.session_id).where(
            EditorSession.expires_at < utc_now() - timedelta(days=7)).limit(500)))
        if ids:
            db.execute(delete(EditorAttempt).where(EditorAttempt.session_id.in_(ids)))
            db.execute(delete(EditorSession).where(EditorSession.session_id.in_(ids)))
            db.commit()
        print(f"Removed {len(ids)} expired editor sessions; Jobs and Artifacts retained")

if __name__ == "__main__":
    main()
