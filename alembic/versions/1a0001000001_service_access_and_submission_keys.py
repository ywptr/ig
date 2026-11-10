"""Add service public-key trust registry and durable media submission keys."""
from alembic import op
import sqlalchemy as sa

revision = "1a0001000001"
down_revision = "d42c6e18a907"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("service_credentials",
        sa.Column("key_id", sa.String(100), primary_key=True),
        sa.Column("service_id", sa.String(64), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("tenant_ids", sa.JSON(), nullable=False),
        sa.Column("actor_issuers", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_service_credentials_service_id", "service_credentials", ["service_id"])
    op.create_table("media_submission_keys",
        sa.Column("submission_id", sa.String(64), primary_key=True),
        sa.Column("service_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("identity_id", sa.String(36), sa.ForeignKey("user_identities.identity_id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.job_id"), unique=True),
        sa.Column("request_id", sa.String(36)),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("media_submission_keys")
    op.drop_index("ix_service_credentials_service_id", table_name="service_credentials")
    op.drop_table("service_credentials")
