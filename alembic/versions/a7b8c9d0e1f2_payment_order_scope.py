"""payments.order_id 서비스 스코프 분리 + toss_order_id 추가 (감사 Phase 2 — 보안 M-1)

order_id 전역 유니크 → (service_id, order_id) 복합 유니크로 변경해
타 서비스 주문번호 선점(스쿼팅)·존재 탐지를 차단한다.
토스에 보내는 전역 고유 ID는 신규 toss_order_id 컬럼이 담당한다
(시스템 전체가 토스 계정 하나를 공유하므로 토스 측 orderId는 전역 고유 필요).
기존 행은 toss_order_id = order_id로 백필(과거 결제는 order_id 그대로 토스에 전달했음).

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """toss_order_id 추가(기존 행은 order_id로 백필) 후 유니크 제약 재구성."""
    op.add_column('payments', sa.Column('toss_order_id', sa.String(64), nullable=True))
    # 기존 결제는 order_id를 그대로 토스에 전달했으므로 동일 값으로 백필
    op.execute("UPDATE payments SET toss_order_id = order_id")
    op.alter_column('payments', 'toss_order_id', nullable=False)
    op.create_unique_constraint('uq_payments_toss_order_id', 'payments', ['toss_order_id'])
    # 전역 유니크 해제 → 서비스 스코프 복합 유니크로 교체
    op.drop_constraint('uq_payments_order_id', 'payments', type_='unique')
    op.create_unique_constraint('uq_payments_service_order', 'payments',
                                ['service_id', 'order_id'])


def downgrade() -> None:
    """복합 유니크 → 전역 유니크 복원 + toss_order_id 제거.

    주의: 서비스 간 order_id 충돌 데이터가 생긴 뒤에는 전역 유니크 복원이 실패할 수 있다.
    """
    op.drop_constraint('uq_payments_service_order', 'payments', type_='unique')
    op.create_unique_constraint('uq_payments_order_id', 'payments', ['order_id'])
    op.drop_constraint('uq_payments_toss_order_id', 'payments', type_='unique')
    op.drop_column('payments', 'toss_order_id')
