#!/usr/bin/env python3
"""Bounded video recovery. Run inside the intended backend environment."""
import argparse
import json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--batch-size", type=int)
parser.add_argument("--max-items", type=int)
parser.add_argument("--max-seconds", type=int)
args = parser.parse_args()
from app.db.database import SessionLocal
from app.jobs.video_recovery import recover_video_jobs
with SessionLocal() as db:
    print(json.dumps(recover_video_jobs(db, batch_size=args.batch_size, max_items=args.max_items,
                                      max_seconds=args.max_seconds, dry_run=args.dry_run), sort_keys=True))
