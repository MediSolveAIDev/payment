from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.models import Payment
from app.notifications.email import RecordingEmailSender
from app.services.renewals import (
    DEFAULT_RETRY_LIMIT,
    DEFAULT_SUSPENDED_GRACE,
    _renewal_order_id,
    process_due,
)
from app.toss.errors import TossError, TossTimeoutError
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


@pytest.fixture
def email():
    return RecordingEmailSender()


async def _sub_with_card(db, toss, cipher, svc, plan, *, external_user_id="user-1", **kw):
    """카드(Card Vault)를 먼저 등록하고 그 card_id를 가진 구독을 만든다(Task 8).

    카드 보관함 도입 이후 갱신/재시도 결제는 cards 테이블의 빌링키를 사용하므로,
    결제가 일어나는 테스트 구독은 반드시 등록된 카드를 참조해야 한다.
    """
    # (svc, external_user_id)당 1장의 카드를 등록하고 그 id를 구독에 연결한다.
    card = await create_card(db, toss, cipher, svc, external_user_id=external_user_id)
    return await create_subscription(db, cipher, svc, plan,
                                     external_user_id=external_user_id,
                                     card_id=card.id, **kw)


async def _due_subscription(db, cipher, svc, plan, *, toss, **kw):
    """만료일이 지난 ACTIVE 구독(등록 카드 포함)."""
    start = utcnow() - timedelta(days=31)
    end = utcnow() - timedelta(minutes=5)
    defaults = dict(external_user_id="u-due", period_start=start, period_end=end,
                    next_billing_at=end)
    defaults.update(kw)
    # 카드 등록 → card_id 연결(Task 8). toss는 카드 발급에 필요(FakeTossClient).
    return await _sub_with_card(db, toss, cipher, svc, plan, **defaults)


async def test_renews_due_subscription(db, session_factory, redis_client, cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake)
    old_end = sub.current_period_end

    stats = await process_due(session_factory, redis_client, fake, cipher, email)

    assert stats["renewed"] == 1
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
    assert sub.current_period_start == old_end          # 기간 연속
    assert sub.next_billing_at == sub.current_period_end
    assert fake.charges[0]["amount"] == 10000
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "DONE"
    assert payment.payment_type == "RENEWAL"


async def test_renews_extended_subscription_to_active(db, session_factory, redis_client,
                                                      cipher, fake, email):
    """연장처리(EXTENDED) 구독도 새 만료일 도래 시 자동결제 갱신 → 성공 시 ACTIVE 복귀."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake, external_user_id="u-ext-due",
                                  status="EXTENDED")
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["renewed"] == 1
    await db.refresh(sub)
    assert sub.status == "ACTIVE"           # 연장처리 → 갱신 성공 시 정상 복귀
    assert fake.charges[0]["amount"] == 10000


async def test_not_due_untouched(db, session_factory, redis_client, cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)  # next_billing 미래
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats == {"renewed": 0, "failed": 0, "suspended": 0, "expired": 0,
                     "skipped": 0, "unresolved": 0, "reconciled": 0, "errors": 0}
    assert fake.charges == []


async def test_failure_moves_to_past_due_and_notifies(db, session_factory, redis_client,
                                                      cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake)
    fake.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)

    now = utcnow()
    stats = await process_due(session_factory, redis_client, fake, cipher, email, now=now)

    assert stats["failed"] == 1
    await db.refresh(sub)
    assert sub.status == "PAST_DUE"
    assert sub.retry_count == 1
    assert sub.next_billing_at == now + timedelta(hours=12)
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "FAILED"
    assert payment.failure_code == "INSUFFICIENT_FUNDS"
    assert len(email.sent) == 1
    assert svc.manager_email == email.sent[0]["to"]


async def test_retry_success_restores_active_continuous_period(
        db, session_factory, redis_client, cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, billing_cycle="MONTH")
    end = utcnow() - timedelta(days=2)
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="u-retry",
                               status="PAST_DUE", retry_count=1,
                               period_start=end - timedelta(days=31), period_end=end,
                               next_billing_at=utcnow() - timedelta(minutes=1))
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["renewed"] == 1
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
    assert sub.retry_count == 0
    assert sub.current_period_start == end  # 원래 만료일부터 연속
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.payment_type == "RETRY"


async def test_retries_exhausted_suspends_and_keeps_key(
        db, session_factory, redis_client, cipher, fake, email):
    """최종 실패 → SUSPENDED(접근 차단). 빌링키는 수동 결제를 위해 보존."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake, status="PAST_DUE",
                                  retry_count=DEFAULT_RETRY_LIMIT)
    fake.fail_charge_with = TossError("CARD_EXPIRED", "카드 만료", 400)

    now = utcnow()
    stats = await process_due(session_factory, redis_client, fake, cipher, email, now=now)

    assert stats["suspended"] == 1
    await db.refresh(sub)
    assert sub.status == "SUSPENDED"
    assert sub.suspended_at == now
    assert sub.next_billing_at is None
    # 카드 보관함(Card Vault): SUSPENDED는 수동 결제를 위해 카드를 보존한다.
    # 빌링키는 cards 테이블에 그대로 남고, 토스 삭제도 호출되지 않는다.
    from app.services.cards import get_card
    card = await get_card(db, service_id=svc.id, external_user_id=sub.external_user_id)
    assert card is not None and card.billing_key_encrypted is not None
    assert fake.deleted == []
    assert "정지" in email.sent[0]["subject"]


async def test_full_retry_storyline_to_suspended(db, session_factory, redis_client,
                                                 cipher, fake, email):
    """정기 1회 + 재시도 4회 = 총 5회 시도 후 SUSPENDED."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake, external_user_id="u-story")
    fake.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)

    now = utcnow()
    for _ in range(DEFAULT_RETRY_LIMIT + 1):
        await process_due(session_factory, redis_client, fake, cipher, email, now=now)
        await db.refresh(sub)
        now = now + timedelta(hours=12, minutes=1)

    assert len(fake.charges) == DEFAULT_RETRY_LIMIT + 1  # 5회
    assert sub.status == "SUSPENDED"
    # 더 돌려도 결제 시도 없음(자동 결제 중지)
    await process_due(session_factory, redis_client, fake, cipher, email, now=now)
    assert len(fake.charges) == DEFAULT_RETRY_LIMIT + 1


async def test_suspended_expires_after_grace(db, session_factory, redis_client,
                                             cipher, fake, email):
    """SUSPENDED 대기 일수 초과 → EXPIRED. 카드 보관함 도입 후 빌링키는 삭제하지 않음."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="u-susp",
                               status="SUSPENDED", next_billing_at=None)
    sub.suspended_at = utcnow() - DEFAULT_SUSPENDED_GRACE - timedelta(hours=1)
    await db.commit()

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["expired"] == 1
    await db.refresh(sub)
    assert sub.status == "EXPIRED"
    # 카드 보관함: 빌링키는 cards 테이블이 소유 → 구독 만료 시 토스 삭제를 호출하지 않는다.
    assert fake.deleted == []


async def test_suspended_within_grace_kept(db, session_factory, redis_client,
                                           cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-susp2",
                                    status="SUSPENDED", next_billing_at=None)
    sub.suspended_at = utcnow() - timedelta(days=1)  # 아직 유예 내
    await db.commit()
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["expired"] == 0
    await db.refresh(sub)
    assert sub.status == "SUSPENDED"


async def test_trial_expiry_charges_to_active(db, session_factory, redis_client,
                                              cipher, fake, email):
    """TRIAL 만료 → 자동 결제 성공 → ACTIVE(기간 전진)."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    trial_end = utcnow() - timedelta(minutes=5)
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="u-trial",
                               status="TRIAL",
                               period_start=trial_end - timedelta(days=7),
                               period_end=trial_end, next_billing_at=trial_end)
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["renewed"] == 1
    assert fake.charges[0]["amount"] == 10000  # 만료 시 정가 결제
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
    assert sub.current_period_start == trial_end  # 체험 종료일부터 전진


async def test_trial_charge_failure_goes_past_due(db, session_factory, redis_client,
                                                  cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    trial_end = utcnow() - timedelta(minutes=5)
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="u-trialf",
                               status="TRIAL", period_end=trial_end,
                               next_billing_at=trial_end)
    fake.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["failed"] == 1
    await db.refresh(sub)
    assert sub.status == "PAST_DUE"
    assert sub.retry_count == 1


async def test_canceled_expires_at_period_end_without_charge(
        db, session_factory, redis_client, cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _sub_with_card(db, fake, cipher, svc, plan, external_user_id="u-cx",
                               status="CANCELED",
                               period_start=utcnow() - timedelta(days=31),
                               period_end=utcnow() - timedelta(minutes=1),
                               next_billing_at=None)
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["expired"] == 1
    await db.refresh(sub)
    assert sub.status == "EXPIRED"
    assert fake.charges == []
    # 카드 보관함: 구독 만료가 토스 빌링키를 삭제하지 않는다(카드는 vault가 관리).
    assert fake.deleted == []


async def test_canceled_before_period_end_kept(db, session_factory, redis_client,
                                               cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-ck",
                                    status="CANCELED", next_billing_at=None)
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["expired"] == 0
    await db.refresh(sub)
    assert sub.status == "CANCELED"


async def test_redis_lock_prevents_double_charge(db, session_factory, redis_client,
                                                 cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake)
    await redis_client.set(f"lock:renew:{sub.id}", "1", ex=60)  # 다른 워커가 처리 중인 상황

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["skipped"] == 1
    assert fake.charges == []


async def test_crash_recovery_done_payment_advances_without_recharge(
        db, session_factory, redis_client, cipher, fake, email):
    """직전 실행이 '결제 성공 후 커밋 전' 크래시 → 같은 order_id의 DONE 결제 발견 시 재결제 없이 기간만 복구."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake, external_user_id="u-crash")
    db.add(Payment(subscription_id=sub.id, order_id=_renewal_order_id(sub),
                   amount=plan.price, payment_type="RENEWAL", status="DONE",
                   toss_payment_key="pay_recovered", idempotency_key="ik",
                   requested_at=utcnow(), approved_at=utcnow(),
                   service_id=sub.service_id, external_user_id=sub.external_user_id))
    await db.commit()

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["renewed"] == 1
    assert fake.charges == []  # 재결제 없음
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
    assert sub.next_billing_at == sub.current_period_end


async def test_renewal_timeout_resolved_by_lookup(db, session_factory, redis_client,
                                                  cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake, external_user_id="u-rt")
    fake.succeed_despite_timeout = True
    fake.charge_failure_queue = [TossTimeoutError()]

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["renewed"] == 1
    await db.refresh(sub)
    assert sub.status == "ACTIVE"


async def test_renewal_timeout_unresolved_preserved_then_converges(
        db, session_factory, redis_client, cipher, fake, email):
    """갱신 타임아웃(결과불명)은 실패 처리하지 않고, 다음 배치에서 같은 멱등키로 수렴."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake, external_user_id="u-unres")
    fake.charge_failure_queue = [TossTimeoutError()]

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["unresolved"] == 1
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
    assert sub.retry_count == 0  # 실패로 세지 않음
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "PENDING"
    first_order_id = payment.order_id

    # 다음 배치 — 같은 order_id/멱등키로 재시도, 이번엔 성공
    stats2 = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats2["renewed"] == 1
    assert fake.charges[1]["order_id"] == first_order_id
    assert fake.charges[1]["idempotency_key"] == fake.charges[0]["idempotency_key"]
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
    assert sub.current_period_start is not None


async def test_reconcile_stuck_first_payment_done(db, session_factory, redis_client,
                                                  cipher, fake, email):
    """결과불명으로 남은 FIRST PENDING — 토스에 DONE이 있으면 확정."""
    from datetime import timedelta as td

    from app.core.clock import utcnow as now_fn
    from app.toss.types import ChargeResult
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=9000)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-rec")
    payment = Payment(subscription_id=sub.id, order_id="frec1order", amount=9000,
                      payment_type="FIRST", status="PENDING", idempotency_key="ik-rec",
                      requested_at=now_fn() - td(minutes=20),
                      service_id=sub.service_id, external_user_id=sub.external_user_id)
    db.add(payment)
    await db.commit()
    fake.payments_by_order["frec1order"] = ChargeResult(
        payment_key="pay_rec", order_id="frec1order", status="DONE", raw={})

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["reconciled"] == 1
    await db.refresh(payment)
    assert payment.status == "DONE"
    assert payment.toss_payment_key == "pay_rec"
    await db.refresh(sub)
    assert sub.status == "ACTIVE"


async def test_reconcile_stuck_first_payment_not_found_expires(
        db, session_factory, redis_client, cipher, fake, email):
    """유예 후에도 토스에 기록 없음 — FAILED 확정 + 구독 만료. 카드 보관함: 키 삭제 안 함."""
    from datetime import timedelta as td

    from app.core.clock import utcnow as now_fn
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-rec2")
    payment = Payment(subscription_id=sub.id, order_id="frec2order", amount=10000,
                      payment_type="FIRST", status="PENDING", idempotency_key="ik-rec2",
                      requested_at=now_fn() - td(minutes=20),
                      service_id=sub.service_id, external_user_id=sub.external_user_id)
    db.add(payment)
    await db.commit()

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["reconciled"] == 1
    await db.refresh(payment)
    assert payment.status == "FAILED"
    assert payment.failure_code == "RECONCILE_NOT_FOUND"
    await db.refresh(sub)
    assert sub.status == "EXPIRED"
    # 카드 보관함: 구독 만료가 토스 빌링키를 삭제하지 않는다(카드는 vault가 관리).
    assert fake.deleted == []


async def test_reconcile_young_pending_untouched(db, session_factory, redis_client,
                                                 cipher, fake, email):
    """유예 기간(10분) 이전의 PENDING은 건드리지 않는다."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-rec3")
    from app.core.clock import utcnow as now_fn
    payment = Payment(subscription_id=sub.id, order_id="frec3order", amount=10000,
                      payment_type="FIRST", status="PENDING", idempotency_key="ik-rec3",
                      requested_at=now_fn(),
                      service_id=sub.service_id, external_user_id=sub.external_user_id)
    db.add(payment)
    await db.commit()
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["reconciled"] == 0
    await db.refresh(payment)
    assert payment.status == "PENDING"


async def test_reconcile_canceled_sub_with_done_payment_stays_canceled(
        db, session_factory, redis_client, cipher, fake, email):
    """결제 확정 시점에 이미 취소된 구독은 CANCELED 유지(기간 혜택 유지 후 만료)."""
    from datetime import timedelta as td

    from app.core.clock import utcnow as now_fn
    from app.toss.types import ChargeResult
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-rec4",
                                    status="CANCELED", next_billing_at=None)
    payment = Payment(subscription_id=sub.id, order_id="frec4order", amount=10000,
                      payment_type="FIRST", status="PENDING", idempotency_key="ik-rec4",
                      requested_at=now_fn() - td(minutes=20),
                      service_id=sub.service_id, external_user_id=sub.external_user_id)
    db.add(payment)
    await db.commit()
    fake.payments_by_order["frec4order"] = ChargeResult(
        payment_key="pay_rec4", order_id="frec4order", status="DONE", raw={})

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["reconciled"] == 1
    await db.refresh(sub)
    assert sub.status == "CANCELED"


async def test_reconcile_orphan_renewal_on_canceled_sub_done(
        db, session_factory, redis_client, cipher, fake, email):
    """취소된 구독의 결과불명 RENEWAL이 DONE으로 확정되면 수동 검토 메일 발송."""
    from datetime import timedelta as td

    from app.core.clock import utcnow as now_fn
    from app.toss.types import ChargeResult
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-orph",
                                    status="CANCELED", next_billing_at=None)
    payment = Payment(subscription_id=sub.id, order_id="orphrenew1", amount=plan.price,
                      payment_type="RENEWAL", status="PENDING", idempotency_key="ik-or",
                      requested_at=now_fn() - td(minutes=20),
                      service_id=sub.service_id, external_user_id=sub.external_user_id)
    db.add(payment)
    await db.commit()
    fake.payments_by_order["orphrenew1"] = ChargeResult(
        payment_key="pay_orph", order_id="orphrenew1", status="DONE", raw={})

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["reconciled"] == 1
    await db.refresh(payment)
    assert payment.status == "DONE"
    assert any("수동 확인" in m["subject"] for m in email.sent)


async def test_reconcile_skips_renewal_pending_while_sub_in_pool(
        db, session_factory, redis_client, cipher, fake, email):
    """갱신 풀(ACTIVE/PAST_DUE)에 있는 구독의 RENEWAL PENDING은 _renew_one 수렴에 맡긴다."""
    from datetime import timedelta as td

    from app.core.clock import utcnow as now_fn
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-pool",
                                    period_start=now_fn() - td(days=31),
                                    period_end=now_fn() + td(days=1))  # 아직 due 아님
    payment = Payment(subscription_id=sub.id, order_id="poolrenew1", amount=plan.price,
                      payment_type="RENEWAL", status="PENDING", idempotency_key="ik-pl",
                      requested_at=now_fn() - td(minutes=20),
                      service_id=sub.service_id, external_user_id=sub.external_user_id)
    db.add(payment)
    await db.commit()

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["reconciled"] == 0
    await db.refresh(payment)
    assert payment.status == "PENDING"


async def test_delete_404_treated_as_success(db, cipher, fake):
    """토스가 404를 주면 키는 이미 없음 — 삭제 성공으로 간주해 암호문 정리."""
    from app.services.payment_utils import safe_delete_billing_key
    from app.toss.errors import TossError as TE
    fake.fail_delete_with = TE("NOT_FOUND_BILLING_KEY", "없음", 404)
    assert await safe_delete_billing_key(fake, "bk_gone") is True


async def test_non_renewing_expires_at_period_end(db, session_factory, redis_client,
                                                    cipher, fake, email):
    """auto_renew=False 구독 기간 종료 시 process_due가 EXPIRED로 만료 처리한다 (요청 013).

    ACTIVE + next_billing_at=None + current_period_end <= now → EXPIRED
    갱신 결제는 발생하지 않아야 한다.
    """
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, auto_renew=False)
    # 기간 만료된 ACTIVE 구독, next_billing_at=None(auto_renew=False 표시)
    end = utcnow() - timedelta(minutes=5)
    sub = await create_subscription(db, cipher, svc, plan,
                                    external_user_id="u-nonrenew",
                                    status="ACTIVE",
                                    period_start=end - timedelta(days=30),
                                    period_end=end,
                                    next_billing_at=None)  # 자동갱신 없음
    stats = await process_due(session_factory, redis_client, fake, cipher, email)

    assert stats["expired"] == 1            # 만료 처리됨
    assert stats["renewed"] == 0            # 갱신 결제 없음
    assert fake.charges == []               # 결제 미발생
    await db.refresh(sub)
    assert sub.status == "EXPIRED"          # 최종 만료 상태
    assert sub.next_billing_at is None      # 다음 결제 없음


async def test_retry_limit_from_global_settings(db, session_factory, redis_client, cipher, fake, email):
    """GlobalSettings.retry_limit=1 로 낮추면 1회 실패 후 바로 SUSPENDED 전환됨을 검증(요청 013).

    process_due가 DB GlobalSettings에서 재시도 설정을 로드하므로,
    update_retry_settings로 낮춘 값이 다음 배치에 즉시 반영된다.
    """
    from app.services import app_settings

    # SYSTEM_ADMIN 사용자 생성 후 GlobalSettings.retry_limit를 1로 낮춤
    u, _ = await create_user(db, role="SYSTEM_ADMIN")
    await app_settings.update_retry_settings(
        db, retry_limit=1, retry_interval_hours=12,
        suspended_grace_days=30, actor_user_id=u.id)

    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    # retry_count=1(= retry_limit) 인 PAST_DUE 구독 — 다음 배치에서 즉시 SUSPENDED 전환 예상
    sub = await _due_subscription(db, cipher, svc, plan, toss=fake, status="PAST_DUE", retry_count=1,
                                  external_user_id="u-gs-retry")
    fake.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)

    now = utcnow()
    stats = await process_due(session_factory, redis_client, fake, cipher, email, now=now)

    # retry_limit=1이므로 retry_count(1) >= limit(1) → 즉시 SUSPENDED
    assert stats["suspended"] == 1
    await db.refresh(sub)
    assert sub.status == "SUSPENDED"
    assert sub.suspended_at == now
    assert sub.next_billing_at is None
    assert "정지" in email.sent[0]["subject"]
