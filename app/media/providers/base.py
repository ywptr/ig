from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ImageProviderError(RuntimeError):
    """Sanitized provider failure safe for Job/Execution error reporting."""


class ImageProviderConfigurationError(ValueError):
    pass

@dataclass(frozen=True)
class ResolvedImageReference:
    artifact_id: str

    role: str
    strength: str

    mime_type: str
    filename: str | None

    data: bytes

@dataclass(frozen=True)
class ResolvedImageRequest:
    description: str

    quality: str
    aspect_ratio: str
    style: str

    layout_type: str
    panel_count: int | None
    arrangement: str | None

    references: list[
        ResolvedImageReference
    ] = field(
        default_factory=list,
    )

    provider: str = ""
    model: str = ""

    parameters: dict[str, Any] = field(
        default_factory=dict,
    )


@dataclass(frozen=True)
class ImageProviderResult:
    provider: str
    model: str

    mime_type: str
    data: bytes
    size_bytes: int

    metadata: dict[str, Any] = field(
        default_factory=dict,
    )


class ImageProviderAdapter(ABC):

    @classmethod
    def validate_submission_options(
        cls, *, config: dict, model: str | None, count: int, strategy: str,
        reference_count: int, parameters: dict,
    ) -> None:
        # Existing adapters retain their request acceptance behavior.
        pass

    @classmethod
    def tenant_default_model(cls, config: dict) -> str | None:
        return config.get("default_model")

    @classmethod
    def resolve_tenant_model(cls, config: dict, explicit_model: str | None = None) -> str:
        # Preserve legacy image resolution and adapter-side fallback.
        return explicit_model or cls.tenant_default_model(config) or ""

    @abstractmethod
    def generate(
        self,
        request: ResolvedImageRequest,
    ) -> ImageProviderResult:
        raise NotImplementedError
