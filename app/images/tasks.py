from sqlalchemy import select

from app.artifacts.service import create_artifact
from app.db.database import SessionLocal
from app.db.models import ImageRequest
from app.artifacts.storage.provider import (
    artifact_store,
)
from app.jobs.result import HandlerResult

import uuid

from app.media.providers.base import (
    ResolvedImageRequest,
)
from app.media.providers.registry import (
    get_image_provider,
)

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
            intent = input_data.get("intent") or {}
            output = input_data.get("output") or {}
            layout = output.get("layout") or {}
            advanced = input_data.get("advanced") or {}

            # Normalize legacy requests to the new structured format.
            if "intent" not in input_data:
                input_data = {
                    **input_data,
                    "request_version": "legacy",
                    "intent": {
                        "description": input_data["prompt"],
                    },
                    "output": {
                        "quality": "standard",
                        "aspect_ratio": "1:1",
                        "layout": {
                            "type": "single",
                            "panel_count": None,
                            "arrangement": None,
                        },
                    },
                    "references": [],
                    "advanced": None,
                }

            # Create a ResolvedImageRequest from the structured request data.
            resolved_request = ResolvedImageRequest(
                description=intent["description"],
                quality=output.get(
                    "quality",
                    "standard",
                ),
                aspect_ratio=output.get(
                    "aspect_ratio",
                    "1:1",
                ),
                layout_type=layout.get(
                    "type",
                    "single",
                ),
                panel_count=layout.get(
                    "panel_count"
                ),
                arrangement=layout.get(
                    "arrangement"
                ),
                references=input_data.get(
                    "references"
                ) or [],
                provider=advanced.get(
                    "provider",
                    "openai",
                ),
                model=advanced.get(
                    "model",
                    "gpt-image-2",
                ),
                parameters=advanced.get(
                    "parameters"
                ) or {},
            )

            image_provider = get_image_provider(
                resolved_request.provider
            )

            result = image_provider.generate(
                resolved_request
            )

            filename = (
                f"{uuid.uuid4()}.png"
            )

            storage_uri = (
                artifact_store.put_bytes(
                    filename=filename,
                    data=result.data,
                )
            )

            artifact = create_artifact(
                db,
                tenant_id=image_record.tenant_id,
                user_id=image_record.user_id,
                media_type="image",
                storage_uri=storage_uri,
                mime_type=result.mime_type,
                filename=filename,
                size_bytes=result.size_bytes,
                metadata={
                    "provider": "openai",
                    "model": result.model,
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
                    "provider_metadata": result.metadata,
                },
            )

            image_record.status = "completed"

            # Legacy compatibility fields
            image_record.filename = filename
            image_record.mime_type = (
                result.mime_type
            )
            image_record.size_bytes = (
                result.size_bytes
            )

            image_record.artifact_id = (
                artifact.artifact_id
            )

            db.commit()

            return HandlerResult(
                provider="openai",
                model=result.model,
                metadata={
                    "provider": "openai",
                    "model": result.model,
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
