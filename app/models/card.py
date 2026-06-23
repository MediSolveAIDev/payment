"""카드(Card) 모델 — 결제수단 보관함(vault).

(service_id, external_user_id)당 1건. 토스 빌링키를 암호화 보관하고,
구독·단건결제가 이 카드를 참조해 결제한다. 카드 등록 API에서만 생성/교체된다.
"""
import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Card(TimestampMixin, Base):
    """결제수단 보관함(vault) — 토스 빌링키를 암호화 보관하는 카드 레코드.

    (service_id, external_user_id) 쌍당 1건만 허용하며, 카드 교체 시 기존 레코드를
    덮어쓴다. 카드 삭제는 활성 구독이 없을 때만 허용한다(서비스 레이어에서 강제).
    """

    __tablename__ = "cards"
    __table_args__ = (
        # (서비스, 외부 사용자) 쌍당 카드는 1건 — 중복 등록 방지(vault 정책)
        UniqueConstraint("service_id", "external_user_id", name="uq_cards_service_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)  # 카드 고유 ID(UUID, 자동 생성)
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), index=True)               # 카드가 속한 서비스(삭제 불가 — RESTRICT)
    external_user_id: Mapped[str] = mapped_column(String(255))                    # 외부 서비스의 사용자 ID=이메일(소문자 정규화, vault 키의 절반)
    customer_key: Mapped[str] = mapped_column(String(300))                        # 토스 customerKey(빌링 인증에 사용)
    billing_key_encrypted: Mapped[str] = mapped_column(String(1024))              # 토스 빌링키 — AES-GCM 암호화 보관(평문 저장 안 함)
    billing_key_hash: Mapped[str] = mapped_column(String(64), index=True)         # 빌링키 SHA-256 해시(중복 탐지·조회용, 인증은 암호문으로)
    card_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)          # 카드 표시 정보(마스킹된 번호·발급사 등, 토스 응답 부분 저장)
    # 활성/비활성 상태 — False면 이 카드로의 모든 결제(구독 자동연장·첫구독·재시도·일반결제)를 차단한다.
    # 관리자가 어드민에서 토글하며, 비활성화해도 구독 상태는 즉시 바꾸지 않고 다음 결제 시 실패로 처리한다.
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False, default=True)
