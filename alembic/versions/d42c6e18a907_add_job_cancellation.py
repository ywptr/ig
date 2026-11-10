"""Add cancellation timestamps; existing UTC timestamp values are unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "d42c6e18a907"
down_revision = "b7a16c42d903"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("cancel_requested_at", sa.DateTime(), nullable=True))
    op.add_column("jobs", sa.Column("canceled_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("jobs", "canceled_at")
    op.drop_column("jobs", "cancel_requested_at")
