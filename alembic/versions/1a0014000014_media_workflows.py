"""Add durable media workflow state."""

import sqlalchemy as sa
from alembic import op
from app.core.time import UTCDateTime

revision = "1a0014000014"
down_revision = "1a0007000007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "media_workflows",
        sa.Column("workflow_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("submission_key", sa.String(128)),
        sa.Column("submission_hash", sa.String(64), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("next_check_at", UTCDateTime()),
        sa.UniqueConstraint("tenant_id", "user_id", "submission_key", name="uq_media_workflow_submission"),
    )
    op.create_index("ix_media_workflow_due", "media_workflows", ["next_check_at", "workflow_id"])


def downgrade():
    op.drop_table("media_workflows")
