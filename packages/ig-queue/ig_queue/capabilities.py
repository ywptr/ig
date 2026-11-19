from dataclasses import dataclass


@dataclass(frozen=True)
class QueueCapabilities:
    delayed_delivery: bool
    retract_pending: bool
    terminate_running: bool
    track_by_work_id: bool
    deduplicate_delivery_id: bool
