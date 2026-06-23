"""단건 결제 취소 통합 테스트 (요청 012)."""
import uuid

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.errors import ConflictError, InputValidationError, NotFoundError, PaymentFailedError
from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
from app.services import payments as payment_service
from app.services.settlement import settlement_summary
from app.toss.errors import TossError
from app.toss.fake import FakeTossClient
from tests.factories import create_service


@pytest.fixture
def fake():
    return FakeTossClient()


async def _done_oneoff(db, svc, *, order="oc-1", amount=10000):
    """DONE 상태의 ONE_OFF 결제 시드 헬퍼. requested_at은 현재 시각으로 채운다."""
    p = Payment(subscription_id=None, service_id=svc.id, external_user_id="u@e.com",
                order_id=order, amount=amount, payment_type=PaymentType.ONE_OFF,
                kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                idempotency_key=order, toss_payment_key=f"pay_{order}",
                requested_at=utcnow())
    db.add(p); await db.commit(); await db.refresh(p)
    return p


async def test_cancel_full_refund_no_fee(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)   # fee_percent 기본 0
    p = await _done_oneoff(db, svc, amount=10000)
    out = await payment_service.cancel_one_off_payment(
        db, fake, service=svc, order_id="oc-1", reason="테스트")
    assert out.status == PaymentStatus.CANCELED
    assert out.canceled_amount == 10000 and out.cancel_fee == 0
    assert fake.canceled and fake.canceled[0]["cancel_amount"] is None   # 전액취소


async def test_cancel_partial_with_fee(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    svc.cancellation_fee_percent = 10; await db.commit()
    p = await _done_oneoff(db, svc, order="oc-2", amount=10000)
    out = await payment_service.cancel_one_off_payment(
        db, fake, service=svc, order_id="oc-2", reason="테스트")
    assert out.cancel_fee == 1000 and out.canceled_amount == 9000
    assert fake.canceled[0]["cancel_amount"] == 9000                     # 부분취소


async def test_cancel_disabled(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    svc.cancellation_enabled = False; await db.commit()
    await _done_oneoff(db, svc, order="oc-3")
    with pytest.raises(PaymentFailedError):
        await payment_service.cancel_one_off_payment(
            db, fake, service=svc, order_id="oc-3", reason="x")


async def test_cancel_rejects_non_done_or_other_service(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    other, _, _ = await create_service(db, cipher, name="타서비스")
    p = await _done_oneoff(db, svc, order="oc-4")
    with pytest.raises(NotFoundError):
        await payment_service.cancel_one_off_payment(
            db, fake, service=other, order_id="oc-4", reason="x")


async def test_cancel_toss_error_keeps_done(db, cipher, fake):
    """토스 취소 API 실패 시: PaymentFailedError 발생, 상태 DONE 유지, 감사 기록."""
    svc, _, _ = await create_service(db, cipher)
    p = await _done_oneoff(db, svc, order="oc-err")
    fake.fail_cancel_with = TossError("CANCEL_REJECT", "취소 거절", 400)
    with pytest.raises(PaymentFailedError):
        await payment_service.cancel_one_off_payment(
            db, fake, service=svc, order_id="oc-err", reason="x")
    row = await db.scalar(select(Payment).where(Payment.order_id == "oc-err"))
    assert row.status == PaymentStatus.DONE   # 토스 취소 실패 시 상태 유지
    # 감사 로그에 payment.cancel_failed 기록 여부 확인
    from sqlalchemy import select as sa_select
    from app.models import AuditLog
    audit = await db.scalar(
        sa_select(AuditLog).where(
            AuditLog.action == "payment.cancel_failed",
            AuditLog.target_id == str(p.id)))
    assert audit is not None, "payment.cancel_failed 감사 로그가 기록되어야 한다"


async def test_cancel_rejects_non_done(db, cipher, fake):
    """FAILED 상태 결제에 대한 취소 시도는 ConflictError."""
    svc, _, _ = await create_service(db, cipher)
    p = await _done_oneoff(db, svc, order="oc-fail")
    p.status = PaymentStatus.FAILED
    await db.commit()
    with pytest.raises(ConflictError):
        await payment_service.cancel_one_off_payment(
            db, fake, service=svc, order_id="oc-fail", reason="x")


async def test_cancel_full_fee_no_refund(db, cipher, fake):
    """100% 수수료(환불 0) 케이스: 토스 취소 호출 생략, status=CANCELED, canceled_amount=0, cancel_fee=amount."""
    svc, _, _ = await create_service(db, cipher)
    svc.cancellation_fee_percent = 100
    await db.commit()
    p = await _done_oneoff(db, svc, order="oc-100", amount=10000)
    out = await payment_service.cancel_one_off_payment(
        db, fake, service=svc, order_id="oc-100", reason="테스트")
    assert out.status == PaymentStatus.CANCELED
    assert out.canceled_amount == 0 and out.cancel_fee == 10000
    assert fake.canceled == []   # 환불 0 → 토스 취소 호출 생략


# ── 어드민(관리자) 취소 — 수수료 없이 전액/부분(누적) ──────────────────────────

async def test_admin_cancel_full_ignores_fee(db, cipher, fake):
    """어드민 전액 취소 — 서비스 수수료율이 있어도 무시하고 전액 환불."""
    svc, _, _ = await create_service(db, cipher)
    svc.cancellation_fee_percent = 50; await db.commit()   # 수수료 있어도
    p = await _done_oneoff(db, svc, order="ac-1", amount=10000)
    out = await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=None, reason="관리자", actor_user_id=uuid.uuid4())
    assert out.status == PaymentStatus.CANCELED
    assert out.canceled_amount == 10000          # 전액 환불(수수료 미적용)
    assert not out.cancel_fee                     # 어드민 무수수료
    assert fake.canceled[0]["cancel_amount"] is None   # 최초 전액 → cancelAmount 생략


async def test_admin_cancel_partial_keeps_done(db, cipher, fake):
    """어드민 부분 취소 — status는 DONE 유지, canceled_amount 누적, 토스 부분취소."""
    svc, _, _ = await create_service(db, cipher)
    p = await _done_oneoff(db, svc, order="ac-2", amount=10000)
    out = await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=3000, reason="관리자", actor_user_id=uuid.uuid4())
    assert out.status == PaymentStatus.DONE       # 일부만 환불 → DONE 유지
    assert out.canceled_amount == 3000
    assert fake.canceled[0]["cancel_amount"] == 3000


async def test_admin_cancel_cumulative_reaches_full(db, cipher, fake):
    """부분 취소를 누적해 전액에 도달하면 CANCELED로 전환."""
    svc, _, _ = await create_service(db, cipher)
    p = await _done_oneoff(db, svc, order="ac-3", amount=10000)
    aid = uuid.uuid4()
    await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=3000, reason="1차", actor_user_id=aid)
    out = await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=7000, reason="2차", actor_user_id=aid)
    assert out.canceled_amount == 10000 and out.status == PaymentStatus.CANCELED
    assert [c["cancel_amount"] for c in fake.canceled] == [3000, 7000]   # 두 번 부분취소


async def test_admin_cancel_over_remaining_rejected(db, cipher, fake):
    """잔여 환불가능액을 초과하는 취소 금액은 InputValidationError."""
    svc, _, _ = await create_service(db, cipher)
    p = await _done_oneoff(db, svc, order="ac-4", amount=10000)
    await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=6000, reason="1차", actor_user_id=uuid.uuid4())
    with pytest.raises(InputValidationError):
        await payment_service.admin_cancel_one_off_payment(   # 잔여 4000 < 5000
            db, fake, payment=p, cancel_amount=5000, reason="2차", actor_user_id=uuid.uuid4())


async def test_admin_cancel_ignores_disabled_gate(db, cipher, fake):
    """cancellation_enabled=False여도 어드민 취소는 항상 허용."""
    svc, _, _ = await create_service(db, cipher)
    svc.cancellation_enabled = False; await db.commit()
    p = await _done_oneoff(db, svc, order="ac-5", amount=10000)
    out = await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=None, reason="관리자", actor_user_id=uuid.uuid4())
    assert out.status == PaymentStatus.CANCELED and out.canceled_amount == 10000


async def test_admin_cancel_toss_error_preserves(db, cipher, fake):
    """토스 실패 시 상태·누적액 보존 + cancel_failed 감사."""
    from app.models import AuditLog
    svc, _, _ = await create_service(db, cipher)
    p = await _done_oneoff(db, svc, order="ac-6", amount=10000)
    fake.fail_cancel_with = TossError("CANCEL_REJECT", "취소 거절", 400)
    with pytest.raises(PaymentFailedError):
        await payment_service.admin_cancel_one_off_payment(
            db, fake, payment=p, cancel_amount=3000, reason="관리자", actor_user_id=uuid.uuid4())
    row = await db.scalar(select(Payment).where(Payment.order_id == "ac-6"))
    assert row.status == PaymentStatus.DONE and not row.canceled_amount
    assert await db.scalar(select(AuditLog).where(
        AuditLog.action == "payment.cancel_failed", AuditLog.target_id == str(p.id))) is not None


async def test_external_cancel_blocked_after_admin_partial(db, cipher, fake):
    """어드민 부분취소된 결제는 외부(사용자) 전액취소가 차단된다(이중환불 방지)."""
    svc, _, _ = await create_service(db, cipher)
    p = await _done_oneoff(db, svc, order="ac-7", amount=10000)
    await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=3000, reason="부분", actor_user_id=uuid.uuid4())
    with pytest.raises(ConflictError):
        await payment_service.cancel_one_off_payment(
            db, fake, service=svc, order_id="ac-7", reason="외부")


async def test_partial_cancel_exposed_in_api_response(db, cipher, fake):
    """부분취소(status=DONE) 후 외부 API 응답에 실제 환불액·실수령액이 노출된다.

    회귀 방지: 어드민 부분취소는 status=DONE을 유지하므로, status==CANCELED로만 환불을
    판정하면 cancel_refund_amount가 0이 되어 샘플서비스에 취소금액이 반영되지 않았다.
    """
    from app.schemas.api import PaymentResponse
    svc, _, _ = await create_service(db, cipher)
    p = await _done_oneoff(db, svc, order="ac-api", amount=10000)
    await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=3000, reason="부분", actor_user_id=uuid.uuid4())
    await db.refresh(p)
    resp = PaymentResponse.from_model(p, svc)
    assert resp.status == "DONE"              # 부분취소는 DONE 유지
    assert resp.canceled_amount == 3000       # 실제 누적 환불액
    assert resp.cancel_refund_amount == 3000  # 환불액(샘플서비스 표시용)
    assert resp.net_amount == 7000            # 실수령 = 10000 − 3000
    assert resp.cancelable is False           # 이미 부분취소 → 외부 재취소 불가


async def test_partial_cancel_reflected_in_settlement(db, cipher, fake):
    """부분취소 후 정산: 총매출=원금, 환불=부분환불액, 순매출=원금−환불."""
    svc, _, _ = await create_service(db, cipher)
    p = await _done_oneoff(db, svc, order="ac-8", amount=10000)
    p.approved_at = utcnow(); await db.commit()
    await payment_service.admin_cancel_one_off_payment(
        db, fake, payment=p, cancel_amount=3000, reason="부분", actor_user_id=uuid.uuid4())
    _, _, rows = await settlement_summary(db, None, None, None)
    row = next(r for r in rows if r.service_id == svc.id)
    assert row.amount == 10000 and row.refund_amount == 3000 and row.net_amount == 7000
