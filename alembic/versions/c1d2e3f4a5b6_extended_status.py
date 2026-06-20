"""extended(연장처리) status — 부분 유니크 인덱스 open-status 집합에 EXTENDED 추가

구독 만료일 연장 기능(요청): 운영자가 만료일을 연장하면 상태가 EXTENDED(연장처리)가 된다.
EXTENDED는 '열린 구독'(서비스+사용자 당 1개 규칙)에 포함되어야 하므로,
부분 유니크 인덱스 uq_subscriptions_one_per_user의 where 절에 'EXTENDED'를 추가한다.
(status는 String 컬럼이라 enum 타입 변경은 불필요 — 인덱스 where만 갱신)

Revision ID: c1d2e3f4a5b6
Revises: b8c9d0e1f2a3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPEN_WITH_EXTENDED = (
    "status IN ('TRIAL','ACTIVE','PAST_DUE','SUSPENDED','CANCELED','EXTENDED')")
_OPEN_WITHOUT_EXTENDED = (
    "status IN ('TRIAL','ACTIVE','PAST_DUE','SUSPENDED','CANCELED')")


def upgrade() -> None:
    """부분 유니크 인덱스 재생성 — open-status 집합에 EXTENDED 포함."""
    op.drop_index("uq_subscriptions_one_per_user", table_name="subscriptions")
    op.create_index(
        "uq_subscriptions_one_per_user", "subscriptions",
        ["service_id", "external_user_id"], unique=True,
        postgresql_where=sa.text(_OPEN_WITH_EXTENDED))


def downgrade() -> None:
    """EXTENDED 제외 집합으로 복원. (EXTENDED 구독이 남아있으면 운영자가 먼저 정리해야 함)"""
    op.drop_index("uq_subscriptions_one_per_user", table_name="subscriptions")
    op.create_index(
        "uq_subscriptions_one_per_user", "subscriptions",
        ["service_id", "external_user_id"], unique=True,
        postgresql_where=sa.text(_OPEN_WITHOUT_EXTENDED))
