"""add tenant provider configuration

Revision ID: 4309b22cb71a
Revises: ef2eee644003
Create Date: 2026-10-14 15:20:02.410381

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4309b22cb71a'
down_revision: Union[str, Sequence[str], None] = 'ef2eee644003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant_provider_configs",
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "provider",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "encrypted_api_key",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "default_model",
            sa.String(length=100),
            nullable=True,
        ),
        sa.Column(
            "config_json",
            sa.JSON(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.tenant_id"],
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "provider",
        ),
    )


def downgrade() -> None:
    op.drop_table(
        "tenant_provider_configs"
    )
