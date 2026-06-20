"""global_settings에 보안/결제 정책 런타임 컬럼 추가

런타임(어드민 '전체 설정')에서 재배포 없이 조정할 수 있도록 다음을 추가한다:
- max_failed_logins     : 어드민 로그인 연속 실패 잠금 임계치(기본 5)
- account_lock_minutes  : 잠금 지속 시간(분, 기본 15)
- one_off_max_amount    : 단건 결제 1회 최대 금액(원, 기본 1억)

기존 단일 행(id=1)에는 server_default로 즉시 기본값이 채워진다.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('global_settings',
                  sa.Column('max_failed_logins', sa.Integer(),
                            server_default='5', nullable=False))
    op.add_column('global_settings',
                  sa.Column('account_lock_minutes', sa.Integer(),
                            server_default='15', nullable=False))
    op.add_column('global_settings',
                  sa.Column('one_off_max_amount', sa.BigInteger(),
                            server_default='100000000', nullable=False))


def downgrade() -> None:
    op.drop_column('global_settings', 'one_off_max_amount')
    op.drop_column('global_settings', 'account_lock_minutes')
    op.drop_column('global_settings', 'max_failed_logins')
