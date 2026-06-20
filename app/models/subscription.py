"""구독(Subscription) 모델.

외부 서비스 사용자 한 명의 요금제 구독 상태를 관리한다.
상태 머신: TRIAL → ACTIVE → PAST_DUE → SUSPENDED → EXPIRED
                          ↘ CANCELED → EXPIRED
구독 취소는 즉시 종료가 아닌 CANCELED 상태로 전환 후 만료일에 EXPIRED로 이행한다.
서비스+사용자 당 1개 구독 규칙을 부분 유니크 인덱스로 DB 수준에서 강제한다.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import SubscriptionStatus


class Subscription(TimestampMixin, Base):
    """사용자 구독 레코드.

    빌링키와 카드 정보(customer_key, billing_key_encrypted, billing_key_hash, card_info)는
    cards 테이블로 이동했으며, 구독은 card_id FK로 해당 카드를 참조한다.
    모든 시각은 UTC로 저장한다.
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))   # 소속 서비스(구독 있으면 서비스 삭제 불가)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))         # 가입 요금제(구독 있으면 요금제 삭제 불가)
    external_user_id: Mapped[str] = mapped_column(String(255))   # 외부 서비스 측 사용자 식별자(내부 users 테이블과 무관)
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cards.id", ondelete="RESTRICT"), index=True, nullable=True)
    # 결제에 사용할 등록 카드(cards 테이블 참조).
    # 카드 삭제(spec §6.1) 시 CANCELED/EXPIRED 구독은 card_id가 NULL로 초기화된다.
    # TRIAL/ACTIVE/PAST_DUE/SUSPENDED/EXTENDED 구독이 있으면 카드 삭제 자체가 차단된다.
    status: Mapped[str] = mapped_column(String(20), default=SubscriptionStatus.ACTIVE)              # 구독 상태 머신 현재 위치
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))   # 현재 결제 주기 시작(UTC)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))     # 현재 결제 주기 종료=접근 만료 시각(UTC)
    next_billing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 다음 자동결제 예정 시각(UTC); 스케줄러가 이 값으로 조회
    retry_count: Mapped[int] = mapped_column(Integer, default=0)   # PAST_DUE 상태에서 결제 재시도 누적 횟수
    # SUSPENDED 진입 시각 — 대기 일수 초과 시 EXPIRED 판정 기준.
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # 서비스+사용자 당 1개 구독 규칙 (EXPIRED만 제외 → 재구독 허용)
        Index(
            "uq_subscriptions_one_per_user",
            "service_id", "external_user_id",
            unique=True,
            postgresql_where=text(
                "status IN ('TRIAL','ACTIVE','PAST_DUE','SUSPENDED','CANCELED','EXTENDED')"),
        ),
        Index("ix_subscriptions_due", "status", "next_billing_at"),  # 스케줄러의 결제 대상 조회 성능용 복합 인덱스
        # 어드민 목록·대시보드의 서비스 스코프 필터용(감사 Phase 3 — 성능 M1).
        # 부분 유니크(uq_subscriptions_one_per_user)는 EXPIRED 행을 제외해 전체 스코프 조회에 못 쓴다.
        Index("ix_subscriptions_service_id", "service_id"),
        # 배치의 취소/비자동갱신 만료 조회 + 만료임박 레일용(감사 Phase 3 — 성능 M1)
        Index("ix_subscriptions_status_period_end", "status", "current_period_end"),
    )
