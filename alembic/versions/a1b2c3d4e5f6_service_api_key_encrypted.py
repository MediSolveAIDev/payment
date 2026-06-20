"""service api_key_encrypted

Revision ID: a1b2c3d4e5f6
Revises: 794d3b3fcf7c
Create Date: 2026-06-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '794d3b3fcf7c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('services',
                  sa.Column('api_key_encrypted', sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column('services', 'api_key_encrypted')
