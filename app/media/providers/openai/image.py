import base64
from dataclasses import replace
from io import BytesIO
from typing import get_args

from openai import OpenAI
from app.media.technical import TechnicalCapabilities, ExecutionBehavior
from app.media.capability_schemas import GenerationControls, IntegerRange, ModelMetadata, ProviderMetadata
from app.media.constraints import IMAGE_ASPECT_RATIOS, MAX_IMAGE_REFERENCES
from app.media.schemas import ReferenceRole, GridArrangement, MediaReference
from app.media.providers.openai.prompts import STYLE_INSTRUCTIONS

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
    CAPABILITIES = ProviderMetadata("OpenAI", (ModelMetadata(
        DEFAULT_OPENAI_MODEL, "GPT Image 2", GenerationControls(
            aspect_ratios=list(IMAGE_ASPECT_RATIOS),
            reference_roles=list(get_args(ReferenceRole)), max_references=MAX_IMAGE_REFERENCES,
            reference_strengths=list(get_args(MediaReference.model_fields["strength"].annotation)),
            count=IntegerRange(min=1, max=1), layout_types=["single", "panel_grid"],
            panel_counts=[4], panel_arrangements=list(get_args(GridArrangement)),
            panel_arrangements_selectable_in=["hard"],
            quality_profiles=list(OPENAI_QUALITY), styles=["auto", *STYLE_INSTRUCTIONS],
        ), technical=TechnicalCapabilities(
            input_media=("text", "image"), output_media=("image",),
            operations=("text_to_image", "image_to_image", "reference_image_conditioning", "multi_reference_conditioning",),
            execution=ExecutionBehavior(mode="synchronous", polling_required=False),
            reference_mime_types=("image/png", "image/jpeg", "image/webp", "image/jpg"),
            output_mime_types=("image/png",),
        ),
    ),))

    @staticmethod
    def configuration_is_valid(config: dict) -> bool:
        return isinstance(config, dict)

    @staticmethod
    def default_model_for(config: dict) -> str:
        return resolve_openai_model(config.get("default_model"))

    @classmethod
    def from_config(cls, *, credentials: dict[str, str], config: dict):
        api_key = credentials.get("api_key")
        if not api_key:
            raise ValueError("OpenAI credential 'api_key' is required")
        return cls(api_key=api_key)

    def __init__(
        self,
        *,
        api_key: str,
    ):
        self.client = OpenAI(
            api_key=api_key
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
