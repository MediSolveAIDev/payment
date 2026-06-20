"""services.notification_url 컬럼 추가 — 서비스 알림(아웃고잉 웹훅) 수신 URL

구독·결제·카드·요금제 상태 변화 시 이 URL로 알림을 POST한다(요청 016).
NULL이면 알림을 보내지 않는다.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-06-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """services에 notification_url(nullable) 추가."""
    op.add_column('services',
                  sa.Column('notification_url', sa.String(length=512), nullable=True))


def downgrade() -> None:
    """notification_url 컬럼 제거."""
    op.drop_column('services', 'notification_url')
