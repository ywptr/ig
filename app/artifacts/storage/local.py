from pathlib import Path

from ig_storage import LocalConfig, LocalStore


class LocalArtifactStore(LocalStore):
    retention_default_enabled = True
    retention_default_days = 30

    def __init__(self, base_dir: Path):
        super().__init__(LocalConfig(root=base_dir))
