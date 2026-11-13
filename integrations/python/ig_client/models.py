"""Public wire views only. Provider constraints remain owned by IG discovery."""
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

JobState = Literal["queued", "running", "waiting", "polling", "cancel_requested",
                   "canceled", "completed", "failed"]


class Reference(TypedDict, total=False):
    artifact_id: str
    role: str
    strength: str


class ReferenceSlot(TypedDict):
    name: str
    media_types: list[str]
    mime_types: list[str]
    roles: list[str]
    min_count: int
    max_count: int
    max_bytes: int
    strengths: list[str]


class TechnicalCapabilities(TypedDict, total=False):
    version: int
    input_media: list[str]
    output_media: list[str]
    operations: list[str]
    execution: dict[str, Any]
    reference_inputs: list[ReferenceSlot]
    reference_mime_types: list[str]
    output_mime_types: list[str]
    max_prompt_characters: int | None


class IntegerRange(TypedDict):
    min: int
    max: int


class GenerationControls(TypedDict, total=False):
    text_to_media: bool
    reference_roles: list[str]
    max_references: int
    reference_strengths: list[str]
    aspect_ratios: list[str]
    resolutions: list[str]
    duration_seconds: IntegerRange | None
    audio: bool
    count: IntegerRange
    layout_types: list[str]
    panel_counts: list[int]
    panel_arrangements: list[str]
    panel_arrangements_selectable_in: list[str]
    quality_profiles: list[str]
    styles: list[str]


class ModelCapability(TypedDict):
    id: str
    display_name: str
    available: bool
    usable: bool
    technical: TechnicalCapabilities | None
    supported_interface_levels: list[str]
    selectable_in: list[str]
    capabilities: GenerationControls
    use_cases: list[str]


class ProviderCapability(TypedDict):
    id: str
    display_name: str
    registered: bool
    configured: bool
    enabled: bool
    available: bool
    default_model: str | None
    default_model_available: bool
    models: list[ModelCapability]


class MediaCapability(TypedDict):
    id: str
    available: bool
    default_provider: str | None
    providers: list[ProviderCapability]
    purposes: list[str]


class Capabilities(TypedDict):
    schema_version: int
    interface_levels: dict[str, dict[str, bool]]
    media_types: list[MediaCapability]


@dataclass(frozen=True)
class Submission:
    correlation_id: str
    job_id: str
    request_id: str
    media_type: str
    status: JobState


@dataclass(frozen=True)
class Job:
    job_id: str
    status: JobState
    error_code: str | None = None
    error_message: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Execution:
    execution_id: str
    job_id: str
    status: str
    attempt: int
    artifact_ids: tuple[str, ...] = ()
    error_code: str | None = None
    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    media_type: str
    mime_type: str | None
    filename: str | None
    size_bytes: int | None
    job_id: str | None
    execution_id: str | None
    preview: dict[str, Any] | None
    media_properties: dict[str, Any]
    content_available: bool


@dataclass(frozen=True)
class MediaResult:
    submission: Submission
    job: Job
    executions: tuple[Execution, ...]
    artifacts: tuple[Artifact, ...]


@dataclass(frozen=True)
class EditorLaunchRequest:
    """Reserved IG#0010 input boundary; no session API exists yet."""
    correlation_id: str
    artifact_ids: tuple[str, ...] = ()
    return_url: str | None = None
