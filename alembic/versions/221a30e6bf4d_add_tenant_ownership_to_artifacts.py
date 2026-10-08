"""add tenant ownership to artifacts

Revision ID: 221a30e6bf4d
Revises: c373b7049135
Create Date: 2026-10-08 17:12:32.560600

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '221a30e6bf4d'
down_revision: Union[str, Sequence[str], None] = 'c373b7049135'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # 1. Add tenant_id as nullable for existing rows.
    op.add_column(
        "artifacts",
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            nullable=True,
        ),
    )

    # 2. Find the bootstrap/default tenant.
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

    # 3. Backfill existing artifacts.
    connection.execute(
        sa.text(
            """
            UPDATE artifacts
            SET tenant_id = :tenant_id
            WHERE tenant_id IS NULL
            """
        ),
        {
            "tenant_id": default_tenant_id,
        },
    )

    # 4. Enforce tenant ownership.
    op.alter_column(
        "artifacts",
        "tenant_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )

    # 5. Index + named FK.
    op.create_index(
        op.f("ix_artifacts_tenant_id"),
        "artifacts",
        ["tenant_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_artifacts_tenant_id_tenants",
        "artifacts",
        "tenants",
        ["tenant_id"],
        ["tenant_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint(
        "fk_artifacts_tenant_id_tenants",
        "artifacts",
        type_="foreignkey",
    )

    op.drop_index(
        op.f("ix_artifacts_tenant_id"),
        table_name="artifacts",
    )

    op.drop_column(
        "artifacts",
        "tenant_id",
    )
    # ### end Alembic commands ###
