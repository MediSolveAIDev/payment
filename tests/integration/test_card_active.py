"""카드 활성/비활성 — 비활성 카드는 모든 결제를 차단한다 + set_card_active 토글 검증.

카드 보관함(Card Vault)에 is_active 컬럼을 추가하고, 비활성(is_active=False) 카드는
구독 생성·자동연장·수동 재결제·일반결제(one-off) 어디에서도 청구되지 않아야 한다.
"""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.errors import ConflictError, PaymentFailedError
from app.models import AuditLog, Payment
from app.notifications.email import RecordingEmailSender
from app.services import payments as payment_service
from app.services import subscriptions as subs
from app.services.cards import register_or_replace_card, set_card_active
from app.services.renewals import process_due
from app.toss.fake import FakeTossClient
from app.toss.provider import TossClientProvider  # T7: process_due는 TossClientProvider를 받음
from tests.factories import create_card, create_plan, create_service, create_subscription


@pytest.fixture
def fake():
    return FakeTossClient()


@pytest.fixture
def email():
    return RecordingEmailSender()


async def _register(db, fake, cipher, svc, uid):
    """카드 등록 헬퍼 — register_or_replace_card 래퍼."""
    return await register_or_replace_card(
        db, fake, cipher, service=svc, external_user_id=uid,
        customer_key="ck-valid-1", auth_key="auth-1")


# ── set_card_active 토글 ──────────────────────────────────────────────────────

async def test_set_card_active_toggles_and_audits(db, cipher, fake):
    """set_card_active로 비활성→활성 전환 + 감사로그(card.deactivate/activate) 기록."""
    svc, _, _ = await create_service(db, cipher)
    card = await _register(db, fake, cipher, svc, "u-toggle")
    assert card.is_active is True  # 기본 활성

    await set_card_active(db, card_id=card.id, is_active=False)
    await db.refresh(card)
    assert card.is_active is False

    await set_card_active(db, card_id=card.id, is_active=True)
    await db.refresh(card)
    assert card.is_active is True

    actions = (await db.scalars(
        select(AuditLog.action).where(AuditLog.target_type == "card",
                                      AuditLog.target_id == str(card.id)))).all()
    assert "card.deactivate" in actions
    assert "card.activate" in actions


async def test_set_card_active_is_idempotent(db, cipher, fake):
    """이미 같은 상태면 감사로그를 남기지 않는다(멱등)."""
    svc, _, _ = await create_service(db, cipher)
    card = await _register(db, fake, cipher, svc, "u-idem")
    await set_card_active(db, card_id=card.id, is_active=True)  # 이미 활성
    deact = (await db.scalars(
        select(AuditLog).where(AuditLog.target_type == "card",
                               AuditLog.target_id == str(card.id)))).all()
    # 등록(card.register) 1건만, 토글 감사로그는 없어야 함
    assert all(a.action == "card.register" for a in deact)


# ── 비활성 카드 → 결제 차단 ───────────────────────────────────────────────────

async def test_inactive_card_blocks_subscription_create(db, cipher, fake):
    """비활성 카드로는 구독을 생성할 수 없다(ConflictError)."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    card = await _register(db, fake, cipher, svc, "u-sub")
    await set_card_active(db, card_id=card.id, is_active=False)

    with pytest.raises(ConflictError):
        await subs.create_subscription(db, fake, cipher, service=svc,
                                       plan_id=plan.id, external_user_id="u-sub")
    # 토스 청구가 발생하지 않아야 한다
    assert len(fake.charges) == 0


async def test_inactive_card_blocks_one_off(db, cipher, fake):
    """비활성 카드로는 일반결제(one-off)를 할 수 없다(ConflictError)."""
    svc, _, _ = await create_service(db, cipher)
    card = await _register(db, fake, cipher, svc, "u-oneoff")
    await set_card_active(db, card_id=card.id, is_active=False)

    with pytest.raises(ConflictError):
        await payment_service.create_one_off_payment(
            db, fake, cipher, service=svc, external_user_id="u-oneoff",
            order_id="oo-inactive-1", order_name="단건", amount=5000)
    assert len(fake.charges) == 0


async def test_inactive_card_blocks_manual_retry(db, cipher, fake):
    """비활성 카드면 어드민 수동 재결제가 차단된다(PaymentFailedError CARD_INACTIVE)."""
    svc, admin_email, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    card = await create_card(db, fake, cipher, svc, external_user_id="u-retry")
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-retry",
                                    card_id=card.id, status="PAST_DUE", retry_count=1)
    await set_card_active(db, card_id=card.id, is_active=False)

    import uuid
    with pytest.raises(PaymentFailedError) as ei:
        await subs.admin_retry_payment(db, fake, cipher, subscription_id=sub.id,
                                       service_scope=None, actor_user_id=uuid.uuid4())
    assert ei.value.code == "CARD_INACTIVE"
    assert len(fake.charges) == 0


async def test_inactive_card_blocks_renewal_to_past_due(
        db, session_factory, redis_client, cipher, fake, email):
    """비활성 카드면 자동연장 시 토스 호출 없이 결제 실패 → 구독 PAST_DUE(Q3: 다음 결제 실패)."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    # 만료가 도래한 ACTIVE 구독 + 등록 카드
    start = utcnow() - timedelta(days=31)
    end = utcnow() - timedelta(minutes=5)
    card = await create_card(db, fake, cipher, svc, external_user_id="u-renew")
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-renew",
                                    card_id=card.id, period_start=start,
                                    period_end=end, next_billing_at=end)
    await set_card_active(db, card_id=card.id, is_active=False)

    # T7: process_due는 TossClientProvider를 요구 — fake를 override로 주입
    provider = TossClientProvider(cipher, "http://fake", override_client=fake)
    stats = await process_due(session_factory, redis_client, provider, cipher, email)

    assert stats["failed"] == 1
    await db.refresh(sub)
    assert sub.status == "PAST_DUE"
    # 토스 청구는 일어나지 않고(비활성 차단), 결제는 CARD_INACTIVE로 실패 기록
    assert len(fake.charges) == 0
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "FAILED"
    assert payment.failure_code == "CARD_INACTIVE"
