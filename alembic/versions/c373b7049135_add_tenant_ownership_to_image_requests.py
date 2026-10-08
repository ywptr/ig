"""add tenant ownership to image requests

Revision ID: c373b7049135
Revises: 10f1b5a6d4e6
Create Date: 2026-10-08 16:35:50.963597

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c373b7049135'
down_revision: Union[str, Sequence[str], None] = '10f1b5a6d4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        "image_requests",
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            nullable=True,
        ),
    )

    connection = op.get_bind()

    default_tenant_id = connection.execute(
        sa.text(
            """
            SELECT tenant_id
            FROM tenants
            WHERE slug = 'default'
            """
        )
    ).scalar_one()

    connection.execute(
        sa.text(
            """
            UPDATE image_requests
            SET tenant_id = :tenant_id
            WHERE tenant_id IS NULL
            """
        ),
        {
            "tenant_id": default_tenant_id,
        },
    )

    op.alter_column(
        "image_requests",
        "tenant_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )

    op.create_index(
        op.f("ix_image_requests_tenant_id"),
        "image_requests",
        ["tenant_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_image_requests_tenant_id_tenants",
        "image_requests",
        "tenants",
        ["tenant_id"],
        ["tenant_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint(
        "fk_image_requests_tenant_id_tenants",
        "image_requests",
        type_="foreignkey",
    )

    op.drop_index(
        op.f("ix_image_requests_tenant_id"),
        table_name="image_requests",
    )

    op.drop_column(
        "image_requests",
        "tenant_id",
    )