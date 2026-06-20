"""카드 보관함: cards 생성 + subscriptions 빌링키 컬럼→card_id 이동

운영 전 도입이라 기존 구독 데이터 보존 불필요. dev/test DB에 남은 subscriptions 행은
리셋 전제(card_id NOT NULL 추가가 기존 행과 충돌). 신규 환경은 깨끗하게 적용된다.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-06-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """cards 테이블 생성 후 subscriptions에서 빌링키 컬럼 제거 + card_id 참조 추가."""

    # --- cards 테이블 신설 ---
    # 서비스별·사용자별 카드(빌링키)를 독립적으로 보관하는 테이블.
    # billing_key_encrypted: AES 암호화된 토스 빌링키, billing_key_hash: 중복 검색용 SHA-256.
    op.create_table(
        'cards',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'service_id',
            sa.Uuid(),
            sa.ForeignKey('services.id', ondelete='RESTRICT'),
            nullable=False,
        ),
        sa.Column('external_user_id', sa.String(length=255), nullable=False),
        sa.Column('customer_key', sa.String(length=300), nullable=False),
        sa.Column('billing_key_encrypted', sa.String(length=1024), nullable=False),
        sa.Column('billing_key_hash', sa.String(length=64), nullable=False),
        sa.Column('card_info', JSONB(astext_type=sa.Text()), nullable=True),
        # created_at / updated_at: TimestampMixin 패턴과 동일하게 server_default=sa.text('now()')
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cards')),
        sa.UniqueConstraint(
            'service_id', 'external_user_id', name='uq_cards_service_user'
        ),
    )
    # 서비스별 카드 목록 조회용 인덱스
    op.create_index('ix_cards_service_id', 'cards', ['service_id'])
    # 빌링키 해시 기반 중복/조회용 인덱스
    op.create_index('ix_cards_billing_key_hash', 'cards', ['billing_key_hash'])

    # --- subscriptions 테이블 정리 ---
    # 운영 전 도입이므로 기존 결제·구독 데이터를 전체 삭제하고 스키마를 정리한다.
    # payments가 subscriptions를 FK로 참조하므로 자식 테이블(payments)을 먼저 삭제.
    op.execute('DELETE FROM payments')       # 운영 전 — 기존 결제 데이터 없음 전제
    op.execute('DELETE FROM subscriptions')  # 운영 전 — 기존 구독 데이터 없음 전제

    # 구독 테이블에서 빌링키 관련 컬럼 제거(cards 테이블로 이동)
    op.drop_index(op.f('ix_subscriptions_billing_key_hash'), table_name='subscriptions')
    op.drop_column('subscriptions', 'card_info')
    op.drop_column('subscriptions', 'billing_key_hash')
    op.drop_column('subscriptions', 'billing_key_encrypted')
    op.drop_column('subscriptions', 'customer_key')

    # 구독 테이블에 카드 참조 컬럼 추가
    op.add_column(
        'subscriptions',
        sa.Column('card_id', sa.Uuid(), nullable=False),
    )
    # 구독 → 카드 외래키 제약 (카드 삭제 방지)
    op.create_foreign_key(
        'fk_subscriptions_card',
        'subscriptions',
        'cards',
        ['card_id'],
        ['id'],
        ondelete='RESTRICT',
    )
    # card_id 기반 조회용 인덱스
    op.create_index('ix_subscriptions_card_id', 'subscriptions', ['card_id'])


def downgrade() -> None:
    """card_id 제거 + 빌링키 컬럼 복원 후 cards 테이블 삭제."""

    # subscriptions card_id 관련 제거
    op.drop_index('ix_subscriptions_card_id', table_name='subscriptions')
    op.drop_constraint('fk_subscriptions_card', 'subscriptions', type_='foreignkey')
    op.drop_column('subscriptions', 'card_id')

    # 빌링키 컬럼 복원 (nullable: 기존 데이터 없으므로 NULL 허용으로 복원)
    op.add_column(
        'subscriptions',
        sa.Column('customer_key', sa.String(length=300), nullable=True),
    )
    op.add_column(
        'subscriptions',
        sa.Column('billing_key_encrypted', sa.String(length=1024), nullable=True),
    )
    op.add_column(
        'subscriptions',
        sa.Column('billing_key_hash', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'subscriptions',
        sa.Column('card_info', JSONB(astext_type=sa.Text()), nullable=True),
    )
    # billing_key_hash 인덱스 복원
    op.create_index(
        op.f('ix_subscriptions_billing_key_hash'),
        'subscriptions',
        ['billing_key_hash'],
    )

    # cards 테이블 삭제
    op.drop_index('ix_cards_billing_key_hash', table_name='cards')
    op.drop_index('ix_cards_service_id', table_name='cards')
    op.drop_table('cards')
