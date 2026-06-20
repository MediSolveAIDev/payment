"""허용 IP 목록에서 루프백(127.0.0.1/::1) 제거

127.0.0.1(IPv4)·::1(IPv6)은 같은 서버(로컬) 환경이라 IP 화이트리스트와 무관하게
애플리케이션에서 항상 허용한다. 따라서 목록에 보관할 필요가 없으므로 기존에 저장된
값에서도 제거한다(데이터 정리). 대상:
- services.allowed_ips          (서비스 API 허용 IP)
- global_settings.admin_allowed_ips (어드민 접속 허용 IP)

두 컬럼 모두 JSONB 배열이며, jsonb_array_elements로 펼친 뒤 루프백을 제외해 재집계한다.

Revision ID: e1f2a3b4c5d6
Revises: d2e3f4a5b6c7
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels = None
depends_on = None

# (테이블, JSONB 컬럼) — 루프백을 제거할 허용 IP 목록들
_TARGETS = (("services", "allowed_ips"), ("global_settings", "admin_allowed_ips"))


def upgrade() -> None:
    for table, col in _TARGETS:
        # 배열을 원소로 펼쳐 루프백을 제외하고 재집계. 원소가 없으면 '[]'.
        # e #>> '{}' 는 jsonb 스칼라 문자열의 텍스트 값을 추출한다.
        op.execute(f"""
            UPDATE {table}
            SET {col} = COALESCE(
                (SELECT jsonb_agg(e)
                   FROM jsonb_array_elements(COALESCE({col}, '[]'::jsonb)) AS e
                  WHERE e #>> '{{}}' NOT IN ('127.0.0.1', '::1')),
                '[]'::jsonb)
        """)


def downgrade() -> None:
    # 데이터 정리 마이그레이션 — 되돌리지 않는다(루프백은 항상 허용이라 복원 불필요).
    pass
