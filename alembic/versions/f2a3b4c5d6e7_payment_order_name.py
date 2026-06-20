"""결제(payments)에 상품명(order_name) 컬럼 추가

결제정보 화면에 상품명을 표시하기 위해 토스 orderName을 영구 보관한다.
- 단건결제: 클라이언트가 전달한 order_name
- 구독결제: 요금제명(plan.name)
기존 결제 행에는 값이 없으므로 nullable(NULL 허용)로 추가한다.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 상품명(토스 orderName) 저장 컬럼. 과거 데이터 호환을 위해 nullable.
    op.add_column('payments', sa.Column('order_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('payments', 'order_name')
