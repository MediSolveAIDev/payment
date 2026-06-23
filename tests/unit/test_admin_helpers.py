from types import SimpleNamespace

from app.admin import receipt_url
from app.admin.routes.services import _parse_ips


def _pay(raw):
    # raw_response만 가진 가짜 결제 객체
    return SimpleNamespace(raw_response=raw)


def test_receipt_url_present():
    assert receipt_url(_pay({"receipt": {"url": "https://dashboard.tosspayments.com/receipt/abc"}})) \
        == "https://dashboard.tosspayments.com/receipt/abc"


def test_receipt_url_missing_receipt():
    assert receipt_url(_pay({"approvedAt": "2026-06-23"})) is None


def test_receipt_url_receipt_without_url():
    assert receipt_url(_pay({"receipt": {}})) is None


def test_receipt_url_none_raw():
    assert receipt_url(_pay(None)) is None


def test_receipt_url_non_string_url():
    # url 값이 문자열이 아니면 None (방어)
    assert receipt_url(_pay({"receipt": {"url": 123}})) is None


def test_parse_ips_newline_separated():
    assert _parse_ips("10.0.0.1\n10.0.0.2") == ["10.0.0.1", "10.0.0.2"]


def test_parse_ips_comma_separated_backward_compat():
    assert _parse_ips("10.0.0.1, 10.0.0.2") == ["10.0.0.1", "10.0.0.2"]


def test_parse_ips_mixed_and_blank_lines():
    assert _parse_ips("10.0.0.1\n\n 10.0.0.2 ,10.0.0.3\n") == [
        "10.0.0.1", "10.0.0.2", "10.0.0.3"]


def test_parse_ips_empty():
    assert _parse_ips("") == []


def test_parse_ips_crlf():
    """윈도우 브라우저 폼 제출(CRLF) — splitlines()가 \r 잔류 없이 처리."""
    assert _parse_ips("10.0.0.1\r\n10.0.0.2\r\n") == ["10.0.0.1", "10.0.0.2"]


def test_validate_ips_rejects_ipv6():
    import pytest as _pytest

    from app.core.errors import InputValidationError
    from app.services.registry import _validate_ips

    assert _validate_ips(["10.0.0.1"]) == ["10.0.0.1"]
    with _pytest.raises(InputValidationError):
        _validate_ips(["::1"])  # IPv6 거부 (요청 005: 옥텟 UI는 IPv4 전용)


def test_date_range_parses_pair():
    from datetime import datetime, timezone
    from app.admin.pagination import PageParams, date_range
    pp = PageParams(filters={"from": "2026-01-10", "to": "2026-01-20"})
    start, end = date_range(pp)
    assert start == datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert end == datetime(2026, 1, 21, tzinfo=timezone.utc)  # 익일 0시(반개구간)


def test_date_range_open_ended():
    from app.admin.pagination import PageParams, date_range
    pp = PageParams(filters={"from": "2026-01-10"})
    start, end = date_range(pp)
    assert start is not None and end is None
    pp2 = PageParams(filters={"to": "2026-01-20"})
    start2, end2 = date_range(pp2)
    assert start2 is None and end2 is not None


def test_date_range_invalid_removed_from_filters():
    from app.admin.pagination import PageParams, date_range
    pp = PageParams(filters={"from": "bogus", "to": "2026-01-20"})
    start, end = date_range(pp)
    assert start is None and end is not None
    assert "from" not in pp.filters       # 페이저 링크 오염 방지
    assert pp.filters.get("to") == "2026-01-20"


def test_kst_format_converts_utc_to_kst():
    from datetime import datetime, timezone
    from app.core.clock import kst_format
    # 2026-06-08 05:00 UTC → 14:00 KST (+9h)
    dt = datetime(2026, 6, 8, 5, 0, tzinfo=timezone.utc)
    assert kst_format(dt, "%Y-%m-%d %H:%M") == "2026-06-08 14:00"
    # 자정 넘김: 2026-06-08 16:00 UTC → 다음날 01:00 KST
    dt2 = datetime(2026, 6, 8, 16, 0, tzinfo=timezone.utc)
    assert kst_format(dt2, "%Y-%m-%d %H:%M") == "2026-06-09 01:00"


def test_kst_format_none_returns_dash():
    from app.core.clock import kst_format
    assert kst_format(None) == "-"


def test_kst_format_naive_treated_as_utc():
    from datetime import datetime
    from app.core.clock import kst_format
    dt = datetime(2026, 6, 8, 5, 0)  # tz 없음 → UTC로 간주
    assert kst_format(dt, "%H:%M") == "14:00"
