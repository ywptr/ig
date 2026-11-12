#!/usr/bin/env python3
"""Run from repository root: PYTHONPATH=. .venv/bin/python scripts/cleanup-artifacts.py --dry-run."""
import argparse
import json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--limit", type=int, default=50)
parser.add_argument("--scan-limit", type=int, default=1000)
args = parser.parse_args()
from app.db.database import SessionLocal
from app.artifacts.retention import cleanup
with SessionLocal() as db:
    print(json.dumps(cleanup(db, limit=args.limit, scan_limit=args.scan_limit, dry_run=args.dry_run), sort_keys=True))
