"""핵심 조회 컬럼 인덱스 6종 추가 (감사 Phase 3 — 성능 M1)

감사에서 확인된 풀스캔 경로를 인덱스로 해소한다:
- payments(status, requested_at)      : 정산 스윕(5분마다) + 결제목록 기본 정렬
- payments(service_id, approved_at)   : 대시보드 매출 집계 + 월별 정산
- audit_logs(created_at)              : 감사 목록 정렬 + 대시보드 기간 집계
- audit_logs(target_type, target_id)  : 대시보드 target_id IN(...) + 서비스 이벤트
- subscriptions(service_id)           : 어드민 목록·대시보드 스코프 필터
  (부분 유니크 uq_subscriptions_one_per_user는 EXPIRED 제외 조건이라 전체 조회에 못 씀)
- subscriptions(status, current_period_end) : 배치 만료 조회 + 만료임박 레일

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """인덱스 6종 생성. 데이터가 큰 운영 환경에서는 잠금 시간을 고려해
    트래픽이 적은 시간대에 적용을 권장한다."""
    op.create_index('ix_payments_status_requested', 'payments',
                    ['status', 'requested_at'])
    op.create_index('ix_payments_service_approved', 'payments',
                    ['service_id', 'approved_at'])
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'])
    op.create_index('ix_audit_logs_target', 'audit_logs',
                    ['target_type', 'target_id'])
    op.create_index('ix_subscriptions_service_id', 'subscriptions', ['service_id'])
    op.create_index('ix_subscriptions_status_period_end', 'subscriptions',
                    ['status', 'current_period_end'])


def downgrade() -> None:
    op.drop_index('ix_subscriptions_status_period_end', table_name='subscriptions')
    op.drop_index('ix_subscriptions_service_id', table_name='subscriptions')
    op.drop_index('ix_audit_logs_target', table_name='audit_logs')
    op.drop_index('ix_audit_logs_created_at', table_name='audit_logs')
    op.drop_index('ix_payments_service_approved', table_name='payments')
    op.drop_index('ix_payments_status_requested', table_name='payments')
