"""cancel policy(services) + cancel fields(payments)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-09
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('services', sa.Column('cancellation_enabled', sa.Boolean(),
                  nullable=False, server_default='true'))
    op.add_column('services', sa.Column('cancellation_fee_percent', sa.Integer(),
                  nullable=False, server_default='0'))
    op.add_column('payments', sa.Column('canceled_amount', sa.BigInteger(), nullable=True))
    op.add_column('payments', sa.Column('cancel_fee', sa.BigInteger(), nullable=True))
    op.add_column('payments', sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('payments', 'canceled_at')
    op.drop_column('payments', 'cancel_fee')
    op.drop_column('payments', 'canceled_amount')
    op.drop_column('services', 'cancellation_fee_percent')
    op.drop_column('services', 'cancellation_enabled')
