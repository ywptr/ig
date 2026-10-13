from dataclasses import dataclass, field
from typing import Any


@dataclass
class HandlerResult:
    provider: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )