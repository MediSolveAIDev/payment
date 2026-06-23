"""단건(일반) 결제 통합 테스트.

Task 9 변경: 단건결제가 카드 보관함 기반으로 전환됨.
- _pay() 헬퍼에서 auth_key/customer_key 제거.
- 각 테스트에서 create_card()로 카드를 먼저 등록한 뒤 결제를 호출한다.
- 카드 미등록 시 NotFoundError 검증 테스트 추가.
- 단건결제 성공/실패/타임아웃 후 카드가 삭제되지 않음(영속) 검증 추가.
"""
import pytest
from sqlalchemy import select

from app.core.errors import NotFoundError, PaymentFailedError
from app.models import Payment, PaymentKind, PaymentStatus
from app.notifications.email import RecordingEmailSender
from app.services import payments as payment_service
from app.services.cards import get_card
from app.toss.errors import TossError, TossTimeoutError
from app.toss.fake import FakeTossClient
from tests.factories import create_card, create_service


@pytest.fixture
def fake():
    return FakeTossClient()


@pytest.fixture
def email():
    return RecordingEmailSender()


async def _pay(db, fake, cipher, svc, *, order_id="oo-001", amount=5000, user="u-1"):
    """단건결제 헬퍼 — Task 9: auth_key/customer_key 없음, 카드 보관함 사용."""
    return await payment_service.create_one_off_payment(
        db, fake, cipher, service=svc, external_user_id=user,
        order_id=order_id, order_name="단건상품", amount=amount)


async def test_one_off_requires_card(db, cipher, fake):
    """카드 미등록 상태에서 단건결제 시도 → NotFoundError.

    Task 9 신규: 카드 없이 결제하면 즉시 오류를 반환해야 한다.
    """
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(NotFoundError, match="등록된 카드가 없습니다"):
        await _pay(db, fake, cipher, svc)


async def test_one_off_success_card_persists(db, cipher, fake):
    """단건결제 성공 후 카드가 삭제되지 않는다(영속).

    Task 9 신규: 카드는 영속(persistent)이므로 단건결제 성공 후에도 get_card가 카드를 반환해야 한다.
    """
    svc, _, _ = await create_service(db, cipher)
    # 카드 먼저 등록
    await create_card(db, fake, cipher, svc, external_user_id="u-1")
    p = await _pay(db, fake, cipher, svc)
    assert p.status == PaymentStatus.DONE and p.kind == PaymentKind.ONE_OFF
    assert p.service_id == svc.id and p.subscription_id is None
    assert p.amount == 5000 and p.external_user_id == "u-1"
    # Task 9: 카드 영속 검증 — 단건결제 후에도 카드가 남아 있어야 한다
    card_after = await get_card(db, service_id=svc.id, external_user_id="u-1")
    assert card_after is not None, "단건결제 성공 후 카드가 삭제되면 안 됨(영속)"
    # Task 9: 빌링키 삭제 없음 — FakeTossClient.deleted는 False여야 한다
    assert not fake.deleted, "단건결제 성공 후 빌링키(카드) 삭제 호출되면 안 됨(영속 카드)"


async def test_one_off_idempotent_same_order_id(db, cipher, fake):
    """같은 order_id 재시도는 기존 Payment를 반환(재결제 없음)."""
    svc, _, _ = await create_service(db, cipher)
    await create_card(db, fake, cipher, svc, external_user_id="u-1")
    p1 = await _pay(db, fake, cipher, svc, order_id="oo-dup")
    n = len(fake.charges)
    p2 = await _pay(db, fake, cipher, svc, order_id="oo-dup")
    assert p2.id == p1.id and len(fake.charges) == n   # 재결제 없음


async def test_one_off_same_order_id_isolated_per_service(db, cipher, fake):
    """order_id는 서비스(테넌트) 스코프 — 타 서비스가 같은 주문번호를 써도 충돌하지 않는다.

    감사 Phase 2(보안 M-1): 과거에는 전역 유니크라 서비스 A가 B의 주문번호를
    선점(스쿼팅)해 B의 결제를 차단할 수 있었다. 이제 각자 독립 결제가 생성되고,
    토스에는 서로 다른 전역 고유 toss_order_id가 전달된다.
    """
    svc_a, _, _ = await create_service(db, cipher)
    svc_b, _, _ = await create_service(db, cipher)
    # 각 서비스별로 카드를 먼저 등록
    await create_card(db, fake, cipher, svc_a, external_user_id="u-1")
    await create_card(db, fake, cipher, svc_b, external_user_id="u-1")
    pay_a = await _pay(db, fake, cipher, svc_a, order_id="oo-xxx")
    pay_b = await _pay(db, fake, cipher, svc_b, order_id="oo-xxx")  # 충돌 없음
    assert pay_a.id != pay_b.id
    assert pay_a.service_id == svc_a.id and pay_b.service_id == svc_b.id
    # 토스 측 식별자는 전역 고유 — 같은 order_id라도 서로 달라야 한다
    assert pay_a.toss_order_id != pay_b.toss_order_id


async def test_one_off_card_declined_failed(db, cipher, fake):
    """카드 거절 시 Payment.status = FAILED, 카드는 영속 유지."""
    svc, _, _ = await create_service(db, cipher)
    await create_card(db, fake, cipher, svc, external_user_id="u-1")
    fake.fail_charge_with = TossError("REJECT_CARD", "카드 거절")
    with pytest.raises(PaymentFailedError):
        await _pay(db, fake, cipher, svc, order_id="oo-fail")
    row = await db.scalar(select(Payment).where(Payment.order_id == "oo-fail"))
    assert row.status == PaymentStatus.FAILED
    # Task 9: 카드 영속 — 결제 실패 후에도 카드가 남아 있어야 한다
    card_after = await get_card(db, service_id=svc.id, external_user_id="u-1")
    assert card_after is not None, "결제 실패 후에도 카드가 삭제되면 안 됨(영속)"


async def test_one_off_max_amount_runtime_configurable(db, cipher, fake):
    """단건결제 상한이 런타임(GlobalSettings)에서 즉시 조정됨 — 사고 시 즉시 조이기.

    전체 설정에서 상한을 1,000원으로 낮추면, 1,001원은 토스 호출 전에 거부되고
    1,000원은 정상 처리된다. (.env가 아닌 DB 전역설정이 런타임 권위)
    """
    from app.core.errors import InputValidationError
    from app.services.app_settings import update_security_policy
    from tests.factories import create_user
    svc, _, _ = await create_service(db, cipher)
    await create_card(db, fake, cipher, svc, external_user_id="u-1")
    admin, _ = await create_user(db, role="SYSTEM_ADMIN")
    await update_security_policy(db, max_failed_logins=5, account_lock_minutes=15,
                                 one_off_max_amount=1000, actor_user_id=admin.id)
    with pytest.raises(InputValidationError):
        await _pay(db, fake, cipher, svc, order_id="oo-over", amount=1001)
    ok = await _pay(db, fake, cipher, svc, order_id="oo-okay", amount=1000)
    assert ok.status == PaymentStatus.DONE


async def test_one_off_timeout_pending(db, cipher, fake):
    """타임아웃 시 PENDING 유지, 카드 삭제 없음(영속)."""
    svc, _, _ = await create_service(db, cipher)
    await create_card(db, fake, cipher, svc, external_user_id="u-1")
    fake.fail_charge_with = TossTimeoutError()
    with pytest.raises(PaymentFailedError):
        await _pay(db, fake, cipher, svc, order_id="oo-tout")
    row = await db.scalar(select(Payment).where(Payment.order_id == "oo-tout"))
    assert row.status == PaymentStatus.PENDING
    # Task 9: 카드 영속 — 타임아웃 후에도 카드가 남아 있어야 한다
    assert not fake.deleted, "타임아웃 후 카드(빌링키) 삭제 호출되면 안 됨(영속 카드)"
    card_after = await get_card(db, service_id=svc.id, external_user_id="u-1")
    assert card_after is not None, "타임아웃 후에도 카드가 삭제되면 안 됨(영속)"


async def test_reconcile_confirms_one_off(db, session_factory, redis_client, cipher, fake, email):
    """PENDING 단건결제를 정산 스윕이 DONE으로 확정한다."""
    from datetime import timedelta
    from app.core.clock import utcnow
    from app.services.renewals import process_due
    from app.toss.errors import TossTimeoutError
    from app.toss.fake import FakeTossClient
    from app.toss.provider import TossClientProvider  # T7: process_due는 TossClientProvider를 받음
    svc, _, _ = await create_service(db, cipher)
    await create_card(db, fake, cipher, svc, external_user_id="u-1")
    fake.fail_charge_with = TossTimeoutError()                  # 타임아웃 → PENDING
    with pytest.raises(PaymentFailedError):
        await _pay(db, fake, cipher, svc, order_id="oo-recon")
    row = await db.scalar(select(Payment).where(Payment.order_id == "oo-recon"))
    assert row.status == PaymentStatus.PENDING
    # 토스에는 실제로 DONE으로 존재(재조회 시 확정되도록 주입).
    # 정산 스윕은 전역 고유 toss_order_id로 토스를 조회한다(보안 M-1) —
    # 단건 결제의 토스 측 식별자는 서버가 생성한 값이므로 그 키로 주입한다.
    fake.fail_charge_with = None
    fake.payments_by_order[row.toss_order_id] = FakeTossClient._result_for(
        row.toss_order_id, 5000)
    # 유예(10분) 경과한 것처럼 requested_at 과거로
    row.requested_at = utcnow() - timedelta(minutes=15)
    await db.commit()
    # 정산 스윕 실행
    # T7: process_due는 TossClientProvider를 요구 — fake를 override로 주입
    provider = TossClientProvider(cipher, "http://fake", override_client=fake)
    await process_due(session_factory, redis_client, provider, cipher, email, now=utcnow())
    db.expire_all()
    row = await db.scalar(select(Payment).where(Payment.order_id == "oo-recon"))
    assert row.status == PaymentStatus.DONE
