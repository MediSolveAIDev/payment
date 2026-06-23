import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import create_app
from app.models import Payment, WebhookEvent
from app.toss.types import ChargeResult
# Task 9: create_card_direct 추가 — 특정 billingKey 값을 가진 Card를 직접 삽입해
# BILLING_DELETED 웹훅이 Card.billing_key_hash로 구독을 찾을 수 있도록 셋업한다.
from tests.factories import create_card_direct, create_plan, create_service, create_subscription


def _billing_deleted(billing_key: str) -> dict:
    return {"eventType": "BILLING_DELETED",
            "createdAt": "2026-06-05T00:00:00.000000",
            "data": {"billingKey": billing_key, "reason": "삭제 API 요청"}}


async def test_billing_deleted_notifies_manager(client, db, cipher, email_sender):
    """BILLING_DELETED 웹훅 수신 시 서비스 담당자에게 알림 메일이 발송된다.

    Task 9: 빌링키가 Subscription에서 Card로 이동했으므로,
    카드를 먼저 생성(create_card_direct)하고 card_id를 구독에 연결한다.
    웹훅 핸들러는 Card.billing_key_hash로 카드를 찾고 card_id로 구독을 조회한다.
    """
    svc, _, _ = await create_service(db, cipher, manager_email="mgr@x.com")
    plan = await create_plan(db, svc)
    # Task 9: 특정 billingKey("bk_hooked")를 가진 Card를 직접 삽입하고 구독에 연결
    card = await create_card_direct(db, cipher, svc, external_user_id="user-1",
                                    billing_key="bk_hooked")
    await create_subscription(db, cipher, svc, plan, card_id=card.id)
    resp = await client.post("/api/v1/webhooks/toss", json=_billing_deleted("bk_hooked"),
                             headers={"tosspayments-webhook-transmission-id": "wh-1"})
    assert resp.status_code == 200
    event = await db.scalar(select(WebhookEvent))
    assert event.status == "PROCESSED"
    assert len(email_sender.sent) == 1
    assert email_sender.sent[0]["to"] == "mgr@x.com"


async def test_duplicate_transmission_processed_once(client, db, cipher, email_sender):
    """동일 transmission_id 웹훅은 두 번 수신해도 한 번만 처리된다(멱등).

    Task 9: BILLING_DELETED이므로 카드(Card)와 구독(card_id)을 연결해 셋업한다.
    """
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    # Task 9: 특정 billingKey("bk_dup")를 가진 Card를 직접 삽입하고 구독에 연결
    card = await create_card_direct(db, cipher, svc, external_user_id="user-1",
                                    billing_key="bk_dup")
    await create_subscription(db, cipher, svc, plan, card_id=card.id)
    payload = _billing_deleted("bk_dup")
    headers = {"tosspayments-webhook-transmission-id": "wh-same"}
    await client.post("/api/v1/webhooks/toss", json=payload, headers=headers)
    await client.post("/api/v1/webhooks/toss", json=payload, headers=headers)
    events = (await db.scalars(select(WebhookEvent))).all()
    assert len(events) == 1
    assert len(email_sender.sent) == 1  # 한 번만 처리


async def test_unknown_event_ignored(client, db, cipher):
    resp = await client.post("/api/v1/webhooks/toss",
                             json={"eventType": "DEPOSIT_CALLBACK", "data": {}},
                             headers={"tosspayments-webhook-transmission-id": "wh-ig"})
    assert resp.status_code == 200
    event = await db.scalar(select(WebhookEvent))
    assert event.status == "IGNORED"


async def test_payment_status_changed_verified_by_refetch(client, db, cipher, fake_toss):
    """페이로드를 믿지 않고 토스 재조회로 확정 — 재조회가 CANCELED일 때만 반영."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan)
    payment = Payment(subscription_id=sub.id, order_id="order-wh-1", amount=plan.price,
                      payment_type="RENEWAL", status="DONE", idempotency_key="ik",
                      requested_at=sub.current_period_start,
                      service_id=sub.service_id, external_user_id=sub.external_user_id)
    db.add(payment)
    await db.commit()
    fake_toss.payments_by_order["order-wh-1"] = ChargeResult(
        payment_key="pay_wh", order_id="order-wh-1", status="CANCELED",
        raw={"status": "CANCELED"})

    resp = await client.post(
        "/api/v1/webhooks/toss",
        json={"eventType": "PAYMENT_STATUS_CHANGED",
              "data": {"orderId": "order-wh-1", "status": "CANCELED"}},
        headers={"tosspayments-webhook-transmission-id": "wh-pay"})
    assert resp.status_code == 200
    await db.refresh(payment)
    assert payment.status == "CANCELED"
    assert payment.canceled_amount == plan.price   # 전액 취소 동기화
    assert payment.canceled_at is not None          # 취소 시각 기록됨


async def test_payment_status_changed_spoofed_payload_not_applied(client, db, cipher, fake_toss):
    """재조회 결과가 없으면(위조 의심) 로컬 상태 불변."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan)
    payment = Payment(subscription_id=sub.id, order_id="order-spoof", amount=plan.price,
                      payment_type="RENEWAL", status="DONE", idempotency_key="ik2",
                      requested_at=sub.current_period_start,
                      service_id=sub.service_id, external_user_id=sub.external_user_id)
    db.add(payment)
    await db.commit()
    # fake_toss.payments_by_order 에 미등록 → 재조회 None

    await client.post(
        "/api/v1/webhooks/toss",
        json={"eventType": "PAYMENT_STATUS_CHANGED",
              "data": {"orderId": "order-spoof", "status": "CANCELED"}},
        headers={"tosspayments-webhook-transmission-id": "wh-spoof"})
    await db.refresh(payment)
    assert payment.status == "DONE"  # 변조 반영 안 됨


async def test_webhook_from_unallowed_ip_rejected(settings, engine, fake_toss, email_sender):
    """토스 인바운드 IP 목록 밖에서 온 웹훅은 403."""
    blocked = settings.model_copy(update={"toss_webhook_allowed_ips": ["10.0.0.1"]})
    application = create_app(blocked, toss_client=fake_toss,
                             email_sender=email_sender, engine=engine)
    async with LifespanManager(application):
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as c:
            resp = await c.post("/api/v1/webhooks/toss",
                                json={"eventType": "BILLING_DELETED", "data": {}})
    assert resp.status_code == 403


async def test_webhook_without_transmission_id_rejected(client, db, cipher):
    """transmission-id 없는 웹훅은 거부(헤더 없는 위조 재전송 적재 차단)."""
    resp = await client.post("/api/v1/webhooks/toss",
                             json={"eventType": "BILLING_DELETED", "data": {}})
    assert resp.status_code == 422
    from sqlalchemy import select

    from app.models import WebhookEvent
    assert await db.scalar(select(WebhookEvent)) is None  # 행 미생성


async def test_payment_status_refetch_error_triggers_retry(db, cipher, fake_toss,
                                                           email_sender):
    """재조회가 일시 오류면 예외를 재발생(엔드포인트 500→토스 재전송)하고 이벤트 미기록.

    서비스 계층에서 계약 검증 — HTTP 경로는 ASGITransport가 앱 예외를 재던져
    500 변환을 가리므로 handle_webhook을 직접 호출한다.
    """
    from app.services.webhooks import handle_webhook
    from app.toss.errors import TossError as TE
    from app.toss.provider import TossClientProvider  # T7: handle_webhook은 TossClientProvider를 받음
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan)
    db.add(Payment(subscription_id=sub.id, order_id="order-retry", amount=plan.price,
                   payment_type="RENEWAL", status="DONE", idempotency_key="ik-r",
                   requested_at=sub.current_period_start,
                   service_id=sub.service_id, external_user_id=sub.external_user_id))
    await db.commit()
    fake_toss.fail_lookup_with = TE("NETWORK_ERROR", "일시 오류", 0)

    # T7: handle_webhook은 TossClientProvider를 요구 — fake_toss를 override로 주입
    provider = TossClientProvider(cipher, "http://fake", override_client=fake_toss)
    with pytest.raises(TE):
        await handle_webhook(db, provider, email_sender,
                             transmission_id="wh-retry",
                             payload={"eventType": "PAYMENT_STATUS_CHANGED",
                                      "data": {"orderId": "order-retry",
                                               "status": "CANCELED"}})
    assert await db.scalar(select(WebhookEvent).where(
        WebhookEvent.transmission_id == "wh-retry")) is None


async def test_billing_deleted_reason_sanitized(client, db, cipher, email_sender):
    """페이로드 reason의 개행/제어문자는 메일 본문에 그대로 들어가지 않는다.

    Task 9: BILLING_DELETED이므로 카드(Card)와 구독(card_id)을 연결해 셋업한다.
    """
    svc, _, _ = await create_service(db, cipher, manager_email="m@x.com")
    plan = await create_plan(db, svc)
    # Task 9: 특정 billingKey("bk_san")를 가진 Card를 직접 삽입하고 구독에 연결
    card = await create_card_direct(db, cipher, svc, external_user_id="user-1",
                                    billing_key="bk_san")
    await create_subscription(db, cipher, svc, plan, card_id=card.id)
    resp = await client.post(
        "/api/v1/webhooks/toss",
        json={"eventType": "BILLING_DELETED",
              "data": {"billingKey": "bk_san", "reason": "악성\n주입\r줄"}},
        headers={"tosspayments-webhook-transmission-id": "wh-san"})
    assert resp.status_code == 200
    assert "\n악성" not in email_sender.sent[0]["body"].replace(
        "삭제되었습니다 ", "")  # 주입 개행 제거 확인
    assert "\r" not in email_sender.sent[0]["body"].split("사유:")[1]
