"""rename key field in tenant provider config to reflect that a key can in fact be one-or-more key-value pairs

Revision ID: 79381a2c2e4a
Revises: 4309b22cb71a
Create Date: 2026-10-14 15:53:31.812006

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '79381a2c2e4a'
down_revision: Union[str, Sequence[str], None] = '4309b22cb71a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """ Rename the 'encrypted_api_key' column to 'encrypted_credentials' in the 'tenant_provider_configs' table. """
    op.alter_column(
        "tenant_provider_configs",
        "encrypted_api_key",
        new_column_name="encrypted_credentials",
    )


def downgrade() -> None:
    """ Rename the 'encrypted_credentials' column back to 'encrypted_api_key' in the 'tenant_provider_configs' table. """
    op.alter_column(
        "tenant_provider_configs",
        "encrypted_credentials",
        new_column_name="encrypted_api_key",
    )
