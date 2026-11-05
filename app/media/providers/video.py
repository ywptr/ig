"""Provider-neutral video requests and operation outcomes. No job dependency."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class VideoProviderError(RuntimeError):
    pass


class VideoProviderTerminalError(VideoProviderError):
    pass


class VideoProviderConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedVideoReference:
    artifact_id: str
    mime_type: str
    data: bytes
    role: str = "first_frame"


@dataclass(frozen=True)
class ResolvedVideoRequest:
    description: str
    provider: str
    model: str
    duration_seconds: int
    aspect_ratio: str
    resolution: str
    audio: bool
    references: list[ResolvedVideoReference] = field(default_factory=list)


@dataclass(frozen=True)
class VideoProviderPending:
    provider: str
    model: str
    operation_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoProviderCompleted:
    provider: str
    model: str
    data: bytes
    size_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)
    media_type: str = "video"
    mime_type: str = "video/mp4"
    extension: str = "mp4"


class VideoProviderAdapter(ABC):
    DEFAULT_MODEL: str
    MODELS: frozenset[str]

    @classmethod
    def resolve_model(cls, model: str | None) -> str:
        selected = model or cls.DEFAULT_MODEL
        if not isinstance(selected, str) or selected not in cls.MODELS:
            raise VideoProviderConfigurationError("Unsupported video model")
        return selected

    @classmethod
    def resolve_tenant_model(cls, config: dict, explicit_model: str | None = None) -> str:
        defaults = config.get("default_models") or {}
        if not isinstance(defaults, dict):
            raise VideoProviderConfigurationError("Invalid video model defaults")
        return cls.resolve_model(explicit_model or defaults.get("video"))

    @classmethod
    def default_model_for(cls, config: dict) -> str:
        return cls.resolve_tenant_model(config)

    @classmethod
    @abstractmethod
    def from_config(cls, *, credentials: dict[str, str], config: dict, model: str):
        ...

    @abstractmethod
    def submit(self, request: ResolvedVideoRequest) -> VideoProviderPending:
        ...

    @abstractmethod
    def poll(self, operation_id: str, checkpoint: dict) -> VideoProviderPending | VideoProviderCompleted:
        ...
