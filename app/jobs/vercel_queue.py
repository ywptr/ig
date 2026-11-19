import os
from functools import partial

from ig_queue import Delivery, VercelQueueConfig
from ig_queue.backends.vercel import VercelBackend, create_vercel_client

from app.jobs.queue import JobKind, QueueCancellation, job_delivery

TOPIC = "ig-jobs"


def queue_config(*, all_deployments: bool = False) -> VercelQueueConfig:
    return VercelQueueConfig(
        topic=TOPIC,
        base_url=os.getenv("IG_QUEUE_BASE_URL") or None,
        region=os.getenv("VERCEL_REGION") or None,
        oidc_token=os.getenv("VERCEL_OIDC_TOKEN") or None,
        all_deployments=all_deployments,
    )


def get_queue_client(config: VercelQueueConfig | None = None):
    return create_vercel_client(config or queue_config())


def _legacy_message(delivery: Delivery) -> dict[str, str]:
    message = {"job_id": delivery.work_id}
    if delivery.kind != "initial":
        message["kind"] = delivery.kind
    if delivery.continuation_token is not None:
        message["continuation_id"] = delivery.continuation_token
    return message


class VercelJobQueue:
    def __init__(self):
        config = queue_config()
        self.backend = VercelBackend(
            config, client_factory=partial(get_queue_client, config), encode=_legacy_message,
        )

    def enqueue(
        self, job_id: str, *, kind: JobKind = "initial",
        continuation_id: str | None = None, delay_seconds: int = 0,
    ) -> None:
        self.backend.schedule(
            job_delivery(job_id, kind, continuation_id), delay_seconds=delay_seconds,
        )

    def cancel(self, job_id: str) -> QueueCancellation:
        result = self.backend.cancel(job_id)
        return QueueCancellation(
            self.backend.capabilities.retract_pending, result.removed,
            "Vercel delivery retraction is unsupported; IG state prevents canceled work from executing. No provider call was canceled.",
        )
