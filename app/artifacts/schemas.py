"""Stable library vocabulary; provenance never grants access."""
from enum import Enum
from app.media.technical import InputRole


class ArtifactProvenance(str, Enum):
    UNKNOWN = "unknown"
    GENERATED = "generated"
    UPLOADED = "uploaded"


LineageRole = InputRole
