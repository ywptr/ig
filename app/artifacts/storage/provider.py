from pathlib import Path

from app.artifacts.storage.base import (
    ArtifactStore,
)
from app.artifacts.storage.local import (
    LocalArtifactStore,
)


artifact_store: ArtifactStore = (
    LocalArtifactStore(
        Path("/app/output")
    )
)