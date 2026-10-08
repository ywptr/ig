"""add tenant ownership to jobs

Revision ID: 10f1b5a6d4e6
Revises: e217d0603ccc
Create Date: 2026-10-08 15:15:46.048813

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '10f1b5a6d4e6'
down_revision: Union[str, Sequence[str], None] = 'e217d0603ccc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    # 1. Add tenant_id as nullable so existing rows remain valid.
    op.add_column(
        "jobs",
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            nullable=True,
        ),
    )

    # 2. Resolve the bootstrap/default tenant.
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

    # 3. Backfill all existing jobs.
    connection.execute(
        sa.text(
            """
            UPDATE jobs
            SET tenant_id = :tenant_id
            WHERE tenant_id IS NULL
            """
        ),
        {
            "tenant_id": default_tenant_id,
        },
    )

    # 4. tenant_id is now mandatory.
    op.alter_column(
        "jobs",
        "tenant_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )

    # 5. Add index + FK after the backfill.
    op.create_index(
        op.f("ix_jobs_tenant_id"),
        "jobs",
        ["tenant_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_jobs_tenant_id_tenants",
        "jobs",
        "tenants",
        ["tenant_id"],
        ["tenant_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint(
        "fk_jobs_tenant_id_tenants",
        "jobs",
        type_="foreignkey",
    )

    op.drop_index(
        op.f("ix_jobs_tenant_id"),
        table_name="jobs",
    )

    op.drop_column(
        "jobs",
        "tenant_id",
    )