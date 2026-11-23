"""Provider-neutral model selection wire contracts."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SelectionMode = Literal["AUTO", "PREFERRED", "LOCKED"]
ProviderID = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,100}$")]
ModelID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")]


class SelectionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelCandidate(SelectionContract):
    provider: ProviderID | None = None
    model: ModelID


class ModelSelectionPolicy(SelectionContract):
    mode: SelectionMode = "AUTO"
    candidates: tuple[ModelCandidate, ...] = Field(default=(), max_length=20)
    allow_fallback: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode == "AUTO" and (self.candidates or self.allow_fallback):
            raise ValueError("AUTO takes no candidates or fallback option")
        if self.mode == "PREFERRED" and not self.candidates:
            raise ValueError("PREFERRED requires an ordered candidate list")
        if self.mode == "LOCKED" and (
            len(self.candidates) != 1 or not self.candidates[0].provider or self.allow_fallback
        ):
            raise ValueError("LOCKED requires one exact provider and model")
        if len({(item.provider, item.model) for item in self.candidates}) != len(self.candidates):
            raise ValueError("Duplicate model candidate")
        return self


class EligibilityReason(SelectionContract):
    code: str
    message: str


class ModelEligibility(SelectionContract):
    candidate: ModelCandidate
    registered: bool
    configured: bool
    enabled: bool
    available: bool
    compatible: bool | None
    eligible: bool
    reasons: tuple[EligibilityReason, ...] = ()


class SelectionSnapshot(SelectionContract):
    policy: ModelSelectionPolicy
    selected: ModelCandidate | None
    reason: str
    fallback_used: bool = False
    rejected: tuple[ModelEligibility, ...] = ()


class ModelSelectionResult(SelectionSnapshot):
    evaluations: tuple[ModelEligibility, ...] = ()

    def snapshot(self):
        # Store only caller candidate rejections and the accepted decision.
        return SelectionSnapshot(policy=self.policy, selected=self.selected,
                                 reason=self.reason, fallback_used=self.fallback_used, rejected=self.rejected)
