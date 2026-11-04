from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Completed:
    """A handler has finished its work, including output persistence."""

    provider: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class Pending:
    """An external operation was submitted and must later be resumed by ID.

    operation_metadata must be JSON-compatible, non-secret checkpoint data.
    Never include credentials, signed URLs, or unfiltered provider responses:
    execution metadata is exposed through the authorized execution API.
    """

    provider: str
    model: str
    operation_id: str
    operation_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("provider", "model", "operation_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Pending {name} must be a non-empty string")


HandlerResult = Completed | Pending


@dataclass(frozen=True)
class ProviderOperation:
    """Persisted operation supplied to a resume handler, never public input."""

    provider: str
    model: str
    operation_id: str
    metadata: dict[str, Any]
