"""카드 어드민 e2e — 카드 상세(결제내역) · 활성/비활성 토글 · 결제/구독 상세 카드 표시."""
from sqlalchemy import select

from app.core.clock import utcnow
from app.models import AuditLog, Card, Payment, PaymentKind, PaymentStatus, PaymentType
from app.toss.fake import FakeTossClient
from tests.factories import (create_card, create_plan, create_service,
                             create_subscription, create_user)
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


def _oneoff(svc, uid, order_id):
    """(service, external_user_id) 매칭용 단건결제 행 — 카드별 결제내역 스코프 테스트에 사용."""
    return Payment(subscription_id=None, service_id=svc.id, external_user_id=uid,
                   order_id=order_id, amount=5000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key=order_id, requested_at=utcnow(), approved_at=utcnow())


async def test_card_detail_shows_info_and_scoped_payments(client, db, redis_client, cipher):
    """카드 상세 — 카드 정보·활성 뱃지 + 이 카드(같은 사용자)의 결제만 표시, 타 사용자 결제 제외."""
    svc, _, _ = await create_service(db, cipher, name="card-detail-svc")
    fake = FakeTossClient()
    card = await create_card(db, fake, cipher, svc, external_user_id="card-user-a")
    db.add(_oneoff(svc, "card-user-a", "pay-mine-1"))
    db.add(_oneoff(svc, "other-user-b", "pay-other-1"))  # 타 사용자 — 표시되면 안 됨
    await db.commit()
    await _admin(client, db, redis_client)

    html = (await client.get(f"/admin/cards/{card.id}")).text
    assert "card-user-a" in html
    assert "1234-****-****-5678" in html        # 마스킹 카드번호
    assert "활성" in html                         # 상태 뱃지
    assert "pay-mine-1" in html                  # 이 카드의 결제
    assert "pay-other-1" not in html             # 타 사용자 결제 미표시


async def test_card_toggle_via_post_and_audit(client, db, redis_client, cipher):
    """카드 상세에서 토글 POST → is_active 반전 + 감사로그(card.deactivate) 기록."""
    svc, _, _ = await create_service(db, cipher, name="card-toggle-svc")
    fake = FakeTossClient()
    card = await create_card(db, fake, cipher, svc, external_user_id="toggle-u")
    csrf = await _admin(client, db, redis_client)

    resp = await client.post(f"/admin/cards/{card.id}/toggle",
                             data={"csrf_token": csrf})
    assert resp.status_code in (302, 303)
    await db.refresh(card)
    assert card.is_active is False

    actions = (await db.scalars(
        select(AuditLog.action).where(AuditLog.target_type == "card",
                                      AuditLog.target_id == str(card.id)))).all()
    assert "card.deactivate" in actions


async def test_card_toggle_htmx_returns_cards_partial(client, db, redis_client, cipher):
    """서비스 상세 리스트에서 htmx 토글 → list-svc-cards partial + 비활성 뱃지로 갱신."""
    svc, _, _ = await create_service(db, cipher, name="card-htmx-toggle-svc")
    fake = FakeTossClient()
    card = await create_card(db, fake, cipher, svc, external_user_id="htmx-u")
    csrf = await _admin(client, db, redis_client)

    resp = await client.post(f"/admin/cards/{card.id}/toggle",
                             data={"csrf_token": csrf},
                             headers={"HX-Request": "true",
                                      "HX-Target": "list-svc-cards"})
    body = resp.text
    assert "<!doctype" not in body.lower()       # 전체 페이지 아님(partial만)
    assert 'id="list-svc-cards"' in body
    assert "비활성" in body                        # 토글 후 상태 반영
    await db.refresh(card)
    assert card.is_active is False


async def test_payment_detail_shows_paying_card(client, db, redis_client, cipher):
    """결제 상세에 '결제 카드' 행으로 마스킹 카드번호가 표시된다."""
    svc, _, _ = await create_service(db, cipher, name="pay-card-svc")
    fake = FakeTossClient()
    await create_card(db, fake, cipher, svc, external_user_id="pay-u")
    payment = _oneoff(svc, "pay-u", "pay-detail-1")
    db.add(payment)
    await db.commit()
    await _admin(client, db, redis_client)

    html = (await client.get(f"/admin/payments/{payment.id}")).text
    assert "결제 카드" in html
    assert "1234-****-****-5678" in html


async def test_service_detail_events_show_card_events(client, db, redis_client, cipher):
    """서비스 상세 '이벤트' 섹션에 카드 등록·비활성화 이벤트가 한글 라벨로 표시된다."""
    from app.services.cards import set_card_active
    svc, _, _ = await create_service(db, cipher, name="card-evt-svc")
    fake = FakeTossClient()
    card = await create_card(db, fake, cipher, svc, external_user_id="evt-u")
    await set_card_active(db, card_id=card.id, is_active=False)
    await _admin(client, db, redis_client)

    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "카드 등록" in html        # card.register 한글 라벨(이벤트 섹션)
    assert "카드 비활성화" in html    # card.deactivate 한글 라벨


async def test_subscription_detail_disables_retry_when_card_inactive(
        client, db, redis_client, cipher):
    """비활성 카드 구독 상세 — 비활성 뱃지 표시 + 재결제 버튼 disabled."""
    svc, _, _ = await create_service(db, cipher, name="sub-inactive-card-svc")
    plan = await create_plan(db, svc, price=10000)
    fake = FakeTossClient()
    card = await create_card(db, fake, cipher, svc, external_user_id="sub-u")
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="sub-u",
                                    card_id=card.id, status="PAST_DUE", retry_count=1)
    card.is_active = False
    await db.commit()
    await _admin(client, db, redis_client)

    html = (await client.get(f"/admin/subscriptions/{sub.id}")).text
    assert "비활성" in html
    # 재결제 버튼이 비활성화(disabled)되어 있어야 한다
    assert "disabled" in html
