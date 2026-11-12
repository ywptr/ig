"""Operator-only daily cron entry point; independent of tenant/browser sessions."""
import hmac
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from app.auth.dependencies import get_db
from app.artifacts.retention import cleanup

router = APIRouter()


@router.get("/internal/artifacts/cleanup", include_in_schema=False)
def scheduled_cleanup(request: Request, db=Depends(get_db)):
    secret = os.getenv("CRON_SECRET")
    if not secret or not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {secret}"):
        raise HTTPException(401, detail="Unauthorized")
    return cleanup(db)
