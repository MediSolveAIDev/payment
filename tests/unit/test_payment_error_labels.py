"""결제 실패 코드 → 의미 매핑 단위 테스트."""
from app.admin.payment_error_labels import PAYMENT_ERROR_LABELS, payment_error_meaning


def test_known_toss_code_meaning():
    assert payment_error_meaning("REJECT_CARD_COMPANY") == PAYMENT_ERROR_LABELS["REJECT_CARD_COMPANY"]
    assert "최소 결제금액" in payment_error_meaning("BELOW_MINIMUM_AMOUNT")


def test_known_internal_code_meaning():
    # 우리 서버 내부 코드도 매핑돼 있어야 함
    assert payment_error_meaning("CANCEL_DISABLED")
    assert payment_error_meaning("NO_BILLING_KEY")


def test_unknown_and_empty_returns_blank():
    # 매핑에 없으면 빈 문자열(호출측이 failure_message로 폴백)
    assert payment_error_meaning("SOME_NEW_UNKNOWN_CODE") == ""
    assert payment_error_meaning(None) == ""
    assert payment_error_meaning("") == ""
