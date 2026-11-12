import anyio
import os
from functools import partial
from uuid import uuid4

from vercel.queue import (
    QueueClient,
)
from app.jobs.queue import JobKind, validate_delivery, QueueCancellation

TOPIC = "ig-jobs"

def get_queue_client() -> QueueClient:
    kwargs = {}

    if base_url := os.getenv("IG_QUEUE_BASE_URL"):
        kwargs["base_url"] = base_url

    if region := os.getenv("VERCEL_REGION"):
        kwargs["region"] = region

    if token := os.getenv("VERCEL_OIDC_TOKEN"):
        kwargs["token"] = token

    return QueueClient(**kwargs)

async def _enqueue(
    job_id: str,
    *,
    kind: JobKind = "initial",
    continuation_id: str | None = None,
    delay_seconds: int = 0,
) -> None:
    validate_delivery(kind, continuation_id, delay_seconds)
    client = get_queue_client()
    payload = {"job_id": job_id}
    identity = job_id
    if kind == "recover":
        payload["kind"] = kind
        identity = f"{job_id}:recover:{uuid4()}"
    if kind == "continue":
        payload.update(kind=kind, continuation_id=continuation_id)
        # Republish is permitted after lost/failed scheduling; DB checkpoint
        # tokens, rather than transport deduplication, gate provider polling.
        identity = f"{job_id}:continue:{continuation_id}:{uuid4()}"
    await client.send(
        TOPIC, payload, idempotency_key=identity, delay=delay_seconds,
    )


class VercelJobQueue:
    def cancel(self, job_id: str) -> QueueCancellation:
        return QueueCancellation(False, 0,
            "Vercel delivery retraction is unsupported; IG state prevents canceled work from executing. No provider call was canceled.")

    def enqueue(
        self,
        job_id: str,
        *,
        kind: JobKind = "initial",
        continuation_id: str | None = None,
        delay_seconds: int = 0,
    ) -> None:
        anyio.run(
            partial(
                _enqueue, job_id, kind=kind, continuation_id=continuation_id,
                delay_seconds=delay_seconds,
            ),
        )
