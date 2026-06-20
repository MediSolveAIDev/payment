"""global_settings 단일 행 테이블 — 자동결제 재시도·어드민IP·결제서버 킬스위치(요청 013)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """global_settings 테이블 생성. id=1 단일 행으로 전역설정을 관리한다."""
    op.create_table(
        'global_settings',
        sa.Column('id', sa.Integer(), nullable=False),                                                           # 싱글톤 행(항상 1)
        sa.Column('retry_limit', sa.Integer(), server_default='4', nullable=False),                              # 자동결제 재시도 횟수
        sa.Column('retry_interval_hours', sa.Integer(), server_default='12', nullable=False),                    # 재시도 간격(시간)
        sa.Column('suspended_grace_days', sa.Integer(), server_default='30', nullable=False),                    # SUSPENDED 유예(일)
        sa.Column('admin_allowed_ips', postgresql.JSONB(), server_default='[]', nullable=False),                 # 어드민 허용 IP(빈=제한없음)
        sa.Column('server_disabled', sa.Boolean(), server_default='false', nullable=False),                      # 결제서버 킬스위치
        sa.Column('disabled_reason', sa.String(length=500), nullable=True),                                      # 비활성화 사유
        sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),                                     # 비활성화 시각(UTC)
        sa.Column('disabled_by', postgresql.UUID(as_uuid=True), nullable=True),                                  # 비활성화한 관리자 user id
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),   # 생성 시각(UTC)
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),   # 수정 시각(UTC)
        sa.PrimaryKeyConstraint('id', name=op.f('pk_global_settings')),
    )


def downgrade() -> None:
    """global_settings 테이블 삭제."""
    op.drop_table('global_settings')
