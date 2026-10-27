import uuid
from dataclasses import replace
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.service import create_artifact
from app.artifacts.storage.provider import (
    artifact_store,
)
from app.db.database import SessionLocal
from app.jobs.result import HandlerResult
from app.media.providers.registry import (
    create_image_provider,
)
from app.db.models import (
    Artifact,
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
) -> ImageRequest:
    """
    Load the persisted ImageRequest and perform
    basic ownership sanity checks.
    """

    statement = select(
        ImageRequest
    ).where(
        ImageRequest.request_id
        == request_id
    )

    image_record = db.scalar(
        statement
    )

    if image_record is None:
        raise RuntimeError(
            f"Image request not found: "
            f"{request_id}"
        )

    if image_record.user_id is None:
        raise RuntimeError(
            f"Image request has no owner: "
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
        provider=advanced.get(
            "provider",
            "openai",
        ),
        model=advanced.get(
            "model") or ""
        ,
        parameters=advanced.get(
            "parameters"
        ) or {},
    )


def generate_image(
    db: Session,
    *,
    tenant_id: str,
    request: ResolvedImageRequest,
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

    resolved_model = (
        request.model
        or provider_settings.get(
            "default_model"
        )
        or ""
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

    filename = (
        f"{uuid.uuid4()}.png"
    )

    storage_uri = (
        artifact_store.put_bytes(
            filename=filename,
            data=result.data,
        )
    )

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
        size_bytes=result.size_bytes,
        metadata={
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
) -> HandlerResult:
    """
    Convert the completed provider result into the generic
    worker HandlerResult used by Execution tracking.
    """

    output = input_data.get(
        "output"
    ) or {}

    return HandlerResult(
        provider=result.provider,
        model=result.model,
        metadata={
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
) -> HandlerResult:
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
            )

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

            return build_handler_result(
                artifact=artifact,
                input_data=normalized_input,
                resolved_request=(
                    resolved_request
                ),
                result=result,
            )

        except Exception:
            db.rollback()

            mark_image_failed(
                db,
                image_record,
            )

            raise

    finally:
        db.close()

def resolve_image_references(
    db: Session,
    *,
    input_data: dict,
    image_record: ImageRequest,
) -> list[ResolvedImageReference]:
    references = (
        input_data.get("references")
        or []
    )

    resolved_references: list[
        ResolvedImageReference
    ] = []

    for reference in references:
        artifact_id = reference.get(
            "artifact_id"
        )

        if not artifact_id:
            raise RuntimeError(
                "Reference artifact_id is required"
            )

        statement = select(
            Artifact
        ).where(
            Artifact.artifact_id
            == artifact_id,
            Artifact.tenant_id
            == image_record.tenant_id,
            Artifact.user_id
            == image_record.user_id,
        )

        artifact = db.scalar(
            statement
        )

        if artifact is None:
            raise RuntimeError(
                "Reference artifact not found "
                f"or not accessible: "
                f"{artifact_id}"
            )

        if artifact.media_type != "image":
            raise RuntimeError(
                "Image generation references "
                "must be image artifacts"
            )

        if not artifact_store.exists(
            artifact.storage_uri
        ):
            raise RuntimeError(
                "Reference artifact content "
                f"is missing: {artifact_id}"
            )

        data = b"".join(
            artifact_store.iter_bytes(
                artifact.storage_uri
            )
        )

        resolved_references.append(
            ResolvedImageReference(
                artifact_id=(
                    artifact.artifact_id
                ),
                role=reference.get(
                    "role",
                    "previous_output",
                ),
                strength=reference.get(
                    "strength",
                    "normal",
                ),
                mime_type=(
                    artifact.mime_type
                    or "image/png"
                ),
                filename=artifact.filename,
                data=data,
            )
        )

    return resolved_references