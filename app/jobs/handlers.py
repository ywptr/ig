from app.images.tasks import execute_image_generation
from typing import Protocol

from app.jobs.result import Completed, HandlerResult, ProviderOperation

def noop(
    input_data: dict,
    *,
    tenant_id: str,
    user_id: str | None,
) -> Completed:
    print(
        f"system.noop executed: {input_data}",
        flush=True,
    )
    return Completed(
        metadata={
            "handler":"system.noop",
        }
    )

HANDLERS = {
    "system.noop": noop,
    "image.generate": execute_image_generation,
}

# Public dispatch is opt-in. Generation handlers are internal orchestration
# invoked through the validated media (or legacy image) submission service.
PUBLIC_CAPABILITIES = frozenset({"system.noop"})


class ResumeHandler(Protocol):
    def __call__(
        self,
        operation: ProviderOperation,
        *,
        tenant_id: str,
        user_id: str,
    ) -> HandlerResult:
        """Read the same external operation once; do not resubmit generation."""
        ...


# Providers opt in explicitly; no asynchronous production provider exists yet.
RESUME_HANDLERS: dict[str, ResumeHandler] = {}


def get_resume_handler(capability: str) -> ResumeHandler:
    handler = RESUME_HANDLERS.get(capability)
    if handler is None:
        raise ValueError(f"Unsupported continuation capability: {capability}")
    return handler


def is_publicly_submittable(capability: str) -> bool:
    return capability in PUBLIC_CAPABILITIES


def get_handler(capability: str):
    handler = HANDLERS.get(capability)

    if handler is None:
        raise ValueError(
            f"Unsupported capability: {capability}"
        )

    return handler


def is_supported(capability: str) -> bool:
    return capability in HANDLERS
