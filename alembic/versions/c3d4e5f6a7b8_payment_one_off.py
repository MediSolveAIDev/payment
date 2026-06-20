"""payment one_off: kind/service_id/external_user_id + subscription_id nullable

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('payments', sa.Column('kind', sa.String(length=20),
                  nullable=False, server_default='SUBSCRIPTION'))
    op.add_column('payments', sa.Column('service_id', sa.Uuid(), nullable=True))
    op.add_column('payments', sa.Column('external_user_id', sa.String(length=255), nullable=True))
    op.alter_column('payments', 'subscription_id', existing_type=sa.Uuid(), nullable=True)
    op.execute("""
        UPDATE payments p SET service_id = s.service_id,
                              external_user_id = s.external_user_id
        FROM subscriptions s WHERE p.subscription_id = s.id
    """)
    op.alter_column('payments', 'service_id', existing_type=sa.Uuid(), nullable=False)
    op.create_foreign_key('fk_payments_service_id_services', 'payments', 'services',
                          ['service_id'], ['id'], ondelete='RESTRICT')
    op.create_index('ix_payments_service_id', 'payments', ['service_id'])
    op.create_index('ix_payments_kind', 'payments', ['kind'])


def downgrade() -> None:
    op.drop_index('ix_payments_kind', table_name='payments')
    op.drop_index('ix_payments_service_id', table_name='payments')
    op.drop_constraint('fk_payments_service_id_services', 'payments', type_='foreignkey')
    op.alter_column('payments', 'subscription_id', existing_type=sa.Uuid(), nullable=False)
    op.drop_column('payments', 'external_user_id')
    op.drop_column('payments', 'service_id')
    op.drop_column('payments', 'kind')
