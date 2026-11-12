"""Eager, bounded and durably cached mechanical Artifact derivatives."""
from datetime import timedelta
from app.artifacts.orphans import track_write
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from uuid import uuid4

from sqlalchemy import inspect, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.artifacts.preview_policy import (
    VERSION, PROCESS_TIMEOUT, MAX_IMAGE_BYTES, MAX_VIDEO_BYTES, MAX_PREVIEW_BYTES, kind_for,
)
from app.artifacts.storage.provider import artifact_store
from app.core.time import utc_now
from app.db.models import Artifact, ArtifactPreview

logger = logging.getLogger(__name__)

SLOTS = BoundedSemaphore(2)
LEASE_SECONDS = 120
MAX_ATTEMPTS = 2  # Only expired/crashed claims can be retried; failures are cached.
ERROR_CODES = {"SOURCE_SIZE_LIMIT", "PROCESSING_TIMEOUT", "PROCESSING_FAILED"}


class PreviewFailure(Exception):
    def __init__(self, code, *, exception_class=None, returncode=None):
        super().__init__(code)
        self.exception_class = exception_class
        self.returncode = returncode


def diagnostic(artifact_id, media_type, stage, outcome, exc=None):
    # Raw exception text can contain signed URLs, paths or credentials. Use an
    # allowlisted message, retaining the class and stage for investigation.
    message = "ok"
    if exc:
        message = str(exc) if isinstance(exc, PreviewFailure) and str(exc) in ERROR_CODES else "operation failed; details withheld"
    if isinstance(exc, PreviewFailure) and isinstance(exc.returncode, int):
        message += f"; decoder_returncode={exc.returncode}"
    logger.log(logging.WARNING if exc else logging.INFO,
        "artifact_preview artifact_id=%s media_type=%s version=%s stage=%s outcome=%s exception_class=%s message=%s",
        artifact_id, media_type, VERSION, stage, outcome,
        (getattr(exc, "exception_class", None) or type(exc).__name__) if exc else "none", message)



def preview_metadata(artifact: Artifact, row: ArtifactPreview | None = None) -> dict:
    kind = kind_for(artifact.media_type, artifact.mime_type)
    removed = artifact.cleanup_started_at is not None or artifact.content_removed_at is not None
    version = row.version if row is not None else VERSION
    return {
        "status": "deleted" if removed else row.status if row is not None else "missing" if kind else "unsupported",
        "kind": kind, "version": version,
        "url": f"/v2/artifacts/{artifact.artifact_id}/preview?v={version}" if kind and not removed else None,
        "mime_type": "image/webp" if kind else None,
        "width": row.width if row is not None else None,
        "height": row.height if row is not None else None,
        "size_bytes": row.size_bytes if row is not None else None,
        "frame_count": row.frame_count if row is not None else None,
        "positions": row.positions if row is not None and row.positions else [],
        "error_code": row.error_code if row is not None else None,
    }


def metadata_for_artifacts(db: Session, artifacts: list[Artifact]) -> dict[str, dict]:
    if not artifacts:
        return {}
    rows = db.scalars(select(ArtifactPreview).where(
        ArtifactPreview.artifact_id.in_([artifact.artifact_id for artifact in artifacts]),
        ArtifactPreview.version == VERSION,
    )).all()
    indexed = {(row.artifact_id, row.kind): row for row in rows}
    return {artifact.artifact_id: preview_metadata(artifact, indexed.get(
        (artifact.artifact_id, kind_for(artifact.media_type, artifact.mime_type)))) for artifact in artifacts}


def process_source(kind: str, storage_uri: str, size_bytes: int | None) -> tuple[bytes, dict]:
    limit = MAX_IMAGE_BYTES if kind == "thumbnail" else MAX_VIDEO_BYTES
    if size_bytes is not None and not 0 < size_bytes <= limit:
        raise PreviewFailure("SOURCE_SIZE_LIMIT")
    with TemporaryDirectory(prefix="ig-preview-") as directory:
        source, destination = Path(directory) / "source.bin", Path(directory) / "preview.webp"
        total = 0
        with source.open("wb") as output:
            for chunk in artifact_store.iter_bytes(storage_uri):
                total += len(chunk)
                if total > limit:
                    raise PreviewFailure("SOURCE_SIZE_LIMIT")
                output.write(chunk)
        if not total:
            raise PreviewFailure("PROCESSING_FAILED")
        if kind == "thumbnail":
            # Pillow runs in the trusted producer process with byte, dimension
            # and pixel limits. Do not apply process-wide resource limits here.
            from app.artifacts.preview_worker import image_preview
            metadata = image_preview(source, destination)
        else:
            # Native video decode retains hard memory/CPU/wall-clock isolation.
            environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"}
            process = subprocess.Popen(
                [sys.executable, "-m", "app.artifacts.preview_worker", kind, str(source), str(destination)],
                cwd=Path(__file__).resolve().parents[2], env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
            )
            try:
                stdout, _ = process.communicate(timeout=PROCESS_TIMEOUT)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise PreviewFailure("PROCESSING_TIMEOUT") from None
            if len(stdout) > 4096:
                raise PreviewFailure("PROCESSING_FAILED")
            metadata = json.loads(stdout) if stdout else {}
            if process.returncode or not destination.is_file():
                error_class = metadata.get("exception_class", "DecoderProcessError")
                if not isinstance(error_class, str) or not error_class.isidentifier() or len(error_class) > 80:
                    error_class = "DecoderProcessError"
                raise PreviewFailure("PROCESSING_FAILED", exception_class=error_class, returncode=process.returncode)
        data = destination.read_bytes()
        if (not 0 < len(data) <= MAX_PREVIEW_BYTES or data[:4] != b"RIFF" or data[8:12] != b"WEBP"
            or not 0 < metadata["width"] <= 768 or not 0 < metadata["height"] <= 512
            or not 1 <= metadata["frame_count"] <= 6):
            raise PreviewFailure("PROCESSING_FAILED")
        return data, metadata


def ensure_preview(db: Session, artifact: Artifact, *, wait_for_slot: bool = False) -> ArtifactPreview | None:
    """Return cached/fenced state; None means this instance is at capacity.

    No transaction spans media reads, decoding or derivative-storage I/O.
    """
    # Serialize preview admission with the durable retention fence.
    artifact = db.scalar(select(Artifact).where(Artifact.artifact_id == artifact.artifact_id)
                         .with_for_update().execution_options(populate_existing=True))
    if artifact is None or artifact.cleanup_started_at is not None or artifact.content_removed_at is not None:
        return None
    kind = kind_for(artifact.media_type, artifact.mime_type)
    if kind is None:
        return None
    identity = (artifact.artifact_id, kind, VERSION)
    source_uri, size_bytes, media_type = artifact.storage_uri, artifact.size_bytes, artifact.media_type
    row = db.get(ArtifactPreview, identity)
    if row is not None and row.status in {"ready", "failed"}:
        return row
    acquired = SLOTS.acquire(timeout=PROCESS_TIMEOUT) if wait_for_slot else SLOTS.acquire(blocking=False)
    if not acquired:
        if wait_for_slot:
            raise PreviewFailure("PROCESSING_TIMEOUT")
        return None
    try:
        now, claim = utc_now(), str(uuid4())
        if row is None:
            row = ArtifactPreview(artifact_id=identity[0], kind=kind, version=VERSION,
                status="processing", claim_id=claim, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS), attempts=1)
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return db.get(ArtifactPreview, identity)
        else:
            if row.lease_expires_at > now:
                return row
            if row.attempts >= MAX_ATTEMPTS:
                db.execute(update(ArtifactPreview).where(
                    ArtifactPreview.artifact_id == identity[0], ArtifactPreview.kind == kind,
                    ArtifactPreview.version == VERSION, ArtifactPreview.status == "processing",
                    ArtifactPreview.lease_expires_at <= now,
                ).values(status="failed", error_code="PROCESSING_FAILED"))
                db.commit()
                return db.get(ArtifactPreview, identity)
            changed = db.execute(update(ArtifactPreview).where(
                ArtifactPreview.artifact_id == identity[0], ArtifactPreview.kind == kind,
                ArtifactPreview.version == VERSION, ArtifactPreview.status == "processing",
                ArtifactPreview.claim_id == row.claim_id, ArtifactPreview.lease_expires_at <= now,
            ).values(claim_id=claim, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                     attempts=ArtifactPreview.attempts + 1).execution_options(synchronize_session=False))
            db.commit()
            if changed.rowcount != 1:
                return db.get(ArtifactPreview, identity, populate_existing=True)
        # commit expires ORM data; use only plain snapshots during external work.
        values = {}
        stage = "decode"
        diagnostic(identity[0], media_type, stage, "started")
        try:
            data, metadata = process_source(kind, source_uri, size_bytes)
            stage = "store_derivative"
            uri = artifact_store.put_bytes(filename="preview.webp", data=data)
            track_write(db, uri, artifact_store)
            values = {"status": "ready", "storage_uri": uri, "size_bytes": len(data),
                      "etag": sha256(data).hexdigest(), **metadata}
        except Exception as exc:
            diagnostic(identity[0], media_type, stage, "failed", exc)
            code = str(exc) if isinstance(exc, PreviewFailure) else "PROCESSING_FAILED"
            if code not in ERROR_CODES:
                code = "PROCESSING_FAILED"
            values = {"status": "failed", "error_code": code}
        # Serialize publication with original cleanup as well as admission.
        # Without this lock an expired publisher could commit a locator after
        # cleanup snapshots preview rows, leaving a hidden derivative orphan.
        db.scalar(select(Artifact.artifact_id).where(Artifact.artifact_id == identity[0]).with_for_update())
        published = db.execute(update(ArtifactPreview).where(
            ArtifactPreview.artifact_id == identity[0], ArtifactPreview.kind == kind,
            ArtifactPreview.version == VERSION, ArtifactPreview.status == "processing",
            ArtifactPreview.claim_id == claim,
            ArtifactPreview.artifact_id.in_(select(Artifact.artifact_id).where(Artifact.cleanup_started_at.is_(None))),
        ).values(**values).execution_options(synchronize_session=False))
        db.commit()
        if published.rowcount != 1 and values.get("storage_uri"):
            try:
                artifact_store.delete(values["storage_uri"])
            except Exception as exc:
                diagnostic(identity[0], media_type, "discard_derivative", "failed", exc)
        diagnostic(identity[0], media_type, "persist_derivative", values["status"] if published.rowcount == 1 else "superseded")
        return db.get(ArtifactPreview, identity, populate_existing=True)
    finally:
        SLOTS.release()


def generate_preview_for_artifact(db: Session, artifact: Artifact) -> ArtifactPreview | None:
    """Best-effort producer hook. Call only AFTER committing the original.

    Preview transactions may commit/rollback independently of successful media
    creation. Cached terminal rows and fenced claims make repeated calls safe.
    """
    artifact_id = inspect(artifact).identity[0]
    media_type, kind = "unknown", None
    try:
        media_type = artifact.media_type
        kind = kind_for(media_type, artifact.mime_type)
        if kind is None:
            return None
        return ensure_preview(db, artifact, wait_for_slot=True)
    except Exception as exc:
        diagnostic(artifact_id, media_type, "preview_transaction", "failed", exc)
        db.rollback()
        if kind is None:
            return None
        # Best effort even when claiming/persisting the preview itself fails.
        # Never overwrite a concurrent owner's claim or a ready derivative.
        try:
            row = db.get(ArtifactPreview, (artifact_id, kind, VERSION))
            if row is None:
                row = ArtifactPreview(artifact_id=artifact_id, kind=kind, version=VERSION,
                    status="failed", error_code="PROCESSING_FAILED", attempts=0,
                    claim_id=str(uuid4()), lease_expires_at=utc_now())
                db.add(row)
                db.commit()
            return row
        except Exception as record_exc:
            db.rollback()
            diagnostic(artifact_id, media_type, "record_failure", "failed", record_exc)
            return None


def retry_failed_preview(db: Session, artifact: Artifact) -> ArtifactPreview | None:
    """Operational retry for exactly one failed current-version derivative."""
    identity = artifact.artifact_id, kind_for(artifact.media_type, artifact.mime_type), VERSION
    changed = db.execute(update(ArtifactPreview).where(
        ArtifactPreview.artifact_id == identity[0], ArtifactPreview.kind == identity[1],
        ArtifactPreview.version == identity[2], ArtifactPreview.status == "failed",
    ).values(status="processing", claim_id=str(uuid4()), lease_expires_at=utc_now() - timedelta(seconds=1),
             attempts=0, error_code=None).execution_options(synchronize_session=False))
    db.commit()
    if changed.rowcount != 1:
        raise ValueError("Selected preview is not failed; no preview was reset")
    db.expire_all()
    return generate_preview_for_artifact(db, artifact)
