import base64
import os
from io import BytesIO
from openai import OpenAI

from app.media.providers.base import (
    ImageProviderAdapter,
    ImageProviderResult,
    ResolvedImageRequest,
)
from app.media.providers.openai.prompts import (
    build_image_prompt,
)


DEFAULT_OPENAI_MODEL = "gpt-image-2"
DEFAULT_ASPECT_RATIO = "2:3"
DEFAULT_QUALITY = "standard"


OPENAI_SIZE_BY_ASPECT_RATIO = {
    "2:3": "1024x1536",
    "1:1": "1024x1024",
    "4:5": "1024x1280",
    "16:9": "1536x864",
}


OPENAI_QUALITY = {
    "draft": "low",
    "standard": "medium",
    "high": "high",
}

# Helper function to build reference files from the request
def build_reference_files(
    request: ResolvedImageRequest,
) -> list[BytesIO]:
    files: list[BytesIO] = []

    for index, reference in enumerate(
        request.references,
        start=1,
    ):
        file = BytesIO(
            reference.data
        )

        extension = (
            extension_for_mime_type(
                reference.mime_type
            )
        )

        file.name = (
            reference.filename
            or f"reference-{index}.{extension}"
        )

        files.append(file)

    return files

# Helper function to get the file extension for a given MIME type
def extension_for_mime_type(
    mime_type: str,
) -> str:
    mapping = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
    }

    extension = mapping.get(
        mime_type.lower()
    )

    if extension is None:
        raise ValueError(
            "Unsupported OpenAI reference "
            f"image type: {mime_type}"
        )

    return extension

class OpenAIImageProvider(
    ImageProviderAdapter
):

    def __init__(self):
        self.client = OpenAI(
            api_key=os.environ[
                "OPENAI_API_KEY"
            ]
        )

    def _generate_without_references(
        self,
        *,
        prompt: str,
        model: str,
        size: str,
        quality: str,
    ):
        return self.client.images.generate(
            model=model,
            prompt=prompt,
            size=size,
            quality=quality,
        )
    
    def _generate_with_references(
        self,
        *,
        request: ResolvedImageRequest,
        prompt: str,
        model: str,
        size: str,
        quality: str,
    ):
        reference_files = (
            build_reference_files(
                request
            )
        )

        try:
            return self.client.images.edit(
                model=model,
                image=reference_files,
                prompt=prompt,
                size=size,
                quality=quality,
            )

        finally:
            for file in reference_files:
                file.close()

    def generate(
        self,
        request: ResolvedImageRequest,
    ) -> ImageProviderResult:
        aspect_ratio = (
            request.aspect_ratio
            or DEFAULT_ASPECT_RATIO
        )

        quality_name = (
            request.quality
            or DEFAULT_QUALITY
        )

        model = (
            request.model
            or DEFAULT_OPENAI_MODEL
        )

        size = OPENAI_SIZE_BY_ASPECT_RATIO.get(
            aspect_ratio
        )

        if size is None:
            raise ValueError(
                "Unsupported OpenAI aspect ratio: "
                f"{aspect_ratio}"
            )

        quality = OPENAI_QUALITY.get(
            quality_name
        )

        if quality is None:
            raise ValueError(
                "Unsupported OpenAI quality: "
                f"{quality_name}"
            )

        prompt = build_image_prompt(
            request
        )

        # If we do not have reference, then we can use the generate endpoint, otherwise we need to use the edit endpoint
        if resolved_request.references:
            result = self._generate_with_references(
                request=resolved_request,
                prompt=prompt,
                model=model,
                size=size,
                quality=quality,
            )
        else:
            result = self._generate_without_references(
                prompt=prompt,
                model=model,
                size=size,
                quality=quality,
            )

        if not result.data:
            raise RuntimeError(
                "OpenAI did not return image data"
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
            model=model,
            mime_type="image/png",
            data=image_bytes,
            size_bytes=len(image_bytes),
            metadata={
                "size": size,
                "quality": quality,
                "quality_profile": quality_name,
                "aspect_ratio": aspect_ratio,
            },
        )