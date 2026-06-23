"""services.toss_secret_key_encrypted 컬럼 추가 — 서비스별 토스 시크릿 키 AES 암호화 보관

서비스별로 별도 토스 시크릿 키를 AES-GCM으로 암호화해 저장한다.
미설정(NULL)이면 결제·승인·갱신 요청이 TOSS_KEY_NOT_CONFIGURED로 거부된다.
평문은 저장·응답·감사로그 어디에도 남기지 않는다.

Revision ID: acfb71bc40a6
Revises: e1f2a3b4c5d7
Create Date: 2026-06-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'acfb71bc40a6'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 서비스별 토스 시크릿 보관 컬럼(AES 암호문, nullable). 기존 행은 NULL → 키 등록 전까지 결제 거부.
    op.add_column("services", sa.Column("toss_secret_key_encrypted", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("services", "toss_secret_key_encrypted")
