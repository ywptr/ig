from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ArtifactPayload:
    """Bounded output bytes, staged before fenced database finalization."""

    media_type: str
    mime_type: str
    extension: str
    data: bytes
    metadata: dict[str, Any] = field(default_factory=dict)
