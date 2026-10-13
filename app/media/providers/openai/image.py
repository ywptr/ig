import base64
import os

from openai import OpenAI

from app.media.providers.base import (
    ImageProviderAdapter,
    ImageProviderResult,
    ResolvedImageRequest,
)
from app.media.providers.openai.prompts import (
    build_image_prompt,
)


OPENAI_SIZE_BY_ASPECT_RATIO = {
    "1:1": "1024x1024",
    "4:5": "1024x1280",
    "16:9": "1536x864",
}


OPENAI_QUALITY = {
    "draft": "low",
    "standard": "medium",
    "high": "high",
}


class OpenAIImageProvider(
    ImageProviderAdapter
):

    def __init__(self):
        self.client = OpenAI(
            api_key=os.environ[
                "OPENAI_API_KEY"
            ]
        )

    def generate(
        self,
        request: ResolvedImageRequest,
    ) -> ImageProviderResult:
        prompt = build_image_prompt(
            request
        )

        size = OPENAI_SIZE_BY_ASPECT_RATIO.get(
            request.aspect_ratio
        )

        if size is None:
            raise ValueError(
                "Unsupported OpenAI aspect ratio: "
                f"{request.aspect_ratio}"
            )

        quality = OPENAI_QUALITY.get(
            request.quality
        )

        if quality is None:
            raise ValueError(
                "Unsupported OpenAI quality: "
                f"{request.quality}"
            )

        result = self.client.images.generate(
            model=request.model,
            prompt=prompt,
            size=size,
            quality=quality,
        )

        image_data = result.data[0]

        if not image_data.b64_json:
            raise RuntimeError(
                "OpenAI did not return "
                "base64 image data"
            )

        image_bytes = base64.b64decode(
            image_data.b64_json
        )

        return ImageProviderResult(
            provider="openai",
            model=request.model,
            mime_type="image/png",
            data=image_bytes,
            size_bytes=len(image_bytes),
            metadata={
                "size": size,
                "quality": quality,
                "aspect_ratio": (
                    request.aspect_ratio
                ),
            },
        )