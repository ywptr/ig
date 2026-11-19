from dataclasses import dataclass
from enum import StrEnum
from math import ceil, isfinite
from typing import Protocol

from .capabilities import QueueCapabilities
from .delivery import Delivery


@dataclass(frozen=True)
class DeliveryReceipt:
    work_id: str
    delivery_id: str
    transport_id: str | None


class CancelStatus(StrEnum):
    RETRACTED = "retracted"
    NOT_FOUND = "not_found"
    UNSUPPORTED = "unsupported"
    ALREADY_RUNNING = "already_running"
    ERROR = "error"


@dataclass(frozen=True)
class CancelResult:
    status: CancelStatus
    removed: int = 0


def whole_second_delay(delay_seconds: float) -> int:
    if isinstance(delay_seconds, bool) or not isfinite(delay_seconds) or delay_seconds < 0:
        raise ValueError("Delivery delay must be finite and non-negative")
    # Both transports store whole seconds. Keep a positive fractional delay scheduled.
    return ceil(delay_seconds)


class QueueBackend(Protocol):
    @property
    def capabilities(self) -> QueueCapabilities: ...

    def enqueue(self, delivery: Delivery) -> DeliveryReceipt: ...

    def schedule(self, delivery: Delivery, *, delay_seconds: float) -> DeliveryReceipt: ...

    def cancel(self, work_id: str) -> CancelResult:
        """Best-effort retraction of pending deliveries tracked for work_id."""
        ...
