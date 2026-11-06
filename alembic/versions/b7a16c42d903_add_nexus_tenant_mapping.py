"""Add optional canonical Nexus tenant mapping without changing local IDs."""
from alembic import op
import sqlalchemy as sa

revision = "b7a16c42d903"
down_revision = "0522f3973d5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("nexus_tenant_id", sa.String(36), nullable=True))
    op.create_index("ix_tenants_nexus_tenant_id", "tenants", ["nexus_tenant_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tenants_nexus_tenant_id", table_name="tenants")
    op.drop_column("tenants", "nexus_tenant_id")
