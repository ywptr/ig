"""Generated-content policy and bounded, resumable physical cleanup.

A durable cleanup fence precedes external deletion. Once fenced, content cannot
be admitted as a new input or decoded into another preview, even after a crash.
"""
from datetime import timedelta
import logging
from urllib.parse import urlparse

from sqlalchemy import String, cast, exists, select, func, literal, text, case
from app.artifacts.schemas import ArtifactProvenance
from app.artifacts.storage.provider import artifact_store, provider
from app.core.time import as_utc, utc_now
from app.db.models import Artifact, ArtifactPreview, Job, Tenant, TenantSettings

logger = logging.getLogger(__name__)
TERMINAL_JOBS = ("completed", "failed", "canceled")


def storage_defaults(storage_policy: str) -> tuple[bool, int]:
    # Unknown/new user-owned backends must opt into automatic destruction.
    scheme = {"local": "file", "vercel": "vercel-blob"}.get(storage_policy, storage_policy)
    backend = artifact_store.providers.get(scheme)
    return getattr(backend, "retention_default_enabled", False), getattr(backend, "retention_default_days", 28)


def policy(settings: TenantSettings | None, storage_policy: str | None = None) -> dict:
    enabled, days = storage_defaults(storage_policy or provider)
    explicit_enabled = getattr(settings, "generated_artifact_retention_enabled", None)
    explicit_days = getattr(settings, "generated_artifact_retention_days", None)
    return {
        "generated_artifact_retention_enabled": enabled if explicit_enabled is None else explicit_enabled,
        "generated_artifact_retention_days": days if explicit_days is None else explicit_days,
        "retention_policy_source": "tenant" if explicit_enabled is not None or explicit_days is not None else "storage_default",
    }


def unavailable(artifact: Artifact) -> bool:
    return artifact.cleanup_started_at is not None or artifact.content_removed_at is not None


def active_input_clause():
    # Legacy jobs store references in JSON rather than an input reservation table.
    # Artifact IDs are server UUIDs; conservative substring matching also protects
    # legacy wrappers without loading the active population into Python.
    return exists(select(Job.job_id).where(
        Job.tenant_id == Artifact.tenant_id,
        Job.status.not_in(TERMINAL_JOBS),
        cast(Job.input_json, String).contains(Artifact.artifact_id),
    ))


def candidate_query(db, now):
    enabled = case(*[(Artifact.storage_uri.startswith(scheme + ":"),
                      getattr(store, "retention_default_enabled", False))
                     for scheme, store in artifact_store.providers.items()], else_=False)
    days = case(*[(Artifact.storage_uri.startswith(scheme + ":"),
                   getattr(store, "retention_default_days", 28))
                  for scheme, store in artifact_store.providers.items()], else_=28)
    settings = select(TenantSettings.generated_artifact_retention_enabled).where(
        TenantSettings.tenant_id == Artifact.tenant_id).scalar_subquery()
    duration = func.coalesce(select(TenantSettings.generated_artifact_retention_days).where(
        TenantSettings.tenant_id == Artifact.tenant_id).scalar_subquery(), days)
    instant = as_utc(now).replace(tzinfo=None)
    if db.bind.dialect.name == "sqlite":
        cutoff = func.datetime(instant, literal("-") + cast(duration, String) + literal(" days"), "+1 second")
    else:
        cutoff = literal(instant) - duration * text("INTERVAL '1 day'")
    # Pending removals are retried after a bounded lease. A concurrent invocation
    # cannot process a freshly claimed object. Expired preview publishers are fenced.
    pending = Artifact.cleanup_started_at < instant - timedelta(minutes=15)
    fresh = (Artifact.cleanup_started_at.is_(None)
             & (Artifact.provenance == ArtifactProvenance.GENERATED)
             & func.coalesce(settings, enabled)
             & (Artifact.created_at < cutoff)
             & ~active_input_clause()
             & ~exists(select(ArtifactPreview.artifact_id).where(
                 ArtifactPreview.artifact_id == Artifact.artifact_id,
                 ArtifactPreview.status == "processing", ArtifactPreview.lease_expires_at > now)))
    return select(Artifact.artifact_id).where(Artifact.content_removed_at.is_(None), pending | fresh).order_by(
        Artifact.cleanup_started_at.asc().nullsfirst(), Artifact.created_at, Artifact.artifact_id)


def eligible(db, artifact, now):
    if artifact.content_removed_at is not None:
        return False
    if artifact.cleanup_started_at is not None:
        return as_utc(artifact.cleanup_started_at) < as_utc(now) - timedelta(minutes=15)  # Resume a committed removal decision, regardless of later policy changes.
    effective = policy(db.get(TenantSettings, artifact.tenant_id), urlparse(artifact.storage_uri).scheme)
    if (artifact.provenance != ArtifactProvenance.GENERATED
        or not effective["generated_artifact_retention_enabled"]
        or as_utc(artifact.created_at) >= as_utc(now) - timedelta(days=effective["generated_artifact_retention_days"])):
        return False
    if db.scalar(select(active_input_clause()).select_from(Artifact).where(Artifact.artifact_id == artifact.artifact_id)):
        return False
    return not db.scalar(select(exists().where(
        ArtifactPreview.artifact_id == artifact.artifact_id,
        ArtifactPreview.status == "processing", ArtifactPreview.lease_expires_at > now,
    )))


def cleanup(db, *, limit=50, scan_limit=1000, dry_run=False, now=None, store=None):
    """One bounded invocation. Summary counts refer to this scan, not all tenants.

    Hold tenant + artifact locks only while fencing; never during storage I/O.
    Each object is removed idempotently before its DB acknowledgement. A crash
    can undercount reclaimed bytes, but cannot make removed content downloadable.
    """
    if not 1 <= limit <= 500 or not 1 <= scan_limit <= 10000:
        raise ValueError("Invalid cleanup batch bounds")
    now, store = now or utc_now(), store or artifact_store
    summary = dict(scanned=0, eligible=0, processed=0, deleted=0, failed=0, bytes_reclaimed=0)
    ids = list(db.scalars(candidate_query(db, now).limit(scan_limit)))
    db.rollback()
    for artifact_id in ids:
        summary["scanned"] += 1
        try:
            artifact = db.get(Artifact, artifact_id)
            if artifact is None:
                continue
            # Same tenant lock is taken during Job input admission. Order is
            # tenant then Artifact. Preview admission/publication locks Artifact.
            db.scalar(select(Tenant).where(Tenant.tenant_id == artifact.tenant_id).with_for_update(key_share=True))
            artifact = db.scalar(select(Artifact).where(Artifact.artifact_id == artifact_id)
                                 .with_for_update().execution_options(populate_existing=True))
            if not eligible(db, artifact, now):
                db.rollback()
                continue
            summary["eligible"] += 1
            if dry_run:
                db.rollback()
                continue
            if summary["processed"] >= limit:
                db.rollback()
                break
            summary["processed"] += 1
            artifact.cleanup_started_at = now
            artifact.content_removal_reason = "retention"
            db.commit()
            # Include historical preview versions. Persist each removal so retries
            # do not count it twice when another object in the batch fails.
            previews = list(db.scalars(select(ArtifactPreview).where(ArtifactPreview.artifact_id == artifact_id)))
            objects = [(row.kind, row.version, row.storage_uri, row.size_bytes or 0) for row in previews]
            original_uri, original_size = artifact.storage_uri, artifact.size_bytes or 0
            db.rollback()
            for kind, version, uri, size in objects:
                present = bool(uri) and store.exists(uri)
                if uri:
                    store.delete(uri)
                row = db.get(ArtifactPreview, (artifact_id, kind, version))
                row.status, row.storage_uri = "deleted", None
                row.error_code = "CONTENT_REMOVED"
                db.commit()
                if present:
                    summary["bytes_reclaimed"] += size
            original_present = store.exists(original_uri)
            store.delete(original_uri)
            artifact = db.get(Artifact, artifact_id)
            artifact.content_removed_at = now
            db.commit()
            if original_present:
                summary["bytes_reclaimed"] += original_size
            summary["deleted"] += 1
        except Exception as exc:
            db.rollback()
            summary["failed"] += 1
            logger.warning("artifact_cleanup artifact_id=%s error_type=%s", artifact_id, type(exc).__name__)
    return summary
