"""subscriptions.card_id 를 nullable로 변경

카드 삭제(spec §6.1) 시 CANCELED/EXPIRED 구독은 결제 흐름에서 이탈한 상태이므로
card_id 참조를 NULL로 초기화하고 카드를 삭제할 수 있어야 한다.
TRIAL/ACTIVE/PAST_DUE/SUSPENDED/EXTENDED 구독이 있으면 카드 삭제 자체가 앱 레이어에서 차단된다.

Revision ID: b1c2d3e4f5a6
Revises: a3b4c5d6e7f8
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """subscriptions.card_id 컬럼을 NOT NULL → NULL 허용으로 변경.

    CANCELED/EXPIRED 구독은 카드 삭제 시 card_id가 NULL로 초기화된다.
    billing-active 구독(TRIAL/ACTIVE/PAST_DUE/SUSPENDED/EXTENDED)이 있으면
    앱 레이어에서 미리 차단하므로 실제로 NULL이 되는 경우는 CANCELED/EXPIRED뿐이다.
    """
    op.alter_column(
        'subscriptions',
        'card_id',
        existing_type=sa.Uuid(),
        nullable=True,   # NOT NULL → NULL 허용
    )


def downgrade() -> None:
    """card_id를 다시 NOT NULL로 되돌린다.

    NULL 값이 존재하면 되돌리기 실패하므로, 필요 시 NULL 행을 먼저 정리해야 한다.
    """
    # NULL 행이 있으면 RESTRICT FK 제약과 NOT NULL 제약 복원이 불가하므로 경고
    op.alter_column(
        'subscriptions',
        'card_id',
        existing_type=sa.Uuid(),
        nullable=False,  # NULL 허용 → NOT NULL 복원
    )
