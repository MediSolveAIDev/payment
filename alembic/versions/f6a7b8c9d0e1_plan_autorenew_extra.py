"""plans 테이블에 auto_renew + extra_info 컬럼 추가 (요청 013 Task 7)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql  # JSONB 사용

# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """plans 테이블에 auto_renew·extra_info 컬럼 추가.

    auto_renew: False이면 첫 주기 후 자동연장 없음(기존 데이터는 모두 True 유지).
    extra_info: 서비스 측 요금제 설명용 JSONB key/value(기존 데이터는 빈 객체).
    """
    op.add_column('plans', sa.Column(
        'auto_renew', sa.Boolean(), server_default='true', nullable=False))  # 자동결제 여부
    op.add_column('plans', sa.Column(
        'extra_info', postgresql.JSONB(), server_default='{}', nullable=False))  # 추가 정보 JSON


def downgrade() -> None:
    """추가한 두 컬럼을 제거한다."""
    op.drop_column('plans', 'extra_info')
    op.drop_column('plans', 'auto_renew')
