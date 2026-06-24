"""매출전표(영수증) URL 추출 — 공용 함수·모델 프로퍼티·API 응답 노출 검증.

receipt_url은 토스 응답 원문(raw_response.receipt.url)에서만 안전 추출하며,
카드결제(DONE)는 보통 존재, 그 외(가상계좌·실패·대기)는 None이다.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.enums import PaymentKind, PaymentStatus
from app.models.payment import Payment, receipt_url_from_raw
from app.schemas.api import PaymentResponse

_URL = "https://dashboard.tosspayments.com/receipt/abc"


def test_receipt_url_from_raw_present():
    assert receipt_url_from_raw({"receipt": {"url": _URL}}) == _URL


def test_receipt_url_from_raw_variants_return_none():
    # 구조가 다르거나 url이 문자열이 아닌 경우(방어) 모두 None
    assert receipt_url_from_raw(None) is None
    assert receipt_url_from_raw({"approvedAt": "2026-06-23"}) is None  # receipt 없음
    assert receipt_url_from_raw({"receipt": {}}) is None               # url 없음
    assert receipt_url_from_raw({"receipt": {"url": 123}}) is None     # url 비문자열


def test_payment_property_reads_receipt():
    # 모델 프로퍼티가 raw_response에서 링크를 그대로 노출(없으면 None)
    assert Payment(raw_response={"receipt": {"url": _URL}}).receipt_url == _URL
    assert Payment(raw_response=None).receipt_url is None


def test_payment_response_exposes_receipt_url():
    """from_model이 매출전표 링크를 응답에 실어 서비스가 클릭 노출할 수 있게 한다."""
    payment = Payment(
        order_id="ord-1", amount=10000, status=PaymentStatus.DONE,
        kind=PaymentKind.ONE_OFF, payment_type="ONE_OFF",
        failure_code=None, failure_message=None,
        requested_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
        approved_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
        canceled_amount=0, cancel_fee=0,
        raw_response={"receipt": {"url": _URL}})
    service = SimpleNamespace(cancellation_fee_percent=0, cancellation_enabled=False)
    resp = PaymentResponse.from_model(payment, service)
    assert resp.receipt_url == _URL


def test_payment_response_receipt_url_none_when_absent():
    payment = Payment(
        order_id="ord-2", amount=10000, status=PaymentStatus.FAILED,
        kind=PaymentKind.ONE_OFF, payment_type="ONE_OFF",
        failure_code="X", failure_message="fail",
        requested_at=datetime(2026, 6, 24, tzinfo=timezone.utc),
        approved_at=None, canceled_amount=0, cancel_fee=0, raw_response=None)
    service = SimpleNamespace(cancellation_fee_percent=0, cancellation_enabled=False)
    assert PaymentResponse.from_model(payment, service).receipt_url is None
