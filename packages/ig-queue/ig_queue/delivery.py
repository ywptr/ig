from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

DeliveryKind = Literal["initial", "continue", "recover"]


def _required_string(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class Delivery:
    """Opaque durable work locator and its transport delivery identity."""

    work_id: str
    delivery_id: str
    kind: DeliveryKind = "initial"
    continuation_token: str | None = None

    def __post_init__(self) -> None:
        _required_string(self.work_id, "work_id")
        _required_string(self.delivery_id, "delivery_id")
        if self.kind not in ("initial", "continue", "recover"):
            raise ValueError("Unknown delivery kind")
        if self.kind == "continue":
            _required_string(self.continuation_token, "continuation_token")
        elif self.continuation_token is not None:
            raise ValueError("Only continuations can carry a continuation token")

    @classmethod
    def initial(cls, work_id: str) -> "Delivery":
        return cls(work_id, work_id)

    @classmethod
    def continuation(cls, work_id: str, token: str) -> "Delivery":
        return cls(work_id, f"{work_id}:continue:{token}:{uuid4()}", "continue", token)

    @classmethod
    def recovery(cls, work_id: str) -> "Delivery":
        return cls(work_id, f"{work_id}:recover:{uuid4()}", "recover")

    def to_message(self) -> dict[str, str]:
        message = {"work_id": self.work_id, "delivery_id": self.delivery_id, "kind": self.kind}
        if self.continuation_token is not None:
            message["continuation_token"] = self.continuation_token
        return message

    @classmethod
    def from_message(cls, message: Mapping[str, object]) -> "Delivery":
        if not isinstance(message, Mapping):
            raise ValueError("Delivery message must be a mapping")
        try:
            return cls(
                work_id=message["work_id"],
                delivery_id=message["delivery_id"],
                kind=message["kind"],
                continuation_token=message.get("continuation_token"),
            )
        except KeyError as exc:
            raise ValueError(f"Missing delivery field: {exc.args[0]}") from exc


DeliveryHandler = Callable[[Delivery], None]
DeliveryDecoder = Callable[[Mapping[str, object]], Delivery]
