#!/usr/bin/env python3
"""Run from repository root: PYTHONPATH=. .venv/bin/python scripts/cleanup-artifacts.py --dry-run."""
import argparse
import json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--max-items", "--limit", dest="limit", type=int,
                    help="Maximum items per run (default: ARTIFACT_CLEANUP_MAX_ITEMS_PER_RUN or 500)")
parser.add_argument("--batch-size", type=int,
                    help="Candidate page size (default: ARTIFACT_CLEANUP_BATCH_SIZE or 50)")
parser.add_argument("--max-seconds", type=int,
                    help="Stop starting items after this time (default: ARTIFACT_CLEANUP_MAX_SECONDS or 240)")
parser.add_argument("--scan-limit", type=int,
                    help="Run scan budget (default: ARTIFACT_CLEANUP_SCAN_LIMIT or 1000)")
args = parser.parse_args()
from app.db.database import SessionLocal
from app.artifacts.retention import cleanup
with SessionLocal() as db:
    print(json.dumps(cleanup(db, limit=args.limit, batch_size=args.batch_size, max_seconds=args.max_seconds,
                             scan_limit=args.scan_limit, dry_run=args.dry_run), sort_keys=True))
