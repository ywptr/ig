from dataclasses import dataclass
from typing import Protocol

from ig_queue import Delivery, DeliveryKind
from ig_queue.contracts import whole_second_delay

JobKind = DeliveryKind


@dataclass(frozen=True)
class QueueCancellation:
    supported: bool
    removed: int
    detail: str


def job_delivery(job_id: str, kind: JobKind, continuation_id: str | None) -> Delivery:
    # Preserve IG's existing Vercel idempotency key format for every delivery kind.
    if kind == "recover" and continuation_id is None:
        return Delivery.recovery(job_id)
    if kind == "continue":
        if continuation_id is None:
            raise ValueError("Continuation delivery requires a checkpoint token")
        return Delivery.continuation(job_id, continuation_id)
    return Delivery(job_id, job_id, kind, continuation_id)


def validate_delivery(kind: JobKind, continuation_id: str | None, delay_seconds: int) -> None:
    Delivery("validation", "validation", kind, continuation_id)
    whole_second_delay(delay_seconds)


class JobQueue(Protocol):
    def cancel(self, job_id: str) -> QueueCancellation: ...

    def enqueue(
        self, job_id: str, *, kind: JobKind = "initial",
        continuation_id: str | None = None, delay_seconds: int = 0,
    ) -> None: ...
