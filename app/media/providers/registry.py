from app.media.providers.base import (
    ImageProviderAdapter,
)
from app.media.providers.openai.image import (
    OpenAIImageProvider,
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

