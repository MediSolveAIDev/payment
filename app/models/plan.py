"""구독 요금제(Plan) 모델.

서비스별로 생성되며, 가격·주기·할인·체험 정책을 정의한다.
구독이 하나라도 연결된 요금제는 삭제할 수 없다(ondelete="RESTRICT").
"""
import uuid

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB  # extra_info JSONB 컬럼용 (요청 013)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import DiscountType, FirstPaymentType, PlanStatus


class Plan(TimestampMixin, Base):
    """구독 요금제 정의.

    금액은 KRW 정수(원 단위)로 저장한다. 할인 계산은 billing_math 모듈이 담당한다.
    billing_cycle=DAY일 때 cycle_days가 필수이며, 나머지 주기는 cycle_days를 무시한다.
    첫 결제 혜택(first_payment_*)과 상시 할인(recurring_discount_*)은 독립적으로
    중첩 적용될 수 있다.
    """

    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))  # 소속 서비스(구독 있으면 삭제 불가)
    name: Mapped[str] = mapped_column(String(100))                                                  # 요금제 표시명
    price: Mapped[int] = mapped_column(BigInteger)  # KRW 정수
    currency: Mapped[str] = mapped_column(String(3), default="KRW")  # 통화 코드(현재 KRW만 사용)
    billing_cycle: Mapped[str] = mapped_column(String(10))           # BillingCycle enum 값(YEAR/MONTH/WEEK/DAY)
    cycle_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # DAY 주기일 때 실제 일수; 나머지는 NULL
    cycle_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # MINUTE 주기일 때 실제 분(5 이상); 나머지는 NULL. 테스트용·비운영 전용
    first_payment_type: Mapped[str] = mapped_column(String(20), default=FirstPaymentType.NONE)      # 첫 결제 혜택 유형
    first_payment_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)              # 첫 결제 할인 값(원 또는 %)
    # 상시 할인(요청 003): 모든 정기 결제에 적용. 첫 결제는 첫구독 할인과 중첩.
    # 유형 NONE / DISCOUNT_AMOUNT(원) / DISCOUNT_PERCENT(%).
    recurring_discount_type: Mapped[str] = mapped_column(
        String(20), default=DiscountType.NONE, server_default=DiscountType.NONE)
    recurring_discount_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 상시 할인 값(원 또는 %)
    # 체험(Trial): 결제정보 등록 시 체험 신청 가능. 만료 시 첫 정기 결제.
    trial_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # 체험 기능 활성 여부
    trial_days: Mapped[int | None] = mapped_column(Integer, nullable=True)                       # 체험 기간(일수); trial_enabled=True일 때만 유효
    # 자동결제안함(요청 013): False이면 첫 주기 후 자동연장 없음 — next_billing_at=None으로 저장되고 기간 종료 시 EXPIRED 처리.
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")       # False=첫 주기 후 자동연장 안 함
    # 추가정보(요청 013): 서비스 측 요금제 설명용 key/value. PlanResponse 외부 노출.
    extra_info: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")           # 서비스단 요금제 설명용 key/value
    status: Mapped[str] = mapped_column(String(20), default=PlanStatus.ACTIVE)                   # ACTIVE(신규 구독 가능) | ARCHIVED(신규 불가)
