from app.media.providers.base import (
    ImageProviderAdapter,
)
from app.media.providers.openai.image import (
    OpenAIImageProvider,
)


_image_providers: dict[
    str,
    ImageProviderAdapter,
] = {}


def get_image_provider(
    provider: str,
) -> ImageProviderAdapter:
    provider = provider.strip().lower()

    if provider not in _image_providers:
        if provider == "openai":
            _image_providers[
                provider
            ] = OpenAIImageProvider()

        else:
            raise ValueError(
                f"Unsupported image provider: "
                f"{provider}"
            )

    return _image_providers[provider]