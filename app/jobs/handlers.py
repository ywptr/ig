from app.images.tasks import execute_image_generation
from app.jobs.result import HandlerResult

def noop(
    input_data: dict,
    *,
    tenant_id: str,
    user_id: str | None,
) -> HandlerResult:
    print(
        f"system.noop executed: {input_data}",
        flush=True,
    )
    return HandlerResult(
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
