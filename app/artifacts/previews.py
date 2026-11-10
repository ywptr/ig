"""Lazy, bounded and durably cached mechanical derivatives of authorized Artifacts."""
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.artifacts.preview_policy import (
    VERSION, PROCESS_TIMEOUT, MAX_IMAGE_BYTES, MAX_VIDEO_BYTES, MAX_PREVIEW_BYTES, kind_for,
)
from app.artifacts.storage.provider import artifact_store
from app.core.time import utc_now
from app.db.models import Artifact, ArtifactPreview

SLOTS = BoundedSemaphore(2)
LEASE_SECONDS = 120
MAX_ATTEMPTS = 2  # Only expired/crashed claims can be retried; failures are cached.


class PreviewFailure(Exception):
    pass


def preview_metadata(artifact: Artifact, row: ArtifactPreview | None = None) -> dict:
    kind = kind_for(artifact.media_type, artifact.mime_type)
    version = row.version if row is not None else VERSION
    return {
        "status": row.status if row is not None else "missing" if kind else "unsupported",
        "kind": kind, "version": version,
        "url": f"/v2/artifacts/{artifact.artifact_id}/preview?v={version}" if kind else None,
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
        # The decoder inherits no service/provider/database credentials.
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
        if process.returncode or len(stdout) > 4096 or not destination.is_file():
            raise PreviewFailure("PROCESSING_FAILED")
        metadata = json.loads(stdout)
        data = destination.read_bytes()
        if (not 0 < len(data) <= MAX_PREVIEW_BYTES or data[:4] != b"RIFF" or data[8:12] != b"WEBP"
            or not 0 < metadata["width"] <= 768 or not 0 < metadata["height"] <= 512
            or not 1 <= metadata["frame_count"] <= 6):
            raise PreviewFailure("PROCESSING_FAILED")
        return data, metadata


def ensure_preview(db: Session, artifact: Artifact) -> ArtifactPreview | None:
    """Return cached/fenced state; None means this instance is at capacity.

    No transaction spans media reads, decoding or derivative-storage I/O.
    """
    kind = kind_for(artifact.media_type, artifact.mime_type)
    if kind is None:
        return None
    identity = (artifact.artifact_id, kind, VERSION)
    source_uri, size_bytes = artifact.storage_uri, artifact.size_bytes
    row = db.get(ArtifactPreview, identity)
    if row is not None and row.status in {"ready", "failed"}:
        return row
    if not SLOTS.acquire(blocking=False):
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
        try:
            data, metadata = process_source(kind, source_uri, size_bytes)
            uri = artifact_store.put_bytes(filename="preview.webp", data=data)
            values = {"status": "ready", "storage_uri": uri, "size_bytes": len(data),
                      "etag": sha256(data).hexdigest(), **metadata}
        except Exception as exc:
            code = str(exc) if isinstance(exc, PreviewFailure) else "PROCESSING_FAILED"
            if code not in {"SOURCE_SIZE_LIMIT", "PROCESSING_TIMEOUT", "PROCESSING_FAILED"}:
                code = "PROCESSING_FAILED"
            values = {"status": "failed", "error_code": code}
        db.execute(update(ArtifactPreview).where(
            ArtifactPreview.artifact_id == identity[0], ArtifactPreview.kind == kind,
            ArtifactPreview.version == VERSION, ArtifactPreview.status == "processing",
            ArtifactPreview.claim_id == claim,
        ).values(**values).execution_options(synchronize_session=False))
        db.commit()
        return db.get(ArtifactPreview, identity, populate_existing=True)
    finally:
        SLOTS.release()
