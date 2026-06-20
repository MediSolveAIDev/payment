"""결제 금액 계산 — 첫 결제 vs 상시 할인, 구독 기간 종료일.

계산 정책:
  첫 결제(1회차):
    계산 기준: plan.price(정가)
    적용 할인: plan.first_payment_type / first_payment_value (FirstPaymentType 열거값)
    상시 할인과 무관 — 첫 회차는 상시 할인을 적용하지 않는다(요청 005).

  정기 결제(2회차~):
    계산 기준: plan.price(정가)
    적용 할인: plan.recurring_discount_type / recurring_discount_value (DiscountType 열거값)
    상시 할인만 적용 — 첫구독 할인과 무관.

  두 할인은 독립적이며 중첩 적용하지 않는다.

FirstPaymentType 열거값 (첫 회차):
  NONE              → 정가 그대로
  FREE              → 0원 (완전 무료)
  DISCOUNT_AMOUNT   → 정가 − 원 단위 금액
  DISCOUNT_PERCENT  → 정가 − 비율 차감 (0~100; compute 계층 허용 범위)

DiscountType 열거값 (정기 회차):
  NONE              → 정가 그대로
  DISCOUNT_AMOUNT   → 정가 − 원 단위 금액
  DISCOUNT_PERCENT  → 정가 − 비율 차감 (0~100; compute 계층 허용 범위)
  (FREE 없음 — 매 회차 0원 정기 결제는 의미 없으므로 설계상 제외)

금액 계산은 항상 서버가 수행한다(외부 입력 금지).
"""

from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from app.core.errors import InputValidationError
from app.models.enums import BillingCycle, DiscountType, FirstPaymentType


def compute_period_end(start: datetime, cycle: str, cycle_days: int | None = None) -> datetime:
    """구독 기간 종료일 계산. MONTH/YEAR는 월말 클램프(relativedelta)."""
    if cycle == BillingCycle.YEAR:
        return start + relativedelta(years=1)
    if cycle == BillingCycle.MONTH:
        return start + relativedelta(months=1)
    if cycle == BillingCycle.WEEK:
        return start + timedelta(weeks=1)
    if cycle == BillingCycle.DAY:
        if not cycle_days or cycle_days < 1:
            raise InputValidationError("DAY 주기는 cycle_days(1 이상)가 필요합니다")
        return start + timedelta(days=cycle_days)
    raise InputValidationError(f"지원하지 않는 결제 주기입니다: {cycle}")


def compute_first_amount(price: int, first_payment_type: str,
                         first_payment_value: int | None) -> int:
    """첫 구독 결제 금액. 금액은 항상 서버가 계산한다(외부 입력 금지).

    요청 005: 첫 결제는 정가(price) 기준 — 상시 할인과 무관.

    반환값은 0 이상(음수 불가 — DISCOUNT_AMOUNT가 price를 초과해도 0으로 클램프).
    DISCOUNT_PERCENT 정수 나눗셈: (price * value) // 100 — 내림 처리.
    """
    if first_payment_type == FirstPaymentType.NONE:
        return price
    if first_payment_type == FirstPaymentType.FREE:
        return 0
    if first_payment_type == FirstPaymentType.DISCOUNT_AMOUNT:
        return max(0, price - (first_payment_value or 0))
    if first_payment_type == FirstPaymentType.DISCOUNT_PERCENT:
        value = first_payment_value or 0
        if not 0 <= value <= 100:
            raise InputValidationError("할인율은 0~100 사이여야 합니다")
        return price - (price * value) // 100
    raise InputValidationError(f"지원하지 않는 첫결제 유형입니다: {first_payment_type}")


def compute_recurring_amount(price: int, discount_type: str,
                             discount_value: int | None) -> int:
    """상시 할인 적용 후 실제 정기 결제 금액(요청 003). 2회차~ 갱신 기준가.

    요청 005: 첫 결제는 plan.price 기준 — 이 함수와 무관.

    유형: NONE(정가) / DISCOUNT_AMOUNT(원 차감) / DISCOUNT_PERCENT(율 차감).

    반환값은 0 이상(DISCOUNT_AMOUNT가 price 초과 시 0으로 클램프).
    DISCOUNT_PERCENT 정수 나눗셈: (price * value) // 100 — 내림 처리.
    """
    if discount_type == DiscountType.NONE:
        return price
    if discount_type == DiscountType.DISCOUNT_AMOUNT:
        return max(0, price - (discount_value or 0))
    if discount_type == DiscountType.DISCOUNT_PERCENT:
        value = discount_value or 0
        if not 0 <= value <= 100:
            raise InputValidationError("할인율은 0~100 사이여야 합니다")
        return price - (price * value) // 100
    raise InputValidationError(f"지원하지 않는 상시 할인 유형입니다: {discount_type}")


def compute_cancel_fee(amount: int, fee_percent: int) -> tuple[int, int]:
    """단건 결제 취소 시 (수수료, 환불액)을 계산한다.

    fee = amount × fee_percent // 100 (정수 내림), refund = amount − fee.
    결제 취소 실제 처리(cancel_one_off_payment)와 결제 조회 응답·화면 표시가
    동일한 공식을 쓰도록 한 곳에 모은다(값 불일치 방지).
    """
    fee = amount * fee_percent // 100
    return fee, amount - fee


def plan_recurring_amount(plan) -> int:
    """요금제의 상시 할인 적용가(2회차~ 및 표시용 '실제 결제 금액')."""
    return compute_recurring_amount(plan.price, plan.recurring_discount_type,
                                    plan.recurring_discount_value)


def plan_first_amount(plan) -> int:
    """첫 결제액 = 정가에 첫구독 할인만 적용(상시 할인 무관 — 요청 005)."""
    return compute_first_amount(plan.price, plan.first_payment_type,
                                plan.first_payment_value)


def _fmt_won(v: int) -> str:
    return f"{v:,}원"


def recurring_amount_breakdown(plan) -> str:
    """정기 결제액 계산 내역 — 리스트 툴팁 표시용."""
    t = plan.recurring_discount_type
    if t == DiscountType.DISCOUNT_AMOUNT:
        return (f"정가 {_fmt_won(plan.price)} − 상시 할인 "
                f"{_fmt_won(plan.recurring_discount_value or 0)} = "
                f"{_fmt_won(plan_recurring_amount(plan))}")
    if t == DiscountType.DISCOUNT_PERCENT:
        return (f"정가 {_fmt_won(plan.price)} − 상시 할인 "
                f"{plan.recurring_discount_value}% = "
                f"{_fmt_won(plan_recurring_amount(plan))}")
    return f"정가 {_fmt_won(plan.price)}"


def first_amount_breakdown(plan) -> str:
    """첫 결제액 계산 내역 — 정가 기준 첫구독 할인(요청 005)."""
    t = plan.first_payment_type
    if t == FirstPaymentType.FREE:
        return f"첫 회차 무료 = {_fmt_won(0)}"
    if t == FirstPaymentType.DISCOUNT_AMOUNT:
        return (f"정가 {_fmt_won(plan.price)} − 첫구독 할인 "
                f"{_fmt_won(plan.first_payment_value or 0)} = "
                f"{_fmt_won(plan_first_amount(plan))}")
    if t == FirstPaymentType.DISCOUNT_PERCENT:
        return (f"정가 {_fmt_won(plan.price)} − 첫구독 할인 "
                f"{plan.first_payment_value}% = "
                f"{_fmt_won(plan_first_amount(plan))}")
    return f"정가 {_fmt_won(plan.price)}"
