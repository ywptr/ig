from typing import Literal, Protocol
from dataclasses import dataclass


@dataclass(frozen=True)
class QueueCancellation:
    supported: bool
    removed: int
    detail: str


JobKind = Literal["initial", "continue", "recover"]


def validate_delivery(kind: JobKind, continuation_id: str | None, delay_seconds: int) -> None:
    if kind not in {"initial", "continue", "recover"}:
        raise ValueError("Unknown job delivery kind")
    if kind == "continue" and not continuation_id:
        raise ValueError("Continuation delivery requires a checkpoint token")
    if kind in {"initial", "recover"} and continuation_id is not None:
        raise ValueError("Initial delivery cannot carry a continuation token")
    if delay_seconds < 0:
        raise ValueError("Delivery delay must be non-negative")


class JobQueue(Protocol):
    def cancel(self, job_id: str) -> QueueCancellation:
        """Best-effort pending-delivery retraction, never process termination."""
        ...

    def enqueue(
        self, job_id: str, *, kind: JobKind = "initial",
        continuation_id: str | None = None, delay_seconds: int = 0,
    ) -> None:
        """Enqueue a job for processing."""
        ...
