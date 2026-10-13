from dataclasses import dataclass, field
from typing import Any


@dataclass
class HandlerResult:
    provider: str | None = None
    model: str | None = None
    metadata={
        "artifact_id": artifact.artifact_id,
        "request_id": request_id,
        "request_version": input_data.get(
            "request_version",
            "legacy",
        ),
        "layout": (
            (
                input_data.get("output")
                or {}
            ).get("layout")
        ),
    },