"""billing_math 경계 케이스 — 로직 검증 리포트(L-5)에서 확인된 테스트 공백 보강.

기존 test_billing_math.py가 다루지 않던 경계를 못박는다:
- compute_cancel_fee 불변식(fee+refund==amount)·내림 방향
- 월말 클램프의 다단계 드리프트(현행 정책: '직전 종료일 앵커' — 가입일 복귀 없음)
- 윤년 경계, WEEK/DAY 대형 주기
- 100% 할인·정가 초과 정액 할인 → 0원 클램프(계산층의 현재 동작 문서화)

⚠️ 이 테스트들은 **현재 동작을 고정(pin)** 한다. 정책이 바뀌면(예: 월말 앵커를
가입일 유지로 변경 — 리포트 M-5) 이 파일의 기대값도 함께 바꿔야 한다.
"""
from datetime import datetime, timezone

import pytest

from app.core.errors import InputValidationError
from app.services.billing_math import (
    compute_cancel_fee,
    compute_first_amount,
    compute_period_end,
    compute_recurring_amount,
)


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


# ── 취소 수수료 — 불변식과 내림 방향 ────────────────────────────────────────

def test_cancel_fee_invariant_sweep():
    """모든 (금액, 수수료율) 조합에서 fee + refund == amount 가 항상 성립한다.

    화면 표시·조회 응답·실제 취소가 이 단일 함수를 공유하므로, 이 불변식이
    깨지면 '안내 금액과 환불 금액이 다른' 사고가 된다.
    """
    for amount in (1, 99, 100, 999, 5000, 99_999_999):
        for pct in (0, 1, 33, 50, 99, 100):
            fee, refund = compute_cancel_fee(amount, pct)
            assert fee + refund == amount
            assert fee >= 0 and refund >= 0


def test_cancel_fee_floor_favors_customer():
    """수수료는 정수 내림 — 1원 미만 절사는 항상 고객(환불액)에 유리하다."""
    fee, refund = compute_cancel_fee(999, 33)   # 999*33/100 = 329.67
    assert (fee, refund) == (329, 670)          # 내림 → 수수료 329, 환불 670


def test_cancel_fee_zero_and_full_percent():
    """경계 수수료율: 0% = 전액 환불, 100% = 환불 0원."""
    assert compute_cancel_fee(5000, 0) == (0, 5000)
    assert compute_cancel_fee(5000, 100) == (5000, 0)


# ── 월말 클램프 — 다단계 드리프트(현행 정책 고정) ────────────────────────────

def test_month_end_clamp_multi_step_drift():
    """1/31 가입 후 갱신을 거듭하면 결제일이 28일로 고정된다(현행 정책).

    갱신은 '직전 기간 종료일'을 앵커로 다음 종료일을 계산하므로
    1/31 → 2/28(클램프) → 3/28 → 4/28 … 로 가입일(31일)로 복귀하지 않는다.
    이는 의도된 동작인지 정책 확정 대기 항목(로직 리포트 M-5)이며,
    이 테스트는 현재 동작을 고정해 무의식적 변경을 막는다.
    """
    end1 = compute_period_end(_dt(2026, 1, 31), "MONTH")
    assert end1 == _dt(2026, 2, 28)             # 평년 2월 클램프
    end2 = compute_period_end(end1, "MONTH")    # 갱신: 직전 종료일이 앵커
    assert end2 == _dt(2026, 3, 28)             # 31일로 복귀하지 않음
    end3 = compute_period_end(end2, "MONTH")
    assert end3 == _dt(2026, 4, 28)


def test_leap_year_boundaries():
    """윤년 경계: 1/31+1개월=윤년 2/29, 2/29+1년=평년 2/28(클램프)."""
    assert compute_period_end(_dt(2024, 1, 31), "MONTH") == _dt(2024, 2, 29)
    assert compute_period_end(_dt(2024, 2, 29), "YEAR") == _dt(2025, 2, 28)


def test_week_and_large_day_cycles():
    """WEEK는 정확히 +7일, DAY는 cycle_days 그대로(대형 값 포함)."""
    assert compute_period_end(_dt(2026, 6, 1), "WEEK") == _dt(2026, 6, 8)
    assert compute_period_end(_dt(2026, 1, 1), "DAY", 365) == _dt(2027, 1, 1)


def test_day_cycle_requires_positive_days():
    """DAY 주기는 cycle_days 1 이상이 필수 — 0/None은 검증 오류."""
    with pytest.raises(InputValidationError):
        compute_period_end(_dt(2026, 6, 1), "DAY", 0)
    with pytest.raises(InputValidationError):
        compute_period_end(_dt(2026, 6, 1), "DAY", None)


# ── 할인 0원 경계 — 계산층의 현재 동작 문서화 ───────────────────────────────

def test_recurring_discount_can_reach_zero():
    """상시 할인 100% 또는 정가 초과 정액 할인은 0원으로 클램프된다.

    계산층은 0원을 허용한다 — 0원 갱신 청구를 막는 가드는 상위(요금제 검증
    또는 갱신 배치)의 책임이며 현재 부재(로직 리포트 H-4). 이 테스트는
    계산층 동작을 고정해, 가드를 추가할 위치가 계산층이 아님을 명시한다.
    """
    assert compute_recurring_amount(10_000, "DISCOUNT_PERCENT", 100) == 0
    assert compute_recurring_amount(10_000, "DISCOUNT_AMOUNT", 20_000) == 0


def test_percent_out_of_range_rejected():
    """할인율 0~100 범위 밖은 첫결제·상시 모두 검증 오류."""
    with pytest.raises(InputValidationError):
        compute_recurring_amount(10_000, "DISCOUNT_PERCENT", 101)
    with pytest.raises(InputValidationError):
        compute_first_amount(10_000, "DISCOUNT_PERCENT", -1)


def test_first_and_recurring_discounts_are_independent():
    """첫 결제는 정가+첫구독 할인만, 정기 결제는 정가+상시 할인만 — 중첩 없음."""
    price = 10_000
    # 첫 결제: 첫구독 할인 30%만 적용(상시 할인과 무관하게 정가 기준)
    assert compute_first_amount(price, "DISCOUNT_PERCENT", 30) == 7_000
    # 정기 결제: 상시 할인 10%만 적용
    assert compute_recurring_amount(price, "DISCOUNT_PERCENT", 10) == 9_000
