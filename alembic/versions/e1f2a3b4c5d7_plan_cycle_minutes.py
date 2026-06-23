"""plans.cycle_minutes 컬럼 추가 — MINUTE 주기 요금제의 분 수 보관

MINUTE 주기(BillingCycle.MINUTE)일 때만 사용하는 분 단위 값.
최소 5분. 그 외 주기에서는 NULL. 테스트·비운영 전용 기능.

Revision ID: e1f2a3b4c5d7
Revises: d3e4f5a6b7c8
Create Date: 2026-06-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d7'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # MINUTE 주기 요금제의 분 수 보관 컬럼(nullable). 기존 행은 NULL.
    op.add_column("plans", sa.Column("cycle_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("plans", "cycle_minutes")
