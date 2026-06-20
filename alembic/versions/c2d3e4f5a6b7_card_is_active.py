"""cards.is_active 컬럼 추가 — 결제수단 활성/비활성 토글

카드를 비활성화하면 이 카드로의 모든 결제(구독 자동연장·첫구독·재시도·일반결제)가
차단된다. 기존 카드는 모두 활성(true)으로 백필된다.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """cards에 is_active 불리언 컬럼 추가(NOT NULL, 기본 true).

    server_default='true'로 기존 행은 모두 활성으로 백필된다.
    """
    op.add_column(
        'cards',
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'),
                  nullable=False),
    )


def downgrade() -> None:
    """is_active 컬럼 제거."""
    op.drop_column('cards', 'is_active')
