"""Independent operator-only video reconciliation schedule."""
import hmac
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from app.auth.dependencies import get_db
from app.jobs.video_recovery import recover_video_jobs

router = APIRouter()


@router.get("/internal/video/recover", include_in_schema=False)
def scheduled_video_recovery(request: Request, db=Depends(get_db)):
    secret = os.getenv("CRON_SECRET")
    if not secret or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {secret}"):
        raise HTTPException(401, detail="Unauthorized")
    return recover_video_jobs(db)
