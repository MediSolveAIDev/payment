from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.errors import (
    ConflictError,
    InputValidationError,
    NotFoundError,
    PaymentFailedError,
)
from app.models import Payment, Subscription, SubscriptionStatus
from app.services import subscriptions as subs
from app.toss.errors import TossError
from app.toss.fake import FakeTossClient
from tests.factories import (
    create_card,
    create_plan,
    create_service,
    create_subscription,
    create_user,
)


@pytest.fixture
def fake():
    return FakeTossClient()


async def _sub_with_card(db, toss, cipher, svc, plan, *, external_user_id, **kw):
    """카드(Card Vault)를 먼저 등록하고 그 card_id를 가진 구독을 만든다(Task 8).

    수동 결제·관리자 재결제는 cards 테이블의 빌링키를 사용하므로,
    결제가 일어나는 테스트 구독은 반드시 등록된 카드를 참조해야 한다.
    """
    card = await create_card(db, toss, cipher, svc, external_user_id=external_user_id)
    return await create_subscription(db, cipher, svc, plan,
                                     external_user_id=external_user_id,
                                     card_id=card.id, **kw)


# ---------------- Trial ----------------

async def test_create_trial_no_charge_period_is_trial_days(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, trial_enabled=True, trial_days=14)
    # Card Vault(Task 7): 구독 전 카드 먼저 등록 — create_subscription은 customer_key/auth_key를 받지 않음
    await create_card(db, fake, cipher, svc, external_user_id="u-trial",
                      customer_key="ck-tr", auth_key="a")
    sub = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-trial", trial=True)
    assert sub.status == "TRIAL"
    assert fake.charges == []                       # 가입 시 결제 없음
    assert fake.issued                              # 빌링키는 등록됨(만료 시 자동결제용)
    # 체험 기간 = trial_days, 만료 시점이 첫 정기 결제일
    delta = (sub.current_period_end - sub.current_period_start).days
    assert 13 <= delta <= 14
    assert sub.next_billing_at == sub.current_period_end
    assert await db.scalar(select(Payment).where(Payment.subscription_id == sub.id)) is None


async def test_trial_rejected_when_plan_has_no_trial(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, trial_enabled=False)
    # 요금제 검증은 카드 조회 전에 일어나므로 카드 등록 없이도 InputValidationError 발생
    with pytest.raises(InputValidationError):
        await subs.create_subscription(
            db, fake, cipher, service=svc, plan_id=plan.id,
            external_user_id="u-nt", trial=True)


async def test_trial_cancel_is_immediate(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, trial_enabled=True, trial_days=14)
    # Card Vault(Task 7): 구독 전 카드 먼저 등록 — create_subscription은 customer_key/auth_key를 받지 않음
    await create_card(db, fake, cipher, svc, external_user_id="u-tc",
                      customer_key="ck-tc", auth_key="a")
    await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-tc", trial=True)
    sub = await subs.cancel_subscription(db, service=svc, external_user_id="u-tc")
    assert sub.status == "CANCELED"
    assert sub.next_billing_at is None
    assert sub.current_period_end <= utcnow() + timedelta(seconds=1)  # 즉시 만료


# ---------------- Manual pay (Suspended) ----------------

async def test_manual_pay_revives_suspended_and_resets_anchor(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="u-susp",
                               status="SUSPENDED", retry_count=4,
                               next_billing_at=None)
    sub.suspended_at = utcnow() - timedelta(days=3)
    await db.commit()

    now = utcnow()
    revived = await subs.manual_charge_subscription(
        db, fake, cipher, service=svc, external_user_id="u-susp")
    assert revived.status == "ACTIVE"
    assert revived.retry_count == 0
    assert revived.suspended_at is None
    assert fake.charges[0]["amount"] == 10000
    # 기준일 리셋: 새 주기가 결제 시점부터
    assert revived.current_period_start >= now
    payment = await db.scalar(select(Payment).where(
        Payment.subscription_id == sub.id, Payment.status == "DONE"))
    assert payment is not None


async def test_manual_pay_requires_suspended(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-act")  # ACTIVE
    with pytest.raises(NotFoundError):
        await subs.manual_charge_subscription(
            db, fake, cipher, service=svc, external_user_id="u-act")


async def test_manual_pay_allows_past_due(db, cipher, fake):
    """PAST_DUE(실패중) 구독도 수동 결제 허용 — 성공 시 ACTIVE 복귀 (요청 012)."""
    from app.models import SubscriptionStatus
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="pd",
                               status="PAST_DUE", next_billing_at=None)
    out = await subs.manual_charge_subscription(
        db, fake, cipher, service=svc, external_user_id="pd")
    assert out.status == SubscriptionStatus.ACTIVE


async def test_manual_pay_failure_keeps_suspended(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="u-sf",
                               status="SUSPENDED", next_billing_at=None)
    fake.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)
    with pytest.raises(PaymentFailedError):
        await subs.manual_charge_subscription(
            db, fake, cipher, service=svc, external_user_id="u-sf")
    await db.refresh(sub)
    assert sub.status == "SUSPENDED"  # 실패 시 정지 유지
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "FAILED"


# ---------------- 외부 API 사용일 추가 (add_usage_days) ----------------

async def test_add_usage_days_extends_active(db, cipher, fake):
    """이용 중(ACTIVE) 구독에 사용일 추가 → 만료일·다음결제 +N일, 상태 유지, 감사 SERVICE."""
    from app.models import AuditLog
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    base = utcnow().replace(microsecond=0)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="ud-act",
                                    status="ACTIVE", period_end=base, next_billing_at=base)
    out = await subs.add_usage_days(db, service=svc, external_user_id="ud-act", days=30)
    assert out.current_period_end == base + timedelta(days=30)
    assert out.next_billing_at == base + timedelta(days=30)
    assert out.status == "ACTIVE"
    log = await db.scalar(select(AuditLog).where(
        AuditLog.action == "subscription.usage_added", AuditLog.target_id == str(sub.id)))
    assert log is not None and log.actor_type == "SERVICE" and log.detail["days"] == 30


async def test_add_usage_days_keeps_none_next_billing(db, cipher, fake):
    """다음결제 None(PAST_DUE 등)은 그대로 유지, 만료일만 +N일."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    base = utcnow().replace(microsecond=0)
    await create_subscription(db, cipher, svc, plan, external_user_id="ud-pd",
                              status="PAST_DUE", period_end=base, next_billing_at=None)
    out = await subs.add_usage_days(db, service=svc, external_user_id="ud-pd", days=10)
    assert out.current_period_end == base + timedelta(days=10)
    assert out.next_billing_at is None


async def test_add_usage_days_rejects_non_active_state(db, cipher, fake):
    """이용 중이 아닌 상태(CANCELED 등)는 ConflictError."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="ud-canc",
                              status="CANCELED", next_billing_at=None)
    with pytest.raises(ConflictError):
        await subs.add_usage_days(db, service=svc, external_user_id="ud-canc", days=10)


async def test_add_usage_days_no_subscription(db, cipher, fake):
    """구독이 없으면 NotFoundError."""
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(NotFoundError):
        await subs.add_usage_days(db, service=svc, external_user_id="nobody", days=10)


async def test_add_usage_days_rejects_bad_days(db, cipher, fake):
    """일수 범위(1~3650) 밖이면 InputValidationError."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="ud-bad",
                              status="ACTIVE")
    with pytest.raises(InputValidationError):
        await subs.add_usage_days(db, service=svc, external_user_id="ud-bad", days=0)


# ---------------- Admin 만료일 연장 (구독 상세 '만료일 연장') ----------------

async def test_extend_subscription_sets_extended_and_dates(db, cipher, fake):
    """열린 구독 연장 → status=EXTENDED(연장처리), 만료일·다음결제=입력일, 감사 전/후 기록."""
    from app.models import AuditLog
    svc, _, _ = await create_service(db, cipher)
    admin, _ = await create_user(db)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="ext-u",
                                    status="ACTIVE")
    new_end = utcnow() + timedelta(days=60)
    out = await subs.extend_subscription(db, subscription_id=sub.id, service_scope=None,
                                         new_end=new_end, actor_user_id=admin.id)
    assert out.status == SubscriptionStatus.EXTENDED
    assert out.current_period_end == new_end and out.next_billing_at == new_end
    log = await db.scalar(select(AuditLog).where(
        AuditLog.action == "subscription.extended", AuditLog.target_id == str(sub.id)))
    assert log is not None
    assert log.detail["new_status"] == "EXTENDED"
    assert log.detail["new_period_end"] == new_end.isoformat()


async def test_extend_subscription_rejects_expired(db, cipher, fake):
    """EXPIRED(완전 종료) 구독은 연장 불가(ConflictError) — 재구독으로 처리."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="ext-exp",
                                    status="EXPIRED", next_billing_at=None)
    with pytest.raises(ConflictError):
        await subs.extend_subscription(db, subscription_id=sub.id, service_scope=None,
                                       new_end=utcnow() + timedelta(days=30),
                                       actor_user_id=None)


async def test_extend_subscription_rejects_past_date(db, cipher, fake):
    """과거 날짜로는 연장 불가(InputValidationError)."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="ext-past")
    with pytest.raises(InputValidationError):
        await subs.extend_subscription(db, subscription_id=sub.id, service_scope=None,
                                       new_end=utcnow() - timedelta(days=1),
                                       actor_user_id=None)


async def test_extend_subscription_scope_enforced(db, cipher, fake):
    """담당 범위 밖 구독은 연장 불가(NotFoundError)."""
    import uuid as _uuid
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="ext-scope")
    with pytest.raises(NotFoundError):
        await subs.extend_subscription(db, subscription_id=sub.id,
                                       service_scope=[_uuid.uuid4()],
                                       new_end=utcnow() + timedelta(days=30),
                                       actor_user_id=None)


# ---------------- Admin 재결제 (구독 상세 '결제 처리' 버튼) ----------------

async def test_admin_retry_payment_revives_and_audits_as_user(db, cipher, fake):
    """담당자가 PAST_DUE 구독을 재결제 → ACTIVE 복귀 + 감사 로그 actor_type=USER."""
    from app.models import AuditLog
    svc, _, _ = await create_service(db, cipher)
    user, _ = await create_user(db)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="pd-admin",
                               status="PAST_DUE", retry_count=2, next_billing_at=None)
    out = await subs.admin_retry_payment(db, fake, cipher, subscription_id=sub.id,
                                         service_scope=None, actor_user_id=user.id)
    assert out.status == "ACTIVE" and out.retry_count == 0
    assert fake.charges[0]["amount"] == 10000
    # 감사 로그가 관리자(USER) 행위자로 기록되었는지 확인
    log = await db.scalar(select(AuditLog).where(
        AuditLog.action == "subscription.manual_pay",
        AuditLog.target_id == str(sub.id)))
    assert log is not None and log.actor_type == "USER" and log.actor_user_id == user.id


async def test_admin_retry_payment_rejects_active(db, cipher, fake):
    """ACTIVE 등 실패/정지가 아닌 상태는 결제 처리 불가(ConflictError)."""
    from app.core.errors import ConflictError
    svc, _, _ = await create_service(db, cipher)
    user, _ = await create_user(db)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="act-admin")
    with pytest.raises(ConflictError):
        await subs.admin_retry_payment(db, fake, cipher, subscription_id=sub.id,
                                       service_scope=None, actor_user_id=user.id)


async def test_admin_retry_payment_scope_enforced(db, cipher, fake):
    """담당 범위 밖 구독은 NotFoundError(존재 여부 비노출)."""
    import uuid as _uuid
    svc, _, _ = await create_service(db, cipher)
    user, _ = await create_user(db)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="scope-admin",
                                    status="SUSPENDED", next_billing_at=None)
    with pytest.raises(NotFoundError):
        await subs.admin_retry_payment(db, fake, cipher, subscription_id=sub.id,
                                       service_scope=[_uuid.uuid4()],  # 다른 서비스만 담당
                                       actor_user_id=user.id)


async def test_admin_retry_payment_failure_keeps_state(db, cipher, fake):
    """결제 거절 시 상태 유지(SUSPENDED) + 결제 FAILED 기록."""
    svc, _, _ = await create_service(db, cipher)
    user, _ = await create_user(db)
    plan = await create_plan(db, svc)
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="sf-admin",
                               status="SUSPENDED", next_billing_at=None)
    fake.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)
    with pytest.raises(PaymentFailedError):
        await subs.admin_retry_payment(db, fake, cipher, subscription_id=sub.id,
                                       service_scope=None, actor_user_id=user.id)
    await db.refresh(sub)
    assert sub.status == "SUSPENDED"
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "FAILED"


# ---------------- 상시 할인 (요청 003) ----------------

async def test_recurring_discount_applied_on_create_and_renewal(
        db, session_factory, redis_client, cipher, fake):
    """첫 결제 = 정가에 첫구독 할인만 적용, 갱신 = 상시 할인가.
    요청 005: 첫 결제는 정가 기준 (상시 할인 미적용).
    """
    from datetime import timedelta
    from app.core.clock import utcnow
    from app.services.renewals import process_due
    from app.notifications.email import RecordingEmailSender
    from app.toss.provider import TossClientProvider  # T7: process_due는 TossClientProvider를 받음

    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH",
                             recurring_discount_type="DISCOUNT_PERCENT",
                             recurring_discount_value=10,           # 상시 10% → 9000 (2회차~)
                             first_payment_type="DISCOUNT_AMOUNT",
                             first_payment_value=1000)              # 첫구독 -1000
    # Card Vault(Task 7): 구독 전 카드 먼저 등록 — create_subscription은 customer_key/auth_key를 받지 않음
    await create_card(db, fake, cipher, svc, external_user_id="u-rec",
                      customer_key="ck-rec", auth_key="a")
    sub = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-rec")
    # 요청 005: 첫 결제는 정가 기준 — 10000 − 1000 = 9000 (상시 10% 무시)
    assert fake.charges[0]["amount"] == 9000  # 첫 결제: 정가 10000 − 1000 = 9000

    # 만료시켜 갱신 → 상시 할인가(9000)
    sub.current_period_end = utcnow() - timedelta(minutes=1)
    sub.next_billing_at = sub.current_period_end
    await db.commit()
    # T7: process_due는 TossClientProvider를 요구 — fake를 override로 주입
    provider = TossClientProvider(cipher, "http://fake", override_client=fake)
    await process_due(session_factory, redis_client, provider, cipher,
                      RecordingEmailSender())
    assert fake.charges[-1]["amount"] == 9000


async def test_recurring_discount_manual_pay_uses_discounted(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=20000, billing_cycle="MONTH",
                             recurring_discount_type="DISCOUNT_AMOUNT",
                             recurring_discount_value=5000)  # → 15000
    await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="u-rm",
                         status="SUSPENDED", next_billing_at=None)
    revived = await subs.manual_charge_subscription(
        db, fake, cipher, service=svc, external_user_id="u-rm")
    assert revived.status == "ACTIVE"
    assert fake.charges[-1]["amount"] == 15000
