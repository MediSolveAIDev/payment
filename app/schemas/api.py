"""외부 서비스 → 결제/구독 API 간 요청·응답 Pydantic 스키마.

외부 서비스(클라이언트)가 API 키로 인증 후 호출하는 엔드포인트의 입출력 형식을 정의한다.
보안 원칙:
  - 결제 금액은 클라이언트가 전달하지 않는다. 서버가 Plan에서 계산해 조작을 차단.
  - billingKey 등 민감 정보는 응답에 포함하지 않는다.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.identifiers import normalize_external_user_id
from app.models import Plan, Subscription
from app.models import access_allowed as model_access_allowed


def _normalize_external_user_id_field(value: str) -> str:
    """요청 본문 external_user_id 공통 검증기 — 이메일 형식 강제 + 정규화(소문자/trim).

    전역 룰(이메일만 허용)을 입력 경계에서 보장한다. 잘못된 형식은 422로 거부된다.
    """
    return normalize_external_user_id(value)


class SubscriptionCreateRequest(BaseModel):
    """구독 생성 요청 — POST /subscriptions.

    Task 7 변경: auth_key·customer_key 제거. 구독 전에 POST /cards 로 카드를 먼저
    등록해야 하며, 빌링키는 등록된 카드(cards 테이블)에서 서버가 자동으로 조회한다.
    trial: True이면 요금제의 trial_enabled=True일 때만 허용; 아니면 422(InputValidationError).
    """

    # Field(description=...)는 Swagger 모델 스키마에 그대로 노출된다(외부 개발자용 설명).
    external_user_id: str = Field(
        min_length=1, max_length=255,
        description="외부 서비스 측 사용자 식별자. **반드시 이메일**이어야 한다(소문자로 정규화 저장). "
                    "(서비스+사용자 당 구독 1개 규칙의 기준 키)",
        examples=["user@example.com"])
    plan_id: uuid.UUID = Field(
        description="구독할 요금제 ID. GET /plans 응답의 id 사용.",
        examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"])

    # external_user_id 는 이메일만 허용 — 입력 경계에서 정규화/검증
    _norm_euid = field_validator("external_user_id")(_normalize_external_user_id_field)

    trial: bool = Field(
        default=False,
        description="체험(Trial)으로 시작할지 여부. 요금제가 체험을 제공(trial_enabled=true)할 때만 허용되며, "
                    "체험 기간 동안 결제 없이 시작하고 만료 시 자동결제된다.")
    # 주의: 금액 필드 없음 — 금액은 서버가 plan에서 계산(클라이언트 조작 차단).
    # auth_key·customer_key 없음 — 카드는 POST /cards 로 사전 등록하며 여기서 재발급하지 않음.

    model_config = ConfigDict(json_schema_extra={"example": {
        "external_user_id": "user-123",
        "plan_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "trial": False,
    }})


class PlanResponse(BaseModel):
    """요금제 정보 응답.

    amount: 상시 할인 적용 후 실제 정기 결제 금액(요청 003). 할인 없으면 price와 동일.
    서버의 billing_math.plan_recurring_amount()로 계산된 값을 반환한다.
    auto_renew: 자동갱신 여부(요청 013). False이면 첫 주기 종료 후 자동결제 없음.
    extra_info: 서비스 측 요금제 설명용 key/value(요청 013).
    """

    id: uuid.UUID = Field(description="요금제 ID. 구독 생성 시 plan_id로 사용.")
    name: str = Field(description="요금제 이름.")
    price: int = Field(description="정가(원).")
    amount: int = Field(
        description="실제 정기 청구 금액(원). 상시 할인 적용 후 값이며, 할인이 없으면 price와 동일.")
    currency: str = Field(description="통화 코드(예: KRW).")
    billing_cycle: str = Field(description="결제 주기: YEAR | MONTH | WEEK | DAY | MINUTE.")
    cycle_days: int | None = Field(
        description="DAY 주기일 때의 실제 일수. 그 외 주기에서는 null.")
    cycle_minutes: int | None = Field(
        default=None,
        description="MINUTE 주기일 때의 실제 분(5 이상). 그 외 주기에서는 null. 테스트용·비운영 전용.")
    first_payment_type: str = Field(
        description="첫 결제 혜택 유형: NONE | FREE | DISCOUNT_AMOUNT | DISCOUNT_PERCENT.")
    first_payment_value: int | None = Field(
        description="첫 결제 할인 값(정액=원, 정률=%). 혜택 없으면 null.")
    trial_enabled: bool = Field(description="체험 제공 여부. true일 때만 구독 생성에서 trial=true 가능.")
    trial_days: int | None = Field(description="체험 일수. 체험 미제공 시 null.")
    auto_renew: bool = Field(description="자동갱신 여부. false면 첫 주기 종료 후 자동결제 없이 만료.")
    extra_info: dict = Field(description="서비스 측 요금제 부가 정보(key/value).")

    @classmethod
    def from_model(cls, plan: Plan) -> "PlanResponse":
        from app.services.billing_math import plan_recurring_amount
        return cls(id=plan.id, name=plan.name, price=plan.price,
                   amount=plan_recurring_amount(plan), currency=plan.currency,
                   billing_cycle=plan.billing_cycle, cycle_days=plan.cycle_days,
                   cycle_minutes=plan.cycle_minutes,
                   first_payment_type=plan.first_payment_type,
                   first_payment_value=plan.first_payment_value,
                   trial_enabled=plan.trial_enabled, trial_days=plan.trial_days,
                   auto_renew=plan.auto_renew,   # 자동갱신 여부(요청 013)
                   extra_info=plan.extra_info or {})


class SubscriptionResponse(BaseModel):
    """구독 상태 응답.

    access_allowed: 외부 서비스가 해당 사용자의 접근을 허용할지 판단하는 핵심 필드.
    TRIAL/ACTIVE/PAST_DUE/CANCELED=true, SUSPENDED/EXPIRED=false.
    billingKey 등 내부 민감 정보는 포함하지 않는다.
    """

    id: uuid.UUID = Field(description="구독 ID.")
    external_user_id: str = Field(description="외부 서비스 측 사용자 식별자.")
    plan_id: uuid.UUID = Field(description="구독한 요금제 ID.")
    plan_name: str = Field(description="구독한 요금제 이름.")
    status: str = Field(
        description="구독 상태: TRIAL | ACTIVE | PAST_DUE | SUSPENDED | CANCELED | EXPIRED.")
    access_allowed: bool = Field(
        description="서비스 접근 허용 여부. 외부 서비스는 이 값으로 사용자 접근을 판단한다. "
                    "TRIAL/ACTIVE/PAST_DUE/CANCELED=true, SUSPENDED/EXPIRED=false.")
    current_period_start: datetime = Field(description="현재 결제 주기 시작 시각.")
    current_period_end: datetime = Field(description="현재 결제 주기 종료(만료) 시각.")
    next_billing_at: datetime | None = Field(
        description="다음 자동결제 예정 시각. 해지 예약·만료 시 null.")
    card: dict | None = Field(description="등록 카드 마스킹 정보(표시용). 미등록 시 null.")
    retry_count: int = Field(description="PAST_DUE 상태에서의 결제 재시도 횟수.")

    @classmethod
    def from_model(cls, sub: Subscription, plan: Plan,
                   card_info: dict | None = None) -> "SubscriptionResponse":
        """Subscription + Plan + 카드 표시 정보(card_info)로 응답을 생성한다.

        Task 7 변경: card_info는 sub.card_info(제거됨) 대신 호출자가 cards 테이블에서
        조회해 전달한다. 카드 미등록 또는 조회 실패 시 None이 그대로 반환된다.
        """
        return cls(id=sub.id, external_user_id=sub.external_user_id, plan_id=sub.plan_id,
                   plan_name=plan.name, status=sub.status,
                   access_allowed=model_access_allowed(sub.status),
                   current_period_start=sub.current_period_start,
                   current_period_end=sub.current_period_end,
                   next_billing_at=sub.next_billing_at,
                   card=card_info,  # cards 테이블에서 조회한 마스킹 카드 정보(없으면 None)
                   retry_count=sub.retry_count)


class OneOffPaymentRequest(BaseModel):
    """단건(비구독) 결제 요청 — POST /payments/one-off.

    amount: 클라이언트가 직접 지정(구독과 달리 서버가 계산할 기준 Plan이 없음).
      gt=0/le 제약으로 0원 이하·상한 초과 요청을 거부한다(상한은 보안 L-3 —
      services/payments.py의 ONE_OFF_MAX_AMOUNT와 동일 값 유지).
    order_id: min=6은 토스 스펙 최솟값; **서비스 내에서** 고유(타 서비스와는 중복 가능 —
      감사 Phase 2, 보안 M-1). 같은 order_id 재시도는 기존 결제를 반환(멱등).

    Task 9 변경: auth_key·customer_key 필드 제거.
    단건결제도 사전 등록된 카드(POST /cards)를 사용하며, 빌링키는 서버가
    카드 보관함(cards 테이블)에서 자동으로 조회한다.
    """

    external_user_id: str = Field(
        min_length=1, max_length=255,
        description="외부 서비스 측 사용자 식별자. **반드시 이메일**이어야 한다(소문자로 정규화 저장).",
        examples=["user@example.com"])
    _norm_euid = field_validator("external_user_id")(_normalize_external_user_id_field)
    order_id: str = Field(
        min_length=6, max_length=64,
        description="주문 ID. 서비스 내에서 고유해야 하며 재사용 시 기존 결제 반환(멱등). 최소 6자(토스 스펙).",
        examples=["order-20260610-0001"])
    order_name: str = Field(
        min_length=1, max_length=100,
        description="결제창에 표시되는 주문명.", examples=["프리미엄 1회 이용권"])
    amount: int = Field(
        gt=0, le=100_000_000,
        description="결제 금액(원). 0원 이하 또는 1억원 초과는 거부된다.",
        examples=[10000])
    # auth_key·customer_key 없음 — 카드는 POST /cards 로 사전 등록하며 여기서 받지 않음(Task 9).


class UsageDaysRequest(BaseModel):
    """구독 사용일 추가 요청 — POST /subscriptions/{external_user_id}/add-days.

    days: 추가할 사용일수. 이용 중(ACTIVE/EXTENDED/PAST_DUE) 구독의 만료일·다음 결제일이
    days만큼 미뤄진다(상태는 변경되지 않음). 1~3650 범위.
    """

    days: int = Field(gt=0, le=3650, description="추가할 사용일수(1~3650).", examples=[30])


class OneOffCancelRequest(BaseModel):
    """단건 결제 취소 요청 — POST /payments/{order_id}/cancel.

    reason: 취소 사유. 기본값은 '사용자 취소'. 토스 취소 API의 cancelReason에 전달된다.
    max_length=200: 토스 API 사유 필드 길이 제한에 맞춤.
    """

    reason: str = Field(
        default="사용자 취소", min_length=1, max_length=200,
        description="취소 사유. 토스 취소 API의 cancelReason으로 전달된다.",
        examples=["사용자 취소"])


class PaymentResponse(BaseModel):
    """결제 결과 응답. Payment 모델 + 서비스 취소 정책에서 변환(from_model).

    toss_payment_key · raw_response 등 내부 필드는 노출하지 않는다.
    failure_code/failure_message는 status=FAILED일 때만 채워진다.
    취소 수수료 필드(cancel_*)는 서비스가 결제 취소 전에 "지금 취소하면 얼마가
    수수료로 빠지고 얼마가 환불되는지"를 화면에 안내할 수 있도록 함께 반환한다.
    """

    model_config = ConfigDict(from_attributes=True)

    order_id: str = Field(description="주문 ID.")
    amount: int = Field(description="실제 청구된 금액(원).")
    status: str = Field(description="결제 상태: PENDING | DONE | FAILED | CANCELED.")
    kind: str = Field(description="결제 분류: SUBSCRIPTION(구독 정기) | ONE_OFF(단건).")
    payment_type: str = Field(
        description="결제 회차 구분: FIRST | RENEWAL | RETRY | ONE_OFF.")
    failure_code: str | None = Field(description="실패 코드. status=FAILED일 때만 값 존재.")
    failure_message: str | None = Field(
        description="실패 사유 메시지. status=FAILED일 때만 값 존재.")
    requested_at: datetime = Field(description="결제 요청 시각.")
    approved_at: datetime | None = Field(
        description="승인 시각. 실패·대기 중에는 null.")
    # ── 취소 수수료 안내(서비스가 취소 화면에 노출) ─────────────────────────────
    cancelable: bool = Field(
        default=False,
        description="지금 이 결제를 취소할 수 있는지. 단건(ONE_OFF)·완료(DONE)·서비스 취소허용일 때만 true.")
    cancel_fee_percent: int = Field(
        default=0, description="서비스의 취소 수수료율(%).")
    cancel_fee: int = Field(
        default=0,
        description="취소 시 차감되는 수수료(원). 취소 가능 결제는 예상액, 이미 취소된 결제는 실제 차감액.")
    cancel_refund_amount: int = Field(
        default=0,
        description="취소 시 환불되는 금액(원). 취소 가능 결제는 예상액, 이미(부분/전액) 취소된 결제는 실제 누적 환불액.")
    canceled_amount: int = Field(
        default=0,
        description="실제 환불된 누적 금액(원). 어드민 부분취소 시 status는 DONE이지만 이 값이 0보다 크다.")
    net_amount: int = Field(
        default=0,
        description="실수령(순) 금액(원) = 결제금액 − 누적 환불액. 부분취소 반영.")

    @classmethod
    def from_model(cls, payment, service) -> "PaymentResponse":
        """Payment + Service(취소 정책)로 응답을 만든다.

        cancelable: 단건·DONE·미취소·서비스 취소허용일 때만 취소 가능.
        - 부분/전액 취소된 결제(canceled_amount>0): 모델에 기록된 실제 환불액을 노출한다.
          어드민 부분취소는 status=DONE을 유지하므로 status가 아니라 canceled_amount로 판정한다.
        - 취소 가능 결제(DONE·미취소): compute_cancel_fee로 '지금 취소 시' 예상 수수료/환불액.
        - 그 외(구독 결제·실패·대기 등): 0.
        """
        from app.services.billing_math import compute_cancel_fee
        from app.models import PaymentKind, PaymentStatus

        fee_percent = service.cancellation_fee_percent
        refunded = payment.canceled_amount or 0          # 실제 누적 환불액(부분취소 포함)
        cancelable = (payment.kind == PaymentKind.ONE_OFF
                      and payment.status == PaymentStatus.DONE
                      and not refunded  # 이미 (부분)취소된 건은 외부 취소 불가
                      and service.cancellation_enabled)
        if refunded > 0:
            # 부분취소(DONE) 또는 전액취소(CANCELED) — 실제 환불액/수수료
            fee, refund = (payment.cancel_fee or 0), refunded
        elif payment.status == PaymentStatus.CANCELED:
            # 전액취소인데 canceled_amount 미기록(레거시) — 수수료만, 환불 0
            fee, refund = (payment.cancel_fee or 0), 0
        elif cancelable:
            # 취소 가능 — 지금 취소하면 빠질 예상 수수료/환불액
            fee, refund = compute_cancel_fee(payment.amount, fee_percent)
        else:
            fee, refund = 0, 0
        return cls(order_id=payment.order_id, amount=payment.amount, status=payment.status,
                   kind=payment.kind, payment_type=payment.payment_type,
                   failure_code=payment.failure_code, failure_message=payment.failure_message,
                   requested_at=payment.requested_at, approved_at=payment.approved_at,
                   cancelable=cancelable, cancel_fee_percent=fee_percent,
                   cancel_fee=fee, cancel_refund_amount=refund,
                   canceled_amount=refunded, net_amount=payment.amount - refunded)


# ── 목록/공통 응답 래퍼 — Swagger가 응답 구조까지 보여주도록 명시적으로 정의 ──────────

class ServiceListItem(BaseModel):
    """서비스 목록의 단일 항목(민감정보 미포함)."""

    id: str = Field(description="서비스 ID.")
    name: str = Field(description="서비스 이름.")
    status: str = Field(description="서비스 상태: ACTIVE | INACTIVE.")


class ServiceListResponse(BaseModel):
    """GET /services 응답."""

    services: list[ServiceListItem] = Field(description="등록된 서비스 목록(이름 오름차순).")


class PlanListResponse(BaseModel):
    """GET /plans 응답."""

    plans: list[PlanResponse] = Field(description="인증된 서비스의 활성 요금제 목록.")


class PaymentListResponse(BaseModel):
    """GET /payments/{external_user_id} 응답."""

    payments: list[PaymentResponse] = Field(description="결제 내역(최신순, 최대 50건).")


class ErrorBody(BaseModel):
    """에러 응답의 본문 형태."""

    code: str = Field(description="머신 리더블 에러 코드.", examples=["UNAUTHORIZED"])
    message: str = Field(description="사람이 읽는 에러 메시지.", examples=["인증에 실패했습니다"])


class ErrorResponse(BaseModel):
    """모든 에러의 공통 응답 형태 — {\"error\": {\"code\", \"message\"}}."""

    error: ErrorBody


class CardRegisterRequest(BaseModel):
    """카드 등록/교체 요청 — POST /cards.

    auth_key: 토스 SDK(결제창)에서 발급받은 1회용 인증값으로, 서버가 빌링키 발급에 사용한다.
    customer_key: 토스 측 고객 식별자(min_length=2는 토스 스펙 최솟값).
    """

    external_user_id: str = Field(
        min_length=1, max_length=255,
        description="외부 서비스 측 사용자 식별자. **반드시 이메일**이어야 한다(소문자로 정규화 저장). "
                    "(서비스+사용자 당 카드 1개 규칙의 기준 키)",
        examples=["user@example.com"])
    _norm_euid = field_validator("external_user_id")(_normalize_external_user_id_field)
    customer_key: str = Field(
        min_length=2, max_length=300,
        description="토스 customerKey(고객 식별자). 최소 2자(토스 스펙).",
        examples=["cust-123"])
    auth_key: str = Field(
        min_length=1, max_length=300,
        description="토스 결제창에서 발급받은 authKey. 빌링키 발급에 사용되는 일회용 키.",
        examples=["toss_auth_key_xxx"])


class CardResponse(BaseModel):
    """등록 카드 응답(마스킹 정보만 반환).

    billingKey 등 민감한 결제 정보는 절대 포함하지 않는다.
    card 필드는 토스에서 반환한 마스킹 카드 표시 정보(issuerCode, number 등)이다.
    """

    external_user_id: str = Field(description="외부 서비스 측 사용자 식별자.")
    card: dict | None = Field(
        default=None,
        description="카드 마스킹 정보(issuerCode, number 등 표시용). 정보 없으면 null.")

    @classmethod
    def from_model(cls, card) -> "CardResponse":
        """Card 모델에서 응답 스키마로 변환한다. billingKey는 포함하지 않는다."""
        return cls(external_user_id=card.external_user_id, card=card.card_info)


class WebhookAck(BaseModel):
    """POST /webhooks/toss 응답 — 처리 결과 상태만 반환."""

    status: str = Field(
        description="웹훅 처리 상태: RECEIVED | PROCESSED | IGNORED | FAILED.",
        examples=["PROCESSED"])
