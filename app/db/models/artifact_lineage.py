from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ArtifactGeneration(Base):
    """One producer per Artifact; each Job/Execution may produce many Artifacts."""
    __tablename__ = "artifact_generations"
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.job_id"), index=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.execution_id"), index=True)


class ArtifactInput(Base):
    __tablename__ = "artifact_inputs"
    output_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    input_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), index=True)
    relation: Mapped[str] = mapped_column(String(30), default="reference")
    role: Mapped[str] = mapped_column(String(30))
    strength: Mapped[str | None] = mapped_column(String(20))
    __table_args__ = (CheckConstraint("output_artifact_id != input_artifact_id", name="ck_artifact_input_not_self"),)
