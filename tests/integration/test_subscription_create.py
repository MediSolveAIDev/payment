"""구독 생성 통합 테스트 — Task 7: auth_key 제거, 등록 카드 사용.

각 테스트는 create_subscription 호출 전에 반드시 카드를 먼저 등록한다.
(register_or_replace_card 또는 factories.create_card 사용)
카드 미등록 시 NotFoundError가 발생하는지도 검증한다.
"""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, InputValidationError, NotFoundError, PaymentFailedError
from app.models import Payment, Service, Subscription
from app.services import subscriptions as subs
from app.services.cards import register_or_replace_card
from app.toss.errors import TossError, TossTimeoutError
from app.toss.fake import FakeTossClient
from tests.factories import create_card, create_plan, create_service, create_subscription


@pytest.fixture
def fake():
    return FakeTossClient()


# ── 헬퍼: 카드 등록 후 create_subscription 호출 ───────────────────────────────

async def _register_card(db, fake, cipher, svc, external_user_id, *,
                         customer_key="ck-valid-1", auth_key="auth-1"):
    """테스트용 카드 등록 헬퍼 — register_or_replace_card 래퍼."""
    return await register_or_replace_card(
        db, fake, cipher,
        service=svc,
        external_user_id=external_user_id,
        customer_key=customer_key,
        auth_key=auth_key,
    )


# ── 기본 구독 생성 테스트 ────────────────────────────────────────────────────


async def test_create_with_full_price(db, cipher, fake):
    """정가 요금제 구독 생성 — 카드 사전 등록 후 create_subscription."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    # Task 7: 구독 전 카드 등록 필수
    await _register_card(db, fake, cipher, svc, "u-1",
                         customer_key="ck-valid-1", auth_key="auth-1")

    sub = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-1")

    assert sub.status == "ACTIVE"
    assert sub.next_billing_at == sub.current_period_end
    # 빌링키는 cards 테이블에 있으므로 sub에는 없음(card_id FK만 존재)
    assert sub.card_id is not None
    assert fake.charges[0]["amount"] == 10000
    assert fake.charges[0]["idempotency_key"] == f"first-{sub.id}"

    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "DONE"
    assert payment.payment_type == "FIRST"
    assert payment.amount == 10000
    assert payment.toss_payment_key.startswith("pay_")


async def test_first_subscription_free_skips_charge(db, cipher, fake):
    """첫구독 무료 요금제 — 카드 등록 후 결제 없이 ACTIVE."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, first_payment_type="FREE")
    await _register_card(db, fake, cipher, svc, "u-free",
                         customer_key="ck-free", auth_key="a")

    sub = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-free")
    assert sub.status == "ACTIVE"
    # 빌링키 발급 1회(카드 등록) + 결제 없음
    assert len(fake.charges) == 0
    assert await db.scalar(select(Payment).where(Payment.subscription_id == sub.id)) is None


async def test_free_benefit_not_repeatable(db, cipher, fake):
    """무료 첫구독을 쓰고 만료된 뒤 재구독하면 정가 결제 (무한 무료 방지)."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, first_payment_type="FREE")
    await _register_card(db, fake, cipher, svc, "u-free2",
                         customer_key="ck-free2", auth_key="a")

    sub1 = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-free2")
    assert len(fake.charges) == 0  # 첫 구독은 무료
    sub1.status = "EXPIRED"
    await db.commit()

    await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-free2")
    assert len(fake.charges) == 1
    assert fake.charges[0]["amount"] == 10000  # 재구독은 정가


async def test_first_subscription_discount_amount(db, cipher, fake):
    """첫구독 정액 할인 — 7000원 청구."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000,
                             first_payment_type="DISCOUNT_AMOUNT", first_payment_value=3000)
    await _register_card(db, fake, cipher, svc, "u-dc",
                         customer_key="ck-dc", auth_key="a")

    await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                   external_user_id="u-dc")
    assert fake.charges[0]["amount"] == 7000


async def test_resubscribe_after_expiry_pays_full_price(db, cipher, fake):
    """DONE 결제 이력이 있으면 재구독은 정가."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000,
                             first_payment_type="DISCOUNT_AMOUNT", first_payment_value=9000)
    await _register_card(db, fake, cipher, svc, "u-re",
                         customer_key="ck-re", auth_key="a")

    sub1 = await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                          external_user_id="u-re")
    assert fake.charges[0]["amount"] == 1000  # 첫구독 할인
    sub1.status = "EXPIRED"
    await db.commit()

    await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                   external_user_id="u-re")
    assert fake.charges[1]["amount"] == 10000  # 정가


async def test_duplicate_subscription_conflicts(db, cipher, fake):
    """중복 구독은 ConflictError — 빌링키 발급(카드 등록) 자체는 이미 완료."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    # factories.create_card로 카드 등록 (빌링키 1회 발급)
    card = await create_card(db, fake, cipher, svc, external_user_id="u-dup")
    issued_before = len(fake.issued)  # 카드 등록 시 발급된 빌링키 수 기록
    await create_subscription(db, cipher, svc, plan,
                               external_user_id="u-dup", card_id=card.id)
    with pytest.raises(ConflictError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-dup")
    # 중복 구독 시도 시 추가 빌링키 발급 없음(create_subscription에서 발급하지 않음)
    assert len(fake.issued) == issued_before


async def test_no_registered_card_raises_not_found(db, cipher, fake):
    """등록된 카드가 없으면 NotFoundError — Task 7 핵심 시나리오."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000)
    # 카드 등록 없이 바로 구독 시도
    with pytest.raises(NotFoundError, match="등록된 카드가 없습니다"):
        await subs.create_subscription(
            db, fake, cipher, service=svc, plan_id=plan.id,
            external_user_id="u-no-card")
    # 토스 빌링키 발급이 일어나지 않아야 함
    assert fake.issued == []
    assert await db.scalar(select(Subscription)) is None


async def test_concurrent_create_only_one_wins(session_factory, cipher):
    """DB 부분 유니크 인덱스가 동시 요청을 차단한다."""
    async with session_factory() as setup_db:
        fake_setup = FakeTossClient()
        svc, _, _ = await create_service(setup_db, cipher, name=f"svc-cc-{uuid.uuid4().hex[:6]}")
        plan = await create_plan(setup_db, svc)
        # 카드 사전 등록(동시성 테스트 전 1회)
        await register_or_replace_card(
            setup_db, fake_setup, cipher,
            service=svc, external_user_id="u-race",
            customer_key="ck-race", auth_key="a")
        svc_id, plan_id = svc.id, plan.id
    fake = FakeTossClient()

    async def attempt(n: int) -> str:
        async with session_factory() as session:
            service = await session.get(Service, svc_id)
            try:
                await subs.create_subscription(
                    session, fake, cipher, service=service, plan_id=plan_id,
                    external_user_id="u-race")
                return "ok"
            except ConflictError:
                return "conflict"

    results = await asyncio.gather(attempt(1), attempt(2))
    assert sorted(results) == ["conflict", "ok"]
    # 카드 등록 시 빌링키 1개 발급됨(setup에서) — 구독 생성 시 추가 발급 없음
    assert len(fake.issued) == 0  # create_subscription에서 발급 없음


async def test_first_charge_failure_not_persisted_keeps_benefit(db, cipher, fake):
    """신규 가입 첫 결제 실패 시 구독·결제 테이블에 저장하지 않고 감사로그만 남긴다(요청).

    Task 7 변경: 카드는 영속적이므로 결제 실패 후에도 카드/빌링키는 보존된다.
    DONE 이력이 없으므로 재시도 시 첫구독 할인은 그대로 유지된다.
    """
    from app.models import AuditLog
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000,
                             first_payment_type="DISCOUNT_AMOUNT", first_payment_value=5000)
    await _register_card(db, fake, cipher, svc, "u-fail",
                         customer_key="ck-f1", auth_key="a")
    fake.charge_failure_queue = [TossError("EXCEED_MAX_AMOUNT", "한도 초과", 400)]
    with pytest.raises(PaymentFailedError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-fail")
    # 구독·결제 테이블에 흔적이 남지 않는다
    assert await db.scalar(select(Subscription)) is None
    assert await db.scalar(select(Payment)) is None
    # 카드는 보존 — 빌링키 삭제 없음(Task 7 변경)
    assert fake.deleted == []
    # 감사로그에는 실패가 기록된다
    audit = await db.scalar(select(AuditLog).where(
        AuditLog.action == "subscription.first_payment_failed"))
    assert audit is not None
    assert audit.detail["code"] == "EXCEED_MAX_AMOUNT"
    assert audit.detail["persisted"] is False
    assert audit.detail["billing_key_deleted"] is False  # 카드 보존 — 삭제 안 함

    # 재시도: DONE 이력이 없으므로 여전히 첫구독 할인 적용
    sub2 = await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                          external_user_id="u-fail")
    assert sub2.status == "ACTIVE"
    assert fake.charges[-1]["amount"] == 5000


async def test_timeout_with_actual_approval_resolves_done(db, cipher, fake):
    """타임아웃 후 재조회에서 승인 확인 시 DONE으로 확정."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000)
    await _register_card(db, fake, cipher, svc, "u-to",
                         customer_key="ck-to", auth_key="a")
    fake.succeed_despite_timeout = True
    fake.charge_failure_queue = [TossTimeoutError()]
    sub = await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                         external_user_id="u-to")
    assert sub.status == "ACTIVE"
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "DONE"  # 재조회로 승인 확인


async def test_timeout_without_approval_stays_unresolved(db, cipher, fake):
    """결과 불명은 '실패 확정'이 아니다 — PENDING 유지 + 슬롯 점유(이중결제 차단)."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000)
    await _register_card(db, fake, cipher, svc, "u-to2",
                         customer_key="ck-to2", auth_key="a")
    fake.charge_failure_queue = [TossTimeoutError()]
    with pytest.raises(PaymentFailedError) as exc:
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-to2")
    assert exc.value.code == "PAYMENT_UNRESOLVED"
    sub = await db.scalar(select(Subscription))
    assert sub.status == "ACTIVE"          # 슬롯 점유 유지
    assert sub.card_id is not None         # 카드 참조 유지
    payment = await db.scalar(select(Payment))
    assert payment.status == "PENDING"     # 확정 전 — 정산 스윕이 처리
    assert fake.deleted == []              # 카드/빌링키 미삭제
    # 외부 재시도는 409 → 이중 결제 불가
    with pytest.raises(ConflictError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-to2")


async def test_timeout_then_lookup_error_stays_unresolved(db, cipher, fake):
    """타임아웃 후 재조회마저 실패해도 결과 불명으로 보존된다."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000)
    await _register_card(db, fake, cipher, svc, "u-to3",
                         customer_key="ck-to3", auth_key="a")
    fake.charge_failure_queue = [TossTimeoutError()]
    fake.fail_lookup_with = TossError("NETWORK_ERROR", "재조회 실패", 0)
    with pytest.raises(PaymentFailedError) as exc:
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-to3")
    assert exc.value.code == "PAYMENT_UNRESOLVED"
    payment = await db.scalar(select(Payment))
    assert payment.status == "PENDING"  # FAILED로 붕괴되지 않음


async def test_charge_failure_card_preserved(db, cipher, fake):
    """확정 실패 시 카드/빌링키는 보존되고 구독·결제 행만 미저장.

    Task 7 변경: 구독 생성 실패 시 카드(빌링키)를 삭제하지 않는다.
    감사로그에 card_id와 billing_key_deleted=False가 기록된다.
    """
    from app.models import AuditLog
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000)
    card = await _register_card(db, fake, cipher, svc, "u-cf",
                                customer_key="ck-cf", auth_key="a")
    fake.charge_failure_queue = [TossError("EXCEED_MAX_AMOUNT", "한도 초과", 400)]
    with pytest.raises(PaymentFailedError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-cf")
    assert await db.scalar(select(Subscription)) is None  # 구독행 미저장
    assert await db.scalar(select(Payment)) is None        # 결제행 미저장
    audit = await db.scalar(select(AuditLog).where(
        AuditLog.action == "subscription.first_payment_failed"))
    assert audit.detail["billing_key_deleted"] is False    # 카드 보존
    assert audit.detail["card_id"] == str(card.id)        # 카드 추적용 ID 기록
    assert fake.deleted == []                              # 토스 빌링키 삭제 없음


async def test_plan_of_other_service_not_found(db, cipher, fake):
    """다른 서비스 요금제로 구독 시도 — NotFoundError."""
    svc_a, _, _ = await create_service(db, cipher, name="svc-cs-a")
    svc_b, _, _ = await create_service(db, cipher, name="svc-cs-b")
    plan_b = await create_plan(db, svc_b)
    # svc_a에 카드 등록 후 svc_b 요금제로 시도
    await _register_card(db, fake, cipher, svc_a, "u-x",
                         customer_key="ck-x", auth_key="a")
    with pytest.raises(NotFoundError):
        await subs.create_subscription(db, fake, cipher, service=svc_a, plan_id=plan_b.id,
                                       external_user_id="u-x")


async def test_archived_plan_not_subscribable(db, cipher, fake):
    """ARCHIVED 요금제는 구독 불가 — NotFoundError."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, status="ARCHIVED")
    await _register_card(db, fake, cipher, svc, "u-a",
                         customer_key="ck-a", auth_key="a")
    with pytest.raises(NotFoundError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-a")


async def test_create_subscription_records_actor_service_id(db, cipher, fake):
    """감사 로그에 actor_service_id가 기록된다."""
    from app.models import AuditLog
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    await _register_card(db, fake, cipher, svc, "u-actor-svc",
                         customer_key="ck-actor-svc", auth_key="a-1")
    await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-actor-svc")
    row = await db.scalar(select(AuditLog).where(
        AuditLog.action == "subscription.create"))
    assert row.actor_service_id == svc.id


# ─── auto_renew 테스트 ───────────────────────────────────────────────────────


async def test_subscription_no_auto_renew_sets_no_next_billing(db, cipher, fake):
    """auto_renew=False 요금제로 구독 시 next_billing_at=None이어야 한다."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, auto_renew=False)
    await _register_card(db, fake, cipher, svc, "nr-1",
                         customer_key="ck-nr-1", auth_key="a-nr")
    sub = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="nr-1")
    assert sub.next_billing_at is None           # 자동갱신 없음
    assert sub.status == "ACTIVE"                # 결제는 정상 진행


async def test_auto_renew_true_plan_has_next_billing(db, cipher, fake):
    """auto_renew=True(기본값) 요금제로 구독 시 next_billing_at이 설정된다."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, auto_renew=True)
    await _register_card(db, fake, cipher, svc, "nr-2",
                         customer_key="ck-nr-2", auth_key="a-nr2")
    sub = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="nr-2")
    assert sub.next_billing_at is not None        # 자동갱신 있음
    assert sub.next_billing_at == sub.current_period_end


async def test_trial_with_no_auto_renew_keeps_first_charge_schedule(db, cipher, fake):
    """체험+자동결제안함 공존(요청): 체험이면 첫 결제(체험 만료)가 예약돼야 하므로
    create 시 next_billing_at은 None이 아니라 체험 만료 시점이어야 한다.

    (그 첫 결제 성공 후 _advance_period가 next_billing=None으로 만료를 예약한다.)
    """
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, auto_renew=False, trial_enabled=True, trial_days=7)
    await _register_card(db, fake, cipher, svc, "nr-trial",
                         customer_key="ck-nrt", auth_key="a-nrt")
    sub = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="nr-trial", trial=True)
    assert sub.status == "TRIAL"
    assert sub.next_billing_at == sub.current_period_end   # 체험 만료 시 첫 결제 예약 유지(None 아님)
