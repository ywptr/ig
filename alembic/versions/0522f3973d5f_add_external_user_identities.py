"""add external user identities

Revision ID: 0522f3973d5f
Revises: a2b0976bdf16
Create Date: 2026-10-27 18:07:22.492500

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0522f3973d5f'
down_revision: Union[str, Sequence[str], None] = 'a2b0976bdf16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_identities",
        sa.Column(
            "identity_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "issuer",
            sa.String(length=512),
            nullable=False,
        ),
        sa.Column(
            "subject",
            sa.String(length=512),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
        ),
        sa.PrimaryKeyConstraint(
            "identity_id"
        ),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name=(
                "uq_user_identities_"
                "issuer_subject"
            ),
        ),
    )

    op.create_index(
        op.f(
            "ix_user_identities_user_id"
        ),
        "user_identities",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f(
            "ix_user_identities_user_id"
        ),
        table_name="user_identities",
    )

    op.drop_table(
        "user_identities"
    )
