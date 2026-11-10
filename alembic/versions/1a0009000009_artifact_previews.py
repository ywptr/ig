"""Add versioned mechanically derived artifact previews."""
from alembic import op
import sqlalchemy as sa

revision = "1a0009000009"
down_revision = "1a0001000001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("artifact_previews",
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifacts.artifact_id"), primary_key=True),
        sa.Column("kind", sa.String(30), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.String(2048)),
        sa.Column("width", sa.Integer()), sa.Column("height", sa.Integer()),
        sa.Column("size_bytes", sa.Integer()), sa.Column("etag", sa.String(64)),
        sa.Column("frame_count", sa.Integer()), sa.Column("positions", sa.JSON()),
        sa.Column("error_code", sa.String(30)),
        sa.Column("created_at", sa.DateTime(), nullable=False))


def downgrade():
    op.drop_table("artifact_previews")
