"""어드민 결제 상세 — 전액/부분 취소 라우트 e2e (수수료 없이 누적 취소)."""
from sqlalchemy import select

from app.core.clock import utcnow
from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
from tests.factories import create_service, create_user
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


async def _done_oneoff(db, svc, *, order, amount=10000):
    p = Payment(subscription_id=None, service_id=svc.id, external_user_id="u@e.com",
                order_id=order, amount=amount, payment_type=PaymentType.ONE_OFF,
                kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                idempotency_key=order, toss_payment_key=f"pay_{order}",
                requested_at=utcnow(), approved_at=utcnow())
    db.add(p); await db.commit(); await db.refresh(p)
    return p


async def test_admin_partial_then_full_cancel_via_route(client, db, redis_client, cipher):
    """부분취소(3000) 폼 제출 → DONE 유지·환불 누적, 이후 전액(잔여) 취소 → CANCELED."""
    svc, _, _ = await create_service(db, cipher)
    svc.cancellation_fee_percent = 30; await db.commit()   # 수수료 있어도 어드민은 무시
    p = await _done_oneoff(db, svc, order="rc-1", amount=10000)
    csrf = await _admin(client, db, redis_client)

    # 부분 취소 3000원
    r1 = await client.post(f"/admin/payments/{p.id}/cancel",
                           data={"csrf_token": csrf, "cancel_amount": "3000"})
    assert r1.status_code in (302, 303)
    await db.refresh(p)
    assert p.status == PaymentStatus.DONE and p.canceled_amount == 3000

    # 상세 페이지에 부분취소·잔여 표시 확인
    html = (await client.get(f"/admin/payments/{p.id}")).text
    assert "부분취소" in html and "7,000원" in html   # 잔여 7000

    # 전액(잔여) 취소 — cancel_amount 비움
    r2 = await client.post(f"/admin/payments/{p.id}/cancel",
                           data={"csrf_token": csrf, "cancel_amount": ""})
    assert r2.status_code in (302, 303)
    await db.refresh(p)
    assert p.status == PaymentStatus.CANCELED and p.canceled_amount == 10000
    assert not p.cancel_fee   # 어드민 무수수료
