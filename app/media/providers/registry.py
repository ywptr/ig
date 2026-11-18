from ig_core.providers.runtime import ProviderRuntimeConfig, create_image_adapter, create_video_adapter
from ig_core.providers.base import (
    ImageProviderAdapter,
    ImageProviderConfigurationError,
)
from app.media.providers.openai.image import (
    OpenAIImageProvider,
)
from app.media.providers.alibaba.video import AlibabaWanVideoProvider
from ig_core.providers.video import VideoProviderAdapter, VideoProviderConfigurationError
from app.media.providers.google.image import GoogleImageProvider


VIDEO_PROVIDERS: dict[str, type[VideoProviderAdapter]] = {"alibaba": AlibabaWanVideoProvider}
DEFAULT_IMAGE_PROVIDER = "openai"
IMAGE_PROVIDERS = {"openai": OpenAIImageProvider, "google": GoogleImageProvider}


def image_provider_registration(provider: str) -> type[ImageProviderAdapter]:
    registration = IMAGE_PROVIDERS.get(provider.strip().lower())
    if registration is None:
        raise ImageProviderConfigurationError("Unsupported image provider")
    return registration


def resolve_image_model(provider: str, *, config: dict, model: str | None = None) -> str:
    registration = IMAGE_PROVIDERS.get(provider.strip().lower())
    if registration is None:
        raise ValueError(f"Unsupported image provider: {provider}")
    return registration.resolve_tenant_model(config, model)


def video_provider_registration(provider: str) -> type[VideoProviderAdapter]:
    registration = VIDEO_PROVIDERS.get(provider.strip().lower())
    if registration is None:
        raise VideoProviderConfigurationError("Unsupported video provider")
    return registration


def create_video_provider(
    provider: str, *, credentials: dict[str, str], config: dict, model: str | None = None,
) -> VideoProviderAdapter:
    registration = video_provider_registration(provider)
    return create_video_adapter(registration, ProviderRuntimeConfig(
        provider=provider, credentials=credentials, model=model, endpoint=config.get("base_url"),
    ))

def create_image_provider(
    provider: str,
    *,
    credentials: dict[str, str],
    config: dict,
) -> ImageProviderAdapter:
    provider = provider.strip().lower()

    registration = IMAGE_PROVIDERS.get(provider)
    if registration is not None:
        return create_image_adapter(registration, ProviderRuntimeConfig(
            provider=provider, credentials=credentials,
        ))

    raise ValueError(
        f"Unsupported image provider: "
        f"{provider}"
    )

