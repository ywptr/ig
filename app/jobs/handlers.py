from app.images.tasks import execute_image_generation

def noop(input_data: dict) -> None:
    print(
        f"system.noop executed: {input_data}",
        flush=True,
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