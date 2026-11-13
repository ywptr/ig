"""Narrow delegated editor sessions and registered parent origins."""
from alembic import op
import sqlalchemy as sa

revision = "1a0010000010"
down_revision = "1a0006000006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("service_credentials", sa.Column("editor_origins", sa.JSON(), nullable=False, server_default="[]"))
    op.create_table("editor_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("credential_id", sa.String(100), sa.ForeignKey("service_credentials.key_id"), nullable=False),
        sa.Column("service_id", sa.String(64), nullable=False),
        sa.Column("identity_id", sa.String(36), sa.ForeignKey("user_identities.identity_id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("parent_origin", sa.String(512), nullable=False),
        sa.Column("launch_hash", sa.String(64)), sa.Column("access_hash", sa.String(64)),
        sa.Column("draft", sa.JSON(), nullable=False), sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("callback_context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("launch_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.create_index("ix_editor_sessions_expires_at", "editor_sessions", ["expires_at"])
    op.create_table("editor_attempts",
        sa.Column("session_id", sa.String(36), sa.ForeignKey("editor_sessions.session_id"), primary_key=True),
        sa.Column("attempt_id", sa.String(100), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.job_id"), nullable=False, unique=True),
        sa.Column("request_id", sa.String(36), nullable=False))


def downgrade():
    op.drop_table("editor_attempts")
    op.drop_index("ix_editor_sessions_expires_at", table_name="editor_sessions")
    op.drop_table("editor_sessions")
    op.drop_column("service_credentials", "editor_origins")
