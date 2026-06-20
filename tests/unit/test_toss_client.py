import base64

import httpx
import pytest
import respx

from app.toss.client import HttpTossClient
from app.toss.errors import TossError, TossTimeoutError

BASE = "https://api.tosspayments.test"


@pytest.fixture
async def toss():
    client = HttpTossClient("test_sk_abc", base_url=BASE)
    yield client
    await client.aclose()


@respx.mock
async def test_issue_billing_key(toss):
    route = respx.post(f"{BASE}/v1/billing/authorizations/issue").mock(
        return_value=httpx.Response(200, json={
            "billingKey": "bk_1", "method": "카드", "customerKey": "ck-1",
            "card": {"number": "1234****", "issuerCode": "61"},
        }))
    result = await toss.issue_billing_key("auth-key-1", "ck-1")
    assert result.billing_key == "bk_1"
    assert result.card == {"number": "1234****", "issuerCode": "61"}
    sent = route.calls.last.request
    # Basic base64("test_sk_abc:") 인증 헤더 확인
    assert sent.headers["authorization"] == \
        "Basic " + base64.b64encode(b"test_sk_abc:").decode()


@respx.mock
async def test_charge_sends_idempotency_key_and_parses(toss):
    route = respx.post(f"{BASE}/v1/billing/bk_1").mock(
        return_value=httpx.Response(200, json={
            "paymentKey": "pay_1", "orderId": "order-1", "status": "DONE",
            "approvedAt": "2026-06-05T10:00:00+09:00", "totalAmount": 10000,
        }))
    result = await toss.charge("bk_1", "ck-1", 10000, "order-1", "기본 요금제", "idem-1")
    assert result.payment_key == "pay_1"
    assert result.status == "DONE"
    sent = route.calls.last.request
    assert sent.headers["idempotency-key"] == "idem-1"
    import json
    body = json.loads(sent.content)
    assert body == {"amount": 10000, "customerKey": "ck-1",
                    "orderId": "order-1", "orderName": "기본 요금제"}


@respx.mock
async def test_error_response_raises_toss_error(toss):
    respx.post(f"{BASE}/v1/billing/bk_1").mock(
        return_value=httpx.Response(400, json={
            "code": "EXCEED_MAX_AMOUNT", "message": "한도 초과"}))
    with pytest.raises(TossError) as exc:
        await toss.charge("bk_1", "ck-1", 10000, "order-1", "요금제", "idem-2")
    assert exc.value.code == "EXCEED_MAX_AMOUNT"
    assert exc.value.http_status == 400


@respx.mock
async def test_timeout_raises_toss_timeout(toss):
    respx.post(f"{BASE}/v1/billing/bk_1").mock(side_effect=httpx.ReadTimeout("timeout"))
    with pytest.raises(TossTimeoutError):
        await toss.charge("bk_1", "ck-1", 10000, "order-1", "요금제", "idem-3")


@respx.mock
async def test_get_payment_by_order_id_found_and_missing(toss):
    respx.get(f"{BASE}/v1/payments/orders/order-1").mock(
        return_value=httpx.Response(200, json={
            "paymentKey": "pay_1", "orderId": "order-1", "status": "DONE"}))
    respx.get(f"{BASE}/v1/payments/orders/order-x").mock(
        return_value=httpx.Response(404, json={
            "code": "NOT_FOUND_PAYMENT", "message": "없음"}))
    found = await toss.get_payment_by_order_id("order-1")
    assert found is not None and found.status == "DONE"
    assert await toss.get_payment_by_order_id("order-x") is None


@respx.mock
async def test_delete_billing_key(toss):
    route = respx.delete(f"{BASE}/v1/billing/bk_1").mock(
        return_value=httpx.Response(200, json={}))
    await toss.delete_billing_key("bk_1")
    assert route.called


@respx.mock
async def test_unparseable_success_body_maps_to_timeout_error(toss):
    """2xx인데 본문 해석 불가 → 처리 결과 불명 → TossTimeoutError(재조회 유도)."""
    respx.post(f"{BASE}/v1/billing/bk_1").mock(
        return_value=httpx.Response(200, text="<html>proxy error</html>"))
    with pytest.raises(TossTimeoutError):
        await toss.charge("bk_1", "ck-1", 10000, "order-h", "요금제", "idem-h")


async def test_fake_idempotent_replay_same_key():
    """같은 멱등키 재시도는 첫 응답을 재생한다 (실제 토스 동작 충실도)."""
    from app.toss.fake import FakeTossClient
    fake = FakeTossClient()
    first = await fake.charge("bk", "ck", 1000, "ord-1", "요금제", "idem-same")
    replay = await fake.charge("bk", "ck", 1000, "ord-1", "요금제", "idem-same")
    assert replay.payment_key == first.payment_key
    assert len(fake.charges) == 2  # 호출 기록은 2회


async def test_fake_duplicate_order_different_key_rejected():
    from app.toss.fake import FakeTossClient
    fake = FakeTossClient()
    await fake.charge("bk", "ck", 1000, "ord-2", "요금제", "idem-a")
    with pytest.raises(TossError) as exc:
        await fake.charge("bk", "ck", 1000, "ord-2", "요금제", "idem-b")
    assert exc.value.code == "ALREADY_PROCESSED_PAYMENT"
