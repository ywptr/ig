"""Compensate known, exclusively owned writes after failed publication.

This is not a bucket sweep. Process death between put and receipt registration
still requires provider inventory and a durable preallocated write journal.
"""
import logging
from sqlalchemy import event, exists, select
from sqlalchemy.orm import sessionmaker
from app.db.database import SessionLocal
from app.db.models import Artifact, ArtifactPreview

logger = logging.getLogger(__name__)


def discard_unpublished(uri, store, *, sessions=None):
    try:
        with (sessions or SessionLocal)() as check:
            if check.scalar(select(exists().where(Artifact.storage_uri == uri))):
                return
            if check.scalar(select(exists().where(ArtifactPreview.storage_uri == uri))):
                return
        store.delete(uri)
    except Exception as exc:
        # An unavailable DB or ambiguous commit never authorizes deletion.
        logger.warning("artifact_orphan outcome=deferred error_type=%s", type(exc).__name__)


def track_write(db, uri, store, *, sessions=None):
    """Call immediately after a successful put, before publication in this Session."""
    if not db.in_transaction():
        db.begin()  # Ensure even failure before the first DB write has a rollback hook.
    sessions = sessions or sessionmaker(bind=db.get_bind())
    receipts = db.info.setdefault("artifact_write_receipts", [])
    receipts.append((uri, store, sessions))
    if db.info.get("artifact_write_hooks"):
        return
    db.info["artifact_write_hooks"] = True

    def committed(session):
        if not session.in_nested_transaction():
            session.info["artifact_write_receipts"].clear()

    def ended(session, transaction):
        if transaction.parent is not None:
            return
        pending = list(session.info["artifact_write_receipts"])
        session.info["artifact_write_receipts"].clear()
        for locator, backend, factory in pending:
            discard_unpublished(locator, backend, sessions=factory)

    event.listen(db, "after_commit", committed)
    event.listen(db, "after_transaction_end", ended)
