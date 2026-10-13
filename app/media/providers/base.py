from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResolvedImageRequest:
    description: str

    quality: str
    aspect_ratio: str

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

    @abstractmethod
    def generate(
        self,
        request: ResolvedImageRequest,
    ) -> ImageProviderResult:
        raise NotImplementedError

@dataclass(frozen=True)
class ResolvedImageReference:
    artifact_id: str

    role: str
    strength: str

    mime_type: str
    filename: str | None

    data: bytes