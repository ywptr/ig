"""Plain runtime inputs supplied by the application after provider selection."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    provider: str
    credentials: dict[str, str] = field(repr=False)
    model: str | None = None
    endpoint: str | None = field(default=None, repr=False)


def create_image_adapter(adapter, runtime: ProviderRuntimeConfig):
    return adapter.from_config(credentials=runtime.credentials, config={})


def create_video_adapter(adapter, runtime: ProviderRuntimeConfig):
    return adapter.from_config(
        credentials=runtime.credentials,
        config={"base_url": runtime.endpoint},
        model=adapter.resolve_model(runtime.model),
    )
