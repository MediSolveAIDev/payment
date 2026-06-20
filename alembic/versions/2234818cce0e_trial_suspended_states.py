"""trial suspended states

Revision ID: 2234818cce0e
Revises: 3501c20729e0
Create Date: 2026-06-06 09:17:45.858425

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2234818cce0e'
down_revision: Union[str, Sequence[str], None] = '3501c20729e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('plans', sa.Column('trial_enabled', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('plans', sa.Column('trial_days', sa.Integer(), nullable=True))
    op.add_column('subscriptions', sa.Column('suspended_at', sa.DateTime(timezone=True), nullable=True))
    # '1구독' 부분 유니크 인덱스의 open-status 집합에 TRIAL·SUSPENDED 추가
    op.drop_index("uq_subscriptions_one_per_user", table_name="subscriptions")
    op.create_index(
        "uq_subscriptions_one_per_user", "subscriptions",
        ["service_id", "external_user_id"], unique=True,
        postgresql_where=sa.text(
            "status IN ('TRIAL','ACTIVE','PAST_DUE','SUSPENDED','CANCELED')"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_subscriptions_one_per_user", table_name="subscriptions")
    op.create_index(
        "uq_subscriptions_one_per_user", "subscriptions",
        ["service_id", "external_user_id"], unique=True,
        postgresql_where=sa.text("status IN ('ACTIVE','PAST_DUE','CANCELED')"))
    op.drop_column('subscriptions', 'suspended_at')
    op.drop_column('plans', 'trial_days')
    op.drop_column('plans', 'trial_enabled')
