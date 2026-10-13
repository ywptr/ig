from sqlalchemy import select

from app.artifacts.service import create_artifact
from app.db.database import SessionLocal
from app.db.models import ImageRequest
from app.services.openai_images import OpenAIImageService
from app.artifacts.storage.provider import (
    artifact_store,
)
from app.jobs.result import HandlerResult

import uuid

image_provider = None
_image_provider = None

def get_image_provider():
    global _image_provider

    if _image_provider is None:
        _image_provider = OpenAIImageService()

    return _image_provider

def execute_image_generation(input_data: dict) -> HandlerResult:
    request_id = input_data["request_id"]

    if "intent" in input_data:
        prompt = build_image_prompt(
            input_data
        )
    else:
        # Temporary compatibility with jobs
        # queued before structured media requests.
        prompt = input_data["prompt"]

    db = SessionLocal()

    try:
        statement = select(ImageRequest).where(
            ImageRequest.request_id == request_id
        )

        image_record = db.scalar(statement)

        if image_record is None:
            raise RuntimeError(
                f"Image request not found: {request_id}"
            )

        if image_record.user_id is None:
            raise RuntimeError(
                f"Image request has no owner: {request_id}"
        )

        try:
            image_provider = get_image_provider()
            result = image_provider.generate(prompt)

            filename = (
                f"{uuid.uuid4()}.png"
            )

            storage_uri = (
                artifact_store.put_bytes(
                    filename=filename,
                    data=result["data"],
                )
            )

            artifact = create_artifact(
                db,
                tenant_id=image_record.tenant_id,
                user_id=image_record.user_id,
                media_type="image",
                storage_uri=storage_uri,
                mime_type=result["mime_type"],
                filename=filename,
                size_bytes=result["size_bytes"],
                metadata={
                    "provider": "openai",
                    "model": result["model"],
                    "request_version": input_data.get(
                        "request_version",
                        "legacy",
                    ),
                    "interface_level": input_data.get(
                        "interface_level"
                    ),
                    "layout": (
                        (
                            input_data.get("output")
                            or {}
                        ).get("layout")
                    ),
                },
            )

            image_record.status = "completed"

            # Legacy compatibility fields
            image_record.filename = filename
            image_record.mime_type = (
                result["mime_type"]
            )
            image_record.size_bytes = (
                result["size_bytes"]
            )

            image_record.artifact_id = (
                artifact.artifact_id
            )

            db.commit()

            return HandlerResult(
                provider="openai",
                model=result["model"],
                metadata={
                    "provider": "openai",
                    "model": result["model"],
                    "request_version": (
                        input_data.get(
                            "request_version",
                            "legacy",
                        )
                    ),
                    "interface_level": (
                        input_data.get(
                            "interface_level"
                        )
                    ),
                    "layout": (
                        (
                            input_data.get("output")
                            or {}
                        ).get("layout")
                    ),
                },
            )

        except Exception:
            db.rollback()

            # Record failure separately after rolling
            # back any partially-created artifact.
            image_record.status = "failed"
            db.commit()

            raise

    finally:
        db.close()

def build_image_prompt(
    input_data: dict,
) -> str:
    intent = input_data.get("intent") or {}

    description = intent.get(
        "description",
        "",
    ).strip()

    if not description:
        raise RuntimeError(
            "Image request has no description"
        )

    output = input_data.get("output") or {}
    layout = output.get("layout") or {}

    aspect_ratio = output.get(
        "aspect_ratio"
    )

    layout_type = layout.get(
        "type",
        "single",
    )

    aspect_instruction = (
        f"The final image canvas should use "
        f"an aspect ratio of {aspect_ratio}. "
        if aspect_ratio
        else ""
    )

    if layout_type == "single":
        return (
            f"{description}\n\n"
            f"{aspect_instruction}"
            f"Compose the scene naturally for the "
            f"full image canvas."
        )

    if layout_type == "panel_grid":
        panel_count = layout.get(
            "panel_count"
        )

        if panel_count is None:
            raise RuntimeError(
                "panel_grid requires panel_count"
            )

        return (
            f"{description}\n\n"
            f"Create this as a {panel_count}-panel "
            f"visual composition contained within "
            f"one final image. "
            f"{aspect_instruction}"
            f"Arrange the {panel_count} panels "
            f"naturally within that canvas. "
            f"Each panel should represent a distinct "
            f"moment or view while remaining visually "
            f"coherent with the others. "
            f"Maintain consistent characters, setting, "
            f"lighting, proportions, and artistic style "
            f"across all {panel_count} panels. "
            f"Panel separation should be subtle: use "
            f"very thin, unobtrusive borders or narrow "
            f"spacing between panels. "
            f"Avoid thick comic-book frames, decorative "
            f"borders, or large gutters. "
            f"If panel numbers are shown, make them very "
            f"small, discreet, and visually secondary, "
            f"placed near a corner of each panel. "
            f"Do not use large labels, captions, titles, "
            f"speech bubbles, or other unnecessary text."
        )

    raise RuntimeError(
        f"Unsupported image layout: "
        f"{layout_type}"
    )