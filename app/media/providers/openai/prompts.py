from app.media.providers.base import (
    ResolvedImageRequest,
)


def build_image_prompt(
    request: ResolvedImageRequest,
) -> str:
    if request.layout_type == "single":
        return (
            f"{request.description}\n\n"
            f"Create one image with an overall "
            f"aspect ratio of "
            f"{request.aspect_ratio}. "
            f"Compose the subject naturally within "
            f"the complete canvas."
        )

    if request.layout_type == "panel_grid":
        if request.panel_count is None:
            raise RuntimeError(
                "panel_grid requires panel_count"
            )

        if request.arrangement is None:
            raise RuntimeError(
                "panel_grid requires arrangement"
            )

        return (
            f"{request.description}\n\n"
            f"Create one final image containing "
            f"{request.panel_count} distinct visual "
            f"panels. "
            f"The complete image must use an overall "
            f"aspect ratio of {request.aspect_ratio}. "
            f"Arrange the panels in a "
            f"{request.arrangement} grid. "
            f"Keep all individual panels balanced, "
            f"consistently proportioned, and composed "
            f"naturally within the overall canvas. "
            f"Do not stretch individual panels simply "
            f"to fill the canvas. "
            f"Each panel should work as a complete "
            f"composition while all panels together "
            f"form one coherent visual sequence. "
            f"Maintain consistent characters, outfits, "
            f"setting, lighting, proportions, camera "
            f"language, and artistic treatment across "
            f"all panels. "
            f"Panel boundaries must be visually minimal: "
            f"use narrow spacing or extremely thin, "
            f"subtle borders only. "
            f"Avoid thick comic-book frames, large "
            f"gutters, decorative borders, captions, "
            f"titles, speech bubbles, or unnecessary "
            f"text. "
            f"If panel numbers appear, they must be "
            f"tiny, discreet, and visually secondary."
        )

    raise RuntimeError(
        f"Unsupported image layout: "
        f"{request.layout_type}"
    )