"""Shared capability-driven artifact checks. Every lookup remains actor scoped."""
from sqlalchemy import select
from app.db.models import Artifact
from ig_core.media.validation import MediaRequestValidationError, validate_reference_shape as validate_reference_shape
from app.media.providers.registry import image_provider_registration, video_provider_registration


def reference_slots(provider: str, model: str, media_type: str):
    registration = (image_provider_registration if media_type == "image" else video_provider_registration)(provider)
    metadata = next((item for item in registration.CAPABILITIES.models if item.id == model), None)
    if metadata is None:
        raise MediaRequestValidationError("Model capabilities are unknown")
    return metadata.technical.reference_inputs if metadata.technical else ()


def scoped_reference_artifacts(db, ids, *, tenant_id, user_id):
    return {row.artifact_id: row for row in db.scalars(select(Artifact).where(
        Artifact.artifact_id.in_(ids), Artifact.tenant_id == tenant_id, Artifact.user_id == user_id,
    ))} if ids else {}


def resolve_references(db, references, *, slots, tenant_id, user_id, store=None, read_bytes=False, rows=None):
    selected = validate_reference_shape(references, slots)
    if rows is None:
        rows = scoped_reference_artifacts(db, [values.get("artifact_id") for values, _ in selected],
                                          tenant_id=tenant_id, user_id=user_id)
    result = []
    for values, slot in selected:
        row = rows.get(values.get("artifact_id"))
        if row is None:
            raise MediaRequestValidationError("Reference artifact not found or not accessible")
        if row.cleanup_started_at is not None or row.content_removed_at is not None:
            raise MediaRequestValidationError("Reference artifact content was removed")
        if row.media_type not in slot.media_types or row.mime_type not in slot.mime_types:
            raise MediaRequestValidationError("Unsupported reference media or MIME type")
        if row.size_bytes is not None and not 0 < row.size_bytes <= slot.max_bytes:
            raise MediaRequestValidationError("Reference artifact exceeds supported size")
        data = None
        if read_bytes:
            chunks, total = [], 0
            try:
                for chunk in store.iter_bytes(row.storage_uri):
                    total += len(chunk)
                    if total > slot.max_bytes:
                        raise MediaRequestValidationError("Reference artifact exceeds supported size")
                    chunks.append(chunk)
            except MediaRequestValidationError:
                raise
            except Exception:
                raise MediaRequestValidationError("Reference artifact content is unavailable") from None
            if not total:
                raise MediaRequestValidationError("Reference artifact content is missing")
            data = b"".join(chunks)
        result.append((row, values, data))
    return result
