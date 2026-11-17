"""Add user-owned Drive storage without changing existing locators or overrides."""
from alembic import op
import sqlalchemy as sa

revision = "1a0007000007"
down_revision = "1a0010000010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("artifact_storage_preference", sa.String(24), nullable=False, server_default="ig_managed"))
    op.add_column("tenant_settings", sa.Column("google_drive_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("tenant_settings", sa.Column("external_generated_retention_enabled", sa.Boolean()))
    op.add_column("tenant_settings", sa.Column("external_generated_retention_days", sa.Integer()))
    with op.batch_alter_table("tenant_settings") as batch:
        batch.create_check_constraint("ck_external_retention_days", "external_generated_retention_days IS NULL OR external_generated_retention_days BETWEEN 1 AND 3650")
    op.create_table("drive_connections",
        sa.Column("connection_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text()),
        sa.Column("account_id", sa.String(255), nullable=False),
        sa.Column("account_email", sa.String(320)), sa.Column("account_name", sa.String(255)),
        sa.Column("folder_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False), sa.Column("checked_at", sa.DateTime()))
    op.create_index("ix_drive_connections_user_id", "drive_connections", ["user_id"])
    op.create_index("uq_drive_active_user", "drive_connections", ["user_id"], unique=True,
                    postgresql_where=sa.text("active = true"), sqlite_where=sa.text("active = 1"))
    op.create_table("drive_oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("session_hash", sa.String(64), nullable=False), sa.Column("browser_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_verifier", sa.Text(), nullable=False), sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False))
    op.create_index("ix_drive_oauth_states_user_id", "drive_oauth_states", ["user_id"])
    op.create_index("ix_drive_oauth_states_expires_at", "drive_oauth_states", ["expires_at"])


def downgrade():
    op.drop_table("drive_oauth_states")
    op.drop_table("drive_connections")
    with op.batch_alter_table("tenant_settings") as batch:
        batch.drop_constraint("ck_external_retention_days", type_="check")
        batch.drop_column("external_generated_retention_days")
        batch.drop_column("external_generated_retention_enabled")
        batch.drop_column("google_drive_enabled")
    op.drop_column("users", "artifact_storage_preference")
