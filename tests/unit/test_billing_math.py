from datetime import UTC, datetime

import pytest

from app.core.errors import InputValidationError
from app.services.billing_math import compute_first_amount, compute_period_end


def dt(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


class TestPeriodEnd:
    def test_month_normal(self):
        assert compute_period_end(dt(2026, 6, 5), "MONTH") == dt(2026, 7, 5)

    def test_month_end_clamps(self):
        # 1/31 + 1개월 → 2/28 (월말 클램프)
        assert compute_period_end(dt(2026, 1, 31), "MONTH") == dt(2026, 2, 28)

    def test_month_end_leap_year(self):
        assert compute_period_end(dt(2024, 1, 31), "MONTH") == dt(2024, 2, 29)

    def test_year(self):
        assert compute_period_end(dt(2026, 6, 5), "YEAR") == dt(2027, 6, 5)

    def test_year_leap_day(self):
        assert compute_period_end(dt(2024, 2, 29), "YEAR") == dt(2025, 2, 28)

    def test_week(self):
        assert compute_period_end(dt(2026, 6, 5), "WEEK") == dt(2026, 6, 12)

    def test_day_with_cycle_days(self):
        assert compute_period_end(dt(2026, 6, 5), "DAY", 10) == dt(2026, 6, 15)

    def test_day_requires_cycle_days(self):
        with pytest.raises(InputValidationError):
            compute_period_end(dt(2026, 6, 5), "DAY", None)
        with pytest.raises(InputValidationError):
            compute_period_end(dt(2026, 6, 5), "DAY", 0)

    def test_unknown_cycle_rejected(self):
        with pytest.raises(InputValidationError):
            compute_period_end(dt(2026, 6, 5), "HOUR")


class TestFirstAmount:
    def test_none_is_full_price(self):
        assert compute_first_amount(10000, "NONE", None) == 10000

    def test_free(self):
        assert compute_first_amount(10000, "FREE", None) == 0

    def test_discount_amount(self):
        assert compute_first_amount(10000, "DISCOUNT_AMOUNT", 3000) == 7000

    def test_discount_amount_floors_at_zero(self):
        assert compute_first_amount(10000, "DISCOUNT_AMOUNT", 99999) == 0

    def test_discount_percent(self):
        assert compute_first_amount(10000, "DISCOUNT_PERCENT", 30) == 7000

    def test_discount_percent_rounds_down_remainder(self):
        assert compute_first_amount(9999, "DISCOUNT_PERCENT", 33) == 6700  # 9999-3299

    def test_discount_percent_bounds(self):
        with pytest.raises(InputValidationError):
            compute_first_amount(10000, "DISCOUNT_PERCENT", 101)
        with pytest.raises(InputValidationError):
            compute_first_amount(10000, "DISCOUNT_PERCENT", -1)

    def test_unknown_type_rejected(self):
        with pytest.raises(InputValidationError):
            compute_first_amount(10000, "BOGOF", None)


from types import SimpleNamespace

from app.services.billing_math import (
    compute_recurring_amount,
    plan_first_amount,
    plan_recurring_amount,
)


def test_recurring_amount_none_amount_percent():
    assert compute_recurring_amount(10000, "NONE", None) == 10000
    assert compute_recurring_amount(10000, "DISCOUNT_AMOUNT", 1500) == 8500
    assert compute_recurring_amount(10000, "DISCOUNT_PERCENT", 10) == 9000
    # 금액 할인이 가격 초과 시 0으로 클램프
    assert compute_recurring_amount(1000, "DISCOUNT_AMOUNT", 5000) == 0


def _plan(**kw):
    base = dict(price=10000, recurring_discount_type="NONE", recurring_discount_value=None,
                first_payment_type="NONE", first_payment_value=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_plan_recurring_amount():
    p = _plan(recurring_discount_type="DISCOUNT_PERCENT", recurring_discount_value=20)
    assert plan_recurring_amount(p) == 8000


def test_plan_first_amount_ignores_recurring_discount():
    """요청 005: 첫 결제는 상시 할인과 무관하게 정가 기준."""
    p = _plan(price=10000,
              recurring_discount_type="DISCOUNT_PERCENT", recurring_discount_value=20,
              first_payment_type="DISCOUNT_AMOUNT", first_payment_value=2000)
    assert plan_first_amount(p) == 8000  # 정가 10000 − 2000 (상시 20% 무시)


def test_plan_first_amount_none_type_is_full_price():
    """첫구독 할인 없음 → 상시 할인이 있어도 첫 결제는 정가."""
    p = _plan(price=10000,
              recurring_discount_type="DISCOUNT_PERCENT", recurring_discount_value=20,
              first_payment_type="NONE", first_payment_value=None)
    assert plan_first_amount(p) == 10000


def test_plan_first_amount_percent_on_full_price():
    """퍼센트 첫구독 할인도 정가 기준(상시 할인 무관)."""
    p = _plan(price=10000,
              recurring_discount_type="DISCOUNT_AMOUNT", recurring_discount_value=3000,
              first_payment_type="DISCOUNT_PERCENT", first_payment_value=10)
    assert plan_first_amount(p) == 9000  # 10000 − floor(10000*10%) — 상시 −3000 무시


def test_plan_first_amount_free_overrides():
    p = _plan(recurring_discount_type="DISCOUNT_AMOUNT", recurring_discount_value=1000,
              first_payment_type="FREE")
    assert plan_recurring_amount(p) == 9000
    assert plan_first_amount(p) == 0  # 첫 회차 무료


def test_breakdown_strings():
    from app.services.billing_math import (first_amount_breakdown,
                                           recurring_amount_breakdown)
    p = _plan(price=10000,
              recurring_discount_type="DISCOUNT_PERCENT", recurring_discount_value=5,
              first_payment_type="DISCOUNT_AMOUNT", first_payment_value=1000)
    assert recurring_amount_breakdown(p) == "정가 10,000원 − 상시 할인 5% = 9,500원"
    assert first_amount_breakdown(p) == "정가 10,000원 − 첫구독 할인 1,000원 = 9,000원"


def test_breakdown_no_discount_and_free():
    from app.services.billing_math import (first_amount_breakdown,
                                           recurring_amount_breakdown)
    p = _plan(price=10000, recurring_discount_type="NONE", recurring_discount_value=None,
              first_payment_type="NONE", first_payment_value=None)
    assert recurring_amount_breakdown(p) == "정가 10,000원"
    assert first_amount_breakdown(p) == "정가 10,000원"
    f = _plan(price=10000, recurring_discount_type="NONE", recurring_discount_value=None,
              first_payment_type="FREE", first_payment_value=None)
    assert first_amount_breakdown(f) == "첫 회차 무료 = 0원"
