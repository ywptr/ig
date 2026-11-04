from app.media.providers.base import (
    ImageProviderAdapter,
)
from app.media.providers.openai.image import (
    OpenAIImageProvider,
)
from app.media.providers.alibaba.video import AlibabaWanVideoProvider
from app.media.providers.video import VideoProviderAdapter, VideoProviderConfigurationError


VIDEO_PROVIDERS: dict[str, type[VideoProviderAdapter]] = {"alibaba": AlibabaWanVideoProvider}


def video_provider_registration(provider: str) -> type[VideoProviderAdapter]:
    registration = VIDEO_PROVIDERS.get(provider.strip().lower())
    if registration is None:
        raise VideoProviderConfigurationError("Unsupported video provider")
    return registration


def create_video_provider(
    provider: str, *, credentials: dict[str, str], config: dict, model: str | None = None,
) -> VideoProviderAdapter:
    registration = video_provider_registration(provider)
    return registration.from_config(
        credentials=credentials, config=config, model=registration.resolve_model(model),
    )

def create_image_provider(
    provider: str,
    *,
    credentials: dict[str, str],
    config: dict,
) -> ImageProviderAdapter:
    provider = provider.strip().lower()

    if provider == "openai":
        api_key = credentials.get(
            "api_key"
        )

        if not api_key:
            raise ValueError(
                "OpenAI credential "
                "'api_key' is required"
            )

        return OpenAIImageProvider(
            api_key=api_key,
        )

    raise ValueError(
        f"Unsupported image provider: "
        f"{provider}"
    )

