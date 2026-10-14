from app.media.providers.base import (
    ResolvedImageRequest,
)

STYLE_INSTRUCTIONS = {
    "photoreal": (
        "Render this as photorealistic imagery with "
        "natural lighting, realistic materials, "
        "physically plausible detail, and lifelike texture."
    ),
    "anime": (
        "Render this in a polished anime visual style "
        "with coherent character design and expressive detail."
    ),
    "3d": (
        "Render this as polished high-quality 3D artwork "
        "with convincing materials, lighting, and depth."
    ),
    "painterly": (
        "Render this with a painterly artistic treatment, "
        "visible artistic texture, and expressive color."
    ),
    "illustration": (
        "Render this as a polished professional illustration."
    ),
    "comic": (
        "Render this with a refined comic-art treatment "
        "and strong visual storytelling."
    ),
    "cinematic": (
        "Render this with cinematic composition, lighting, "
        "depth, and visual atmosphere."
    ),
}

def build_style_instruction(
    request: ResolvedImageRequest,
) -> str:
    if request.style == "auto":
        return ""

    return STYLE_INSTRUCTIONS.get(
        request.style,
        "",
    )

def build_reference_instructions(
    request: ResolvedImageRequest,
) -> str:
    if not request.references:
        return ""

    instructions = [
        "Reference images are provided."
    ]

    for index, reference in enumerate(
        request.references,
        start=1,
    ):
        role_instruction = {
            "character": (
                "Preserve the character's identity, "
                "facial features, body proportions, "
                "and recognizable appearance."
            ),
            "outfit": (
                "Preserve the clothing design, colors, "
                "materials, accessories, and details."
            ),
            "style": (
                "Use this image as a visual style "
                "reference rather than copying its "
                "specific subject."
            ),
            "pose": (
                "Use the pose and body positioning "
                "as guidance."
            ),
            "composition": (
                "Use the framing and composition "
                "as guidance."
            ),
            "environment": (
                "Preserve the important environmental "
                "and location characteristics."
            ),
            "previous_output": (
                "Maintain visual continuity with "
                "this previous output."
            ),
        }.get(
            reference.role,
            "Use this image as visual guidance."
        )

        strength_instruction = {
            "loose": (
                "Treat it as loose guidance."
            ),
            "normal": (
                "Follow it with normal consistency."
            ),
            "strong": (
                "Match it closely while still "
                "following the requested scene."
            ),
        }.get(
            reference.strength,
            "",
        )

        instructions.append(
            f"Reference image {index}: "
            f"{role_instruction} "
            f"{strength_instruction}"
        )

    return " ".join(
        instructions
    )

def build_image_prompt(
    request: ResolvedImageRequest,
) -> str:
    # Add reference instructions if any references are provided
    reference_instructions = (
        build_reference_instructions(
            request
        )
    )
    # Add style instructions if a specific style is requested
    style_instructions = (
        build_style_instruction(
            request
        )
    )
    # Join the reference and style instructions into a single string
    shared_instructions = "\n\n".join(
        part
        for part in (
            request.description,
            style_instruction,
            reference_instructions,
        )
        if part
    )
    # Single image mode: create one image with the requested aspect ratio and natural composition
    if request.layout_type == "single":
        return (
            f"{shared_instructions}\n\n"
            f"Create one image with an overall "
            f"aspect ratio of "
            f"{request.aspect_ratio}. "
            f"Compose the subject naturally within "
            f"the complete canvas."
        )

    # Panel grid mode: create a single image containing multiple distinct panels arranged in a grid
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
            f"{shared_instructions}\n\n"
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