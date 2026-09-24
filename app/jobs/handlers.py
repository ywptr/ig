from app.images.tasks import execute_image_generation
from app.jobs.result import HandlerResult

def noop(input_data: dict) -> HandlerResult:
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


def get_handler(capability: str):
    handler = HANDLERS.get(capability)

    if handler is None:
        raise ValueError(
            f"Unsupported capability: {capability}"
        )

    return handler


def is_supported(capability: str) -> bool:
    return capability in HANDLERS