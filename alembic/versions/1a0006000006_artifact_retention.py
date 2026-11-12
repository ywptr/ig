"""Artifact content tombstones and explicit tenant retention overrides."""
from alembic import op
import sqlalchemy as sa

revision = "1a0006000006"
down_revision = "1a0003000003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("artifacts", sa.Column("cleanup_started_at", sa.DateTime(), nullable=True))
    op.add_column("artifacts", sa.Column("content_removed_at", sa.DateTime(), nullable=True))
    op.add_column("artifacts", sa.Column("content_removal_reason", sa.String(30), nullable=True))
    op.create_index("ix_artifacts_cleanup_started_at", "artifacts", ["cleanup_started_at"])
    op.create_index("ix_artifacts_retention", "artifacts", ["provenance", "content_removed_at", "created_at"])
    op.add_column("tenant_settings", sa.Column("generated_artifact_retention_enabled", sa.Boolean(), nullable=True))
    op.add_column("tenant_settings", sa.Column("generated_artifact_retention_days", sa.Integer(), nullable=True))
    with op.batch_alter_table("tenant_settings") as batch:
        batch.create_check_constraint("ck_retention_days", "generated_artifact_retention_days IS NULL OR generated_artifact_retention_days BETWEEN 1 AND 3650")


def downgrade():
    with op.batch_alter_table("tenant_settings") as batch:
        batch.drop_constraint("ck_retention_days", type_="check")
        batch.drop_column("generated_artifact_retention_days")
        batch.drop_column("generated_artifact_retention_enabled")
    op.drop_index("ix_artifacts_retention", table_name="artifacts")
    op.drop_index("ix_artifacts_cleanup_started_at", table_name="artifacts")
    op.drop_column("artifacts", "content_removal_reason")
    op.drop_column("artifacts", "content_removed_at")
    op.drop_column("artifacts", "cleanup_started_at")
