import base64
from dataclasses import replace
from io import BytesIO

from openai import OpenAI
from ig_core.media.catalog import ProviderMetadata

from ig_core.providers.base import (
    ImageProviderAdapter,
    ImageProviderResult,
    ResolvedImageRequest,
)
from ig_core.providers.openai.prompts import (
    build_image_prompt,
)


from ig_core.providers.openai.models import DEFAULT_OPENAI_MODEL, OPENAI_IMAGE_MODELS
from ig_core.providers.base import ImageProviderConfigurationError

DEFAULT_ASPECT_RATIO = "2:3"
DEFAULT_QUALITY = "standard"
# Compatibility exports; actual translation always selects a model definition.
OPENAI_SIZE_BY_ASPECT_RATIO = OPENAI_IMAGE_MODELS[DEFAULT_OPENAI_MODEL].sizes
OPENAI_QUALITY = OPENAI_IMAGE_MODELS[DEFAULT_OPENAI_MODEL].qualities


def resolve_openai_model(model: str | None) -> str:
    selected = DEFAULT_OPENAI_MODEL if model is None or model == "" else model
    if not isinstance(selected, str) or selected not in OPENAI_IMAGE_MODELS:
        raise ImageProviderConfigurationError("Unsupported OpenAI image model")
    return selected


def resolve_openai_size(aspect_ratio: str | None, model: str | None = None) -> tuple[str, str]:
    ratio = aspect_ratio or DEFAULT_ASPECT_RATIO
    size = OPENAI_IMAGE_MODELS[resolve_openai_model(model)].sizes.get(ratio)
    if size is None:
        raise ImageProviderConfigurationError("Unsupported OpenAI aspect ratio")
    return ratio, size


def resolve_openai_quality(quality: str | None, model: str | None = None) -> tuple[str, str]:
    profile = quality or DEFAULT_QUALITY
    value = OPENAI_IMAGE_MODELS[resolve_openai_model(model)].qualities.get(profile)
    if value is None:
        raise ImageProviderConfigurationError("Unsupported OpenAI quality")
    return profile, value


def request_parameters(model, parameters):
    definition = OPENAI_IMAGE_MODELS[resolve_openai_model(model)]
    allowed = {"background"} | ({"complexity"} if definition.complexity_levels else set())
    if set(parameters) - allowed:
        raise ImageProviderConfigurationError("Unsupported parameters for selected OpenAI model")
    if parameters.get("background", "auto") not in definition.background_modes:
        raise ImageProviderConfigurationError("Unsupported background for selected OpenAI model")
    if "complexity" in parameters and parameters["complexity"] not in definition.complexity_levels:
        raise ImageProviderConfigurationError("Unsupported complexity for selected OpenAI model")
    translated = {"output_format": definition.output_format}
    if "background" in parameters:
        translated["background"] = parameters["background"]
    if "complexity" in parameters:
        # The pinned SDK predates this field; its supported extra_body path
        # handles both JSON generations and multipart edits without an upgrade.
        translated["extra_body"] = {"complexity": parameters["complexity"]}
    return translated


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
    DEFAULT_MODEL = DEFAULT_OPENAI_MODEL
    MODELS = OPENAI_IMAGE_MODELS
    CAPABILITIES = ProviderMetadata("OpenAI", tuple(item.metadata for item in OPENAI_IMAGE_MODELS.values()))

    @staticmethod
    def configuration_is_valid(config: dict) -> bool:
        return isinstance(config, dict)

    @classmethod
    def validate_model_options(cls, *, model, count, strategy, reference_count, parameters):
        if count != 1 or strategy != "single" or reference_count > cls.MODELS[model].metadata.capabilities.max_references:
            raise ImageProviderConfigurationError("Unsupported OpenAI count, strategy or references")
        request_parameters(model, parameters)

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
        parameters: dict | None = None,
    ):
        return self.client.images.generate(
            model=model,
            prompt=prompt,
            size=size,
            quality=quality,
            **(parameters or {"output_format": "png"}),
        )

    def _generate_with_references(
        self,
        *,
        request: ResolvedImageRequest,
        prompt: str,
        model: str,
        size: str,
        quality: str,
        parameters: dict | None = None,
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
                **(parameters or {"output_format": "png"}),
            )

        finally:
            for file in reference_files:
                file.close()

    def generate(
        self,
        request: ResolvedImageRequest,
    ) -> ImageProviderResult:
        model = resolve_openai_model(request.model)
        definition = OPENAI_IMAGE_MODELS[model]
        controls = definition.metadata.capabilities
        if (not request.description.strip() or request.style not in controls.styles
            or request.layout_type not in controls.layout_types
            or (request.layout_type == "panel_grid" and (request.panel_count not in controls.panel_counts
                or request.arrangement not in controls.panel_arrangements))
            or len(request.references) > controls.max_references):
            raise ImageProviderConfigurationError("Unsupported OpenAI image request")
        for reference in request.references:
            slot = definition.metadata.technical.reference_inputs[0]
            if (reference.mime_type not in slot.mime_types or not 0 < len(reference.data) <= slot.max_bytes
                or reference.role not in slot.roles or reference.strength not in slot.strengths):
                raise ImageProviderConfigurationError("Unsupported OpenAI reference input")
        parameters = request_parameters(model, request.parameters)
        (
            aspect_ratio,
            size,
        ) = resolve_openai_size(
            request.aspect_ratio, model
        )

        (
            quality_profile,
            quality,
        ) = resolve_openai_quality(
            request.quality, model
        )

        resolved_request = replace(
            request,
            aspect_ratio=aspect_ratio,
            quality=quality_profile,
            model=model,
        )

        prompt = build_image_prompt(resolved_request)
        if len(prompt) > definition.metadata.technical.max_prompt_characters:
            raise ImageProviderConfigurationError("Rendered image prompt exceeds model limit")

        if resolved_request.references:
            result = (
                self._generate_with_references(
                    request=resolved_request,
                    prompt=prompt,
                    model=model,
                    size=size,
                    quality=quality,
                    parameters=parameters,
                )
            )

        else:
            result = (
                self._generate_without_references(
                    prompt=prompt,
                    model=model,
                    size=size,
                    quality=quality,
                    parameters=parameters,
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
