"""서비스 알림(아웃고잉 웹훅) — 발송 디스패치·서명·미등록 시 미발송 검증 (요청 016)."""
import asyncio
import uuid

import pytest

from app.core.security import sign_request
from app.notifications.service_notify import (
    EVENT_CARD_REGISTERED,
    EVENT_PAYMENT_ONE_OFF,
    EVENT_PAYMENT_ONE_OFF_ADMIN_CANCELED,
    EVENT_PLAN_ARCHIVED,
    EVENT_SUBSCRIPTION_CREATED,
    HttpServiceNotifier,
    RecordingServiceNotifier,
    build_payload,
)
from app.services import payments as payment_service
from app.services import plans as plan_service
from app.services import subscriptions as subs
from app.services.cards import register_or_replace_card
from app.toss.fake import FakeTossClient
from tests.factories import create_card, create_plan, create_service


@pytest.fixture
def fake():
    return FakeTossClient()


def _rec():
    return RecordingServiceNotifier()


async def _svc_with_url(db, cipher, *, url="https://svc.example.com/notify", **kw):
    svc, _, _ = await create_service(db, cipher, **kw)
    svc.notification_url = url
    await db.commit()
    return svc


# ── payload 구조 ──────────────────────────────────────────────────────────────

def test_build_payload_has_event_and_all_fields():
    class S:  # 최소 더미 서비스
        name = "My Svc"
    p = build_payload(S(), event="payment.one_off", order_id="o-1", status="DONE",
                      email="u@x.com", desc="10,000원")
    assert p["EVENT"] == "payment.one_off"
    assert set(p) == {"EVENT", "subscribe_id", "order_id", "PRE_STATUS", "STATUS",
                      "service_name", "email", "date", "DESC"}
    assert p["order_id"] == "o-1" and p["STATUS"] == "DONE"
    assert p["service_name"] == "My Svc" and p["subscribe_id"] == ""  # 없는 값은 빈 문자열


# ── URL 미등록 → 미발송 ───────────────────────────────────────────────────────

async def test_no_url_no_send(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)   # notification_url 없음
    plan = await create_plan(db, svc, price=10000)
    await create_card(db, fake, cipher, svc, external_user_id="u-no")
    rec = _rec()
    await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                   external_user_id="u-no", notifier=rec)
    assert rec.sent == []   # URL 미등록 → 발송 안 함


# ── 이벤트별 디스패치 ─────────────────────────────────────────────────────────

async def test_subscription_created_notifies(db, cipher, fake):
    svc = await _svc_with_url(db, cipher)
    plan = await create_plan(db, svc, price=10000)
    await create_card(db, fake, cipher, svc, external_user_id="u-sub")
    rec = _rec()
    sub = await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                         external_user_id="u-sub", notifier=rec)
    assert len(rec.sent) == 1
    msg = rec.sent[0]
    assert msg["EVENT"] == EVENT_SUBSCRIPTION_CREATED
    assert msg["subscribe_id"] == str(sub.id) and msg["email"] == "u-sub"
    assert msg["service_name"] == svc.name


async def test_one_off_payment_notifies(db, cipher, fake):
    svc = await _svc_with_url(db, cipher)
    await create_card(db, fake, cipher, svc, external_user_id="u-oo")
    rec = _rec()
    await payment_service.create_one_off_payment(
        db, fake, cipher, service=svc, external_user_id="u-oo",
        order_id="oo-notify-1", order_name="상품", amount=5000, notifier=rec)
    assert [m["EVENT"] for m in rec.sent] == [EVENT_PAYMENT_ONE_OFF]
    assert rec.sent[0]["order_id"] == "oo-notify-1" and "5,000" in rec.sent[0]["DESC"]


async def test_card_register_notifies(db, cipher, fake):
    svc = await _svc_with_url(db, cipher)
    rec = _rec()
    await register_or_replace_card(db, fake, cipher, service=svc,
                                   external_user_id="u-card", customer_key="ck-1",
                                   auth_key="auth-1", notifier=rec)
    assert [m["EVENT"] for m in rec.sent] == [EVENT_CARD_REGISTERED]
    assert rec.sent[0]["email"] == "u-card"


async def test_plan_archive_notifies(db, cipher, fake):
    svc = await _svc_with_url(db, cipher)
    plan = await create_plan(db, svc, price=10000)
    rec = _rec()
    await plan_service.archive_plan(db, plan_id=plan.id, service_id=svc.id,
                                    actor_user_id=uuid.uuid4(), notifier=rec)
    assert [m["EVENT"] for m in rec.sent] == [EVENT_PLAN_ARCHIVED]
    assert plan.name in rec.sent[0]["DESC"] and rec.sent[0]["email"] == ""  # 플랜 이벤트는 사용자 비귀속


async def test_admin_one_off_cancel_notifies(db, cipher, fake):
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc = await _svc_with_url(db, cipher)
    p = Payment(subscription_id=None, service_id=svc.id, external_user_id="u-c",
                order_id="oo-cancel-n", amount=10000, payment_type=PaymentType.ONE_OFF,
                kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                idempotency_key="oo-cancel-n", toss_payment_key="pay_x",
                requested_at=utcnow())
    db.add(p); await db.commit(); await db.refresh(p)
    rec = _rec()
    await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=3000, reason="x",
        actor_user_id=uuid.uuid4(), notifier=rec)
    assert [m["EVENT"] for m in rec.sent] == [EVENT_PAYMENT_ONE_OFF_ADMIN_CANCELED]
    assert "3,000" in rec.sent[0]["DESC"]


# ── 서명(HMAC) ────────────────────────────────────────────────────────────────

class _CapturingHttp(HttpServiceNotifier):
    """실제 POST를 가로채 (url, body, headers)를 기록하는 테스트용 발송기."""
    def __init__(self, cipher):
        super().__init__(cipher)
        self.posted = []

    async def _post(self, url, body, headers, event):  # 네트워크 대신 캡처
        self.posted.append((url, body, headers))


async def test_http_notifier_signs_request(db, cipher, fake):
    svc, _, secret = await create_service(db, cipher)
    svc.notification_url = "https://svc.example.com/hooks/notify"
    await db.commit()
    notifier = _CapturingHttp(cipher)
    await notifier.send(svc, event="payment.one_off", order_id="o-1", status="DONE")
    await asyncio.sleep(0)   # 백그라운드 POST 태스크 실행 기회 부여
    assert len(notifier.posted) == 1
    url, body, headers = notifier.posted[0]
    assert url == "https://svc.example.com/hooks/notify"
    # 수신 측이 동일 방식으로 서명을 재계산해 검증 가능해야 한다
    expected = sign_request(secret, "POST", "/hooks/notify",
                            headers["X-Timestamp"], headers["X-Nonce"], body)
    assert headers["X-Signature"] == expected
    assert headers["X-Event"] == "payment.one_off"


# ── 테스트 알림 전송(send_test) ───────────────────────────────────────────────

async def test_send_test_records_when_url_set(db, cipher):
    svc = await _svc_with_url(db, cipher)
    rec = _rec()
    ok, detail = await rec.send_test(svc)
    assert ok is True and rec.sent[0]["EVENT"] == "notification.test"


async def test_send_test_fails_when_no_url(db, cipher):
    svc, _, _ = await create_service(db, cipher)   # URL 미등록
    rec = _rec()
    ok, detail = await rec.send_test(svc)
    assert ok is False and "URL" in detail and rec.sent == []


# ── 스케줄러(자동결제) 알림 ───────────────────────────────────────────────────

async def test_renewal_notifies(db, session_factory, redis_client, cipher, fake):
    """자동연장 결제 성공 시 subscription.renewed 알림 발송."""
    from datetime import timedelta
    from app.core.clock import utcnow
    from app.notifications.email import RecordingEmailSender
    from app.notifications.service_notify import EVENT_SUBSCRIPTION_RENEWED
    from app.services.renewals import process_due
    from tests.factories import create_subscription
    svc = await _svc_with_url(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    card = await create_card(db, fake, cipher, svc, external_user_id="u-renew")
    end = utcnow() - timedelta(minutes=5)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-renew",
                              card_id=card.id, period_start=utcnow() - timedelta(days=31),
                              period_end=end, next_billing_at=end)
    rec = _rec()
    stats = await process_due(session_factory, redis_client, fake, cipher,
                              RecordingEmailSender(), notifier=rec)
    assert stats["renewed"] == 1
    assert any(m["EVENT"] == EVENT_SUBSCRIPTION_RENEWED for m in rec.sent)
