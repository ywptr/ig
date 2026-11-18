"""Reviewed Images API subset. See docs/openai-model-catalog.md for source evidence.

IG deliberately exposes one PNG result, four tested sizes, three semantic quality
profiles, and at most eight references. Upstream features outside this subset are
not advertised as IG capabilities. No model is discovered from a tenant string.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, get_args
from ig_core.media.catalog import GenerationControls, IntegerRange, ModelMetadata, ModelLifecycle
from ig_core.media.technical import TechnicalCapabilities, ExecutionBehavior, ReferenceInputSlot
from ig_core.media.schemas import ReferenceRole, GridArrangement, MediaReference
from ig_core.providers.openai.prompts import STYLE_INSTRUCTIONS

DEFAULT_OPENAI_MODEL = "gpt-image-2"  # Preserve existing implicit defaults; never migrate tenant policy silently.


@dataclass(frozen=True)
class OpenAIModelDefinition:
    metadata: ModelMetadata
    sizes: Mapping[str, str]
    qualities: Mapping[str, str]
    upstream_qualities: tuple[str, ...]
    background_modes: tuple[str, ...]
    complexity_levels: tuple[str, ...] = ()
    output_format: str = "png"
    transparency_preview: bool = False


def definition(identity, name, *, deprecated=False, modern=False, complexity=False, transparency_preview=False):
    sizes = MappingProxyType({"2:3": "1024x1536", "4:5": "1024x1280", "1:1": "1024x1024", "16:9": "1536x864"})
    qualities = MappingProxyType({"draft": "low", "standard": "medium", "high": "high"})
    return OpenAIModelDefinition(
        metadata=ModelMetadata(identity, name, GenerationControls(
            aspect_ratios=list(sizes), reference_roles=list(get_args(ReferenceRole)), max_references=8,
            reference_strengths=list(get_args(MediaReference.model_fields["strength"].annotation)),
            count=IntegerRange(min=1, max=1), layout_types=["single", "panel_grid"], panel_counts=[4],
            panel_arrangements=list(get_args(GridArrangement)), panel_arrangements_selectable_in=["hard"],
            quality_profiles=list(qualities), styles=["auto", *STYLE_INSTRUCTIONS],
        ), technical=TechnicalCapabilities(
            input_media=("text", "image"), output_media=("image",),
            operations=("text_to_image", "image_to_image", "image_editing", "reference_image_conditioning", "multi_reference_conditioning"),
            execution=ExecutionBehavior(mode="synchronous", polling_required=False),
            reference_inputs=(ReferenceInputSlot(name="references", media_types=("image",),
                mime_types=("image/png", "image/jpeg", "image/webp", "image/jpg"), roles=get_args(ReferenceRole),
                max_count=8, max_bytes=32 * 1024 * 1024, strengths=("loose", "normal", "strong")),),
            reference_mime_types=("image/png", "image/jpeg", "image/webp", "image/jpg"), output_mime_types=("image/png",),
            max_prompt_characters=32000,
        ), lifecycle=ModelLifecycle(deprecated=deprecated, replacement_model="gpt-image-2.6-sunburst" if deprecated else None)),
        sizes=sizes, qualities=qualities,
        upstream_qualities=("low", "medium", "high", "xhigh", "max", "auto") if modern else ("low", "medium", "high", "auto"),
        background_modes=("auto", "opaque", "transparent"), complexity_levels=("low", "medium", "high") if complexity else (),
        transparency_preview=transparency_preview,
    )


OPENAI_IMAGE_MODELS = MappingProxyType({item.metadata.id: item for item in (
    definition(DEFAULT_OPENAI_MODEL, "GPT Image 2", deprecated=True, transparency_preview=True),
    definition("gpt-image-2.5-flare", "GPT Image 2.5 Flare", modern=True),
    definition("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst", modern=True),
    definition("gpt-image-2.6-flare", "GPT Image 2.6 Flare", modern=True, complexity=True),
    definition("gpt-image-2.6-sunburst", "GPT Image 2.6 Sunburst", modern=True, complexity=True),
)})
