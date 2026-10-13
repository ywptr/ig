import base64
import os
from dataclasses import replace
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


def resolve_openai_size(
    aspect_ratio: str | None,
) -> tuple[str, str]:
    """
    Resolve an IG semantic aspect ratio into an
    OpenAI-native image size.

    Returns:
        (resolved_aspect_ratio, openai_size)
    """

    resolved_aspect_ratio = (
        aspect_ratio
        or DEFAULT_ASPECT_RATIO
    )

    size = OPENAI_SIZE_BY_ASPECT_RATIO.get(
        resolved_aspect_ratio
    )

    if size is None:
        raise ValueError(
            "Unsupported OpenAI aspect ratio: "
            f"{resolved_aspect_ratio}"
        )

    return (
        resolved_aspect_ratio,
        size,
    )


def resolve_openai_quality(
    quality: str | None,
) -> tuple[str, str]:
    """
    Resolve an IG semantic quality profile into
    an OpenAI-native quality value.

    Returns:
        (resolved_quality_profile, openai_quality)
    """

    resolved_quality = (
        quality
        or DEFAULT_QUALITY
    )

    openai_quality = OPENAI_QUALITY.get(
        resolved_quality
    )

    if openai_quality is None:
        raise ValueError(
            "Unsupported OpenAI quality: "
            f"{resolved_quality}"
        )

    return (
        resolved_quality,
        openai_quality,
    )


def resolve_openai_model(
    model: str | None,
) -> str:
    return (
        model
        or DEFAULT_OPENAI_MODEL
    )


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
            or (
                f"reference-"
                f"{index}."
                f"{extension}"
            )
        )

        files.append(file)

    return files


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
        (
            aspect_ratio,
            size,
        ) = resolve_openai_size(
            request.aspect_ratio
        )

        (
            quality_profile,
            quality,
        ) = resolve_openai_quality(
            request.quality
        )

        model = resolve_openai_model(
            request.model
        )

        resolved_request = replace(
            request,
            aspect_ratio=aspect_ratio,
            quality=quality_profile,
            model=model,
        )

        prompt = build_image_prompt(
            resolved_request
        )

        if resolved_request.references:
            result = (
                self._generate_with_references(
                    request=resolved_request,
                    prompt=prompt,
                    model=model,
                    size=size,
                    quality=quality,
                )
            )

        else:
            result = (
                self._generate_without_references(
                    prompt=prompt,
                    model=model,
                    size=size,
                    quality=quality,
                )
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
                "quality_profile": (
                    quality_profile
                ),
                "aspect_ratio": (
                    aspect_ratio
                ),
                "reference_count": len(
                    resolved_request.references
                ),
            },
        )