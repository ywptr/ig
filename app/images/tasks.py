import uuid
from dataclasses import replace
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.media_metadata import inspect_media
from app.artifacts.lineage import record_generation
from app.executions.context import current_execution
from app.artifacts.service import create_artifact
from app.artifacts.previews import generate_preview_for_artifact
from app.artifacts.storage.provider import (
    artifact_store,
)
from app.db.database import SessionLocal
from app.jobs.result import Completed
from app.jobs.cancellation import check_cancellation, JobCancelled
from app.media.routing import MediaSelection
from app.media.providers.registry import (
    create_image_provider,
    DEFAULT_IMAGE_PROVIDER,
    resolve_image_model,
)
from app.db.models import (
    ImageRequest,
)
from app.media.providers.base import (
    ImageProviderResult,
    ResolvedImageReference,
    ResolvedImageRequest,
)
from app.tenancy.provider_configs import (
    ProviderNotConfiguredError,
    get_provider_config,
    get_provider_credentials,
)

def normalize_image_job_input(
    input_data: dict,
) -> dict:
    """
    Convert legacy image-generation jobs into the
    current structured media-request format.
    """

    if "intent" in input_data:
        return input_data

    return {
        **input_data,
        "request_version": "legacy",
        "interface_level": "easy",
        "intent": {
            "description": input_data["prompt"],
        },
        "output": {
            "quality": "standard",
            "aspect_ratio": "1:1",
            "style": "auto",
            "count": 1,
            "layout": {
                "type": "single",
                "panel_count": None,
                "arrangement": None,
            },
        },
        "references": [],
        "advanced": None,
    }


def get_image_record(
    db: Session,
    request_id: str,
    *,
    tenant_id: str,
    user_id: str | None,
) -> ImageRequest:
    """
    Load only a request owned by the trusted job tenant and user.
    """

    if not tenant_id or not user_id:
        raise RuntimeError("Image generation requires trusted tenant and user ownership")

    statement = select(
        ImageRequest
    ).where(
        ImageRequest.request_id
        == request_id,
        ImageRequest.tenant_id == tenant_id,
        ImageRequest.user_id == user_id,
    )

    image_record = db.scalar(
        statement
    )

    if image_record is None:
        raise RuntimeError(
            f"Image request not found or not accessible: "
            f"{request_id}"
        )

    return image_record


def build_resolved_image_request(
    db: Session,
    *,
    input_data: dict,
    image_record: ImageRequest,
) -> ResolvedImageRequest:
    """
    Convert structured job input into the provider-neutral
    ResolvedImageRequest consumed by image adapters.
    """

    # Resolve references to existing artifacts and validate
    references = resolve_image_references(
        db,
        input_data=input_data,
        image_record=image_record,
    )

    intent = input_data.get(
        "intent"
    ) or {}

    output = input_data.get(
        "output"
    ) or {}

    layout = output.get(
        "layout"
    ) or {}

    advanced = input_data.get(
        "advanced"
    ) or {}
    selection = MediaSelection.model_validate(input_data["selection"]) if "selection" in input_data else None

    description = intent.get(
        "description",
        "",
    ).strip()

    if not description:
        raise RuntimeError(
            "Image request has no description"
        )

    return ResolvedImageRequest(
        description=description,
        quality=output.get(
            "quality",
            "standard",
        ),
        aspect_ratio=output.get(
            "aspect_ratio",
            "2:3",
        ),
        style=output.get(
            "style",
            "auto",
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
        references=references,
        provider=selection.provider if selection else (advanced.get("provider") or DEFAULT_IMAGE_PROVIDER).strip().lower(),
        model=selection.model if selection else advanced.get("model") or "",
        parameters=advanced.get(
            "parameters"
        ) or {},
    )


def generate_image(
    db: Session,
    *,
    tenant_id: str,
    request: ResolvedImageRequest,
    model_is_resolved: bool = False,
) -> ImageProviderResult:
    provider_config = get_provider_config(
        db,
        tenant_id=tenant_id,
        provider=request.provider,
    )

    if (
        provider_config is None
        or not provider_config.enabled
    ):
        raise ProviderNotConfiguredError(
            f"Provider is not configured "
            f"for this tenant: "
            f"{request.provider}"
        )

    credentials = get_provider_credentials(
        db,
        tenant_id=tenant_id,
        provider=request.provider,
    )

    provider_settings = (
        provider_config.config_json
        or {}
    )

    resolved_model = request.model if model_is_resolved else resolve_image_model(
        request.provider, config=provider_settings, model=request.model,
    )

    provider_request = replace(
        request,
        model=resolved_model,
    )

    provider = create_image_provider(
        request.provider,
        credentials=credentials,
        config=provider_settings,
    )

    check_cancellation()
    return provider.generate(
        provider_request
    )


def persist_image_result(
    db: Session,
    *,
    image_record: ImageRequest,
    input_data: dict,
    resolved_request: ResolvedImageRequest,
    result: ImageProviderResult,
):
    """
    Store provider output and create the Artifact record.
    """

    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(result.mime_type, "png")
    filename = f"{uuid.uuid4()}.{extension}"

    storage_uri = (
        artifact_store.put_bytes(
            filename=filename,
            data=result.data,
        )
    )

    check_cancellation()
    output = input_data.get(
        "output"
    ) or {}

    artifact = create_artifact(
        db,
        tenant_id=image_record.tenant_id,
        user_id=image_record.user_id,
        media_type="image",
        storage_uri=storage_uri,
        mime_type=result.mime_type,
        filename=filename,
        size_bytes=len(result.data),
        provenance="generated",
        media_properties=inspect_media(result.data, "image"),
        metadata={
            **selection_metadata(input_data),
            "provider": result.provider,
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
            "layout": output.get(
                "layout"
            ),
            "aspect_ratio": (
                resolved_request.aspect_ratio
            ),
            "style": (
                resolved_request.style
            ),
            "quality": (
                resolved_request.quality
            ),
            "provider_metadata": (
                result.metadata
            ),
        },
    )

    identity = current_execution()
    record_generation(db, artifact, inputs=input_data.get("references") or [],
        job_id=identity.job_id if identity else None, execution_id=identity.execution_id if identity else None)
    return artifact


def complete_image_record(
    image_record: ImageRequest,
    *,
    artifact,
    result: ImageProviderResult,
) -> None:
    """
    Update the legacy ImageRequest projection after
    successful artifact creation.
    """

    image_record.status = "completed"

    image_record.model = result.model

    image_record.filename = (
        artifact.filename
    )

    image_record.mime_type = (
        result.mime_type
    )

    image_record.size_bytes = (
        result.size_bytes
    )

    image_record.artifact_id = (
        artifact.artifact_id
    )


def mark_image_failed(
    db: Session,
    image_record: ImageRequest,
) -> None:
    """
    Persist failure state after rolling back any partially
    completed artifact/database work.
    """

    image_record.status = "failed"
    db.commit()


def build_handler_result(
    *,
    artifact,
    input_data: dict,
    resolved_request: ResolvedImageRequest,
    result: ImageProviderResult,
) -> Completed:
    """
    Convert the completed provider result into the generic
    worker Completed outcome used by Execution tracking.
    """

    output = input_data.get(
        "output"
    ) or {}

    return Completed(
        provider=result.provider,
        model=result.model,
        metadata={
            **selection_metadata(input_data),
            "artifact_id": (
                artifact.artifact_id
            ),
            "request_id": (
                input_data["request_id"]
            ),
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
            "layout": output.get(
                "layout"
            ),
            "aspect_ratio": (
                resolved_request.aspect_ratio
            ),
            "style": (
                resolved_request.style
            ),
            "quality": (
                resolved_request.quality
            ),
            "provider_metadata": (
                result.metadata
            ),
        },
    )


def execute_image_generation(
    input_data: dict,
    *,
    tenant_id: str,
    user_id: str | None,
) -> Completed:
    """
    Execute one image-generation job.

    The worker itself remains provider-neutral. Provider-
    specific prompt construction and API behavior belong
    to the provider adapter.
    """

    normalized_input = (
        normalize_image_job_input(
            input_data
        )
    )

    request_id = normalized_input[
        "request_id"
    ]

    db = SessionLocal()

    try:
        image_record = get_image_record(
            db,
            request_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )

        resolved_request = (
            build_resolved_image_request(
                db,
                input_data=normalized_input,
                image_record=image_record,
            )
        )

        try:
            result = generate_image(
                db,
                tenant_id=image_record.tenant_id,
                request=resolved_request,
                model_is_resolved="selection" in normalized_input,
            )

            check_cancellation()
            artifact = persist_image_result(
                db,
                image_record=image_record,
                input_data=normalized_input,
                resolved_request=(
                    resolved_request
                ),
                result=result,
            )

            complete_image_record(
                image_record,
                artifact=artifact,
                result=result,
            )

            db.commit()
            generate_preview_for_artifact(db, artifact)

            return build_handler_result(
                artifact=artifact,
                input_data=normalized_input,
                resolved_request=(
                    resolved_request
                ),
                result=result,
            )

        except JobCancelled:
            db.rollback()
            raise
        except Exception:
            db.rollback()

            mark_image_failed(
                db,
                image_record,
            )

            raise

    finally:
        db.close()


def selection_metadata(input_data: dict) -> dict:
    if "selection" not in input_data:
        return {}
    selection = MediaSelection.model_validate(input_data["selection"])
    return {"purpose": selection.purpose, "selection": selection.model_dump()}

def resolve_image_references(
    db: Session,
    *,
    input_data: dict,
    image_record: ImageRequest,
) -> list[ResolvedImageReference]:
    from app.artifacts.references import reference_slots, resolve_references
    selection = input_data.get("selection") or input_data.get("advanced") or {}
    provider = selection.get("provider") or DEFAULT_IMAGE_PROVIDER
    model = selection.get("model") or ""
    rows = resolve_references(db, input_data.get("references") or [],
        slots=reference_slots(provider, model, "image"), tenant_id=image_record.tenant_id,
        user_id=image_record.user_id, store=artifact_store, read_bytes=True)
    return [ResolvedImageReference(artifact_id=row.artifact_id, role=values.get("role", "previous_output"),
        strength=values.get("strength", "normal"), mime_type=row.mime_type,
        filename=row.filename, data=data) for row, values, data in rows]
