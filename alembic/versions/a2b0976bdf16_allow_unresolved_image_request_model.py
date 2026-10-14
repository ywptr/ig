"""Allow unresolved image request model

Revision ID: a2b0976bdf16
Revises: 79381a2c2e4a
Create Date: 2026-10-14 17:33:42.174222

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b0976bdf16'
down_revision: Union[str, Sequence[str], None] = '79381a2c2e4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "image_requests",
        "model",
        existing_type=sa.String(length=100),
        nullable=True,
    )

def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        UPDATE image_requests
        SET model = ''
        WHERE model IS NULL
        """
    )

    op.alter_column(
        "image_requests",
        "model",
        existing_type=sa.String(length=100),
        nullable=False,
    )