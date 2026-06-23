"""정산 집계 통합 테스트 (요청 009)."""
from datetime import datetime, timezone

from app.models import Payment, PaymentKind, PaymentStatus
from app.services.settlement import settlement_summary
from tests.factories import create_plan, create_service, create_subscription

UTC = timezone.utc


async def _done(db, sub, amount, approved, *, order):
    db.add(Payment(subscription_id=sub.id, order_id=order, amount=amount,
                   payment_type="RENEWAL", status="DONE", idempotency_key=order,
                   requested_at=approved, approved_at=approved,
                   service_id=sub.service_id, external_user_id=sub.external_user_id))
    await db.commit()


async def _seed_two_services(db, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="정산A")
    svc_b, _, _ = await create_service(db, cipher, name="정산B")
    plan_a = await create_plan(db, svc_a)
    plan_b = await create_plan(db, svc_b)
    sub_a = await create_subscription(db, cipher, svc_a, plan_a, external_user_id="sa@e.com")
    sub_b = await create_subscription(db, cipher, svc_b, plan_b, external_user_id="sb@e.com")
    await _done(db, sub_a, 10000, datetime(2026, 5, 10, tzinfo=UTC), order="st-a1")
    await _done(db, sub_a, 20000, datetime(2026, 5, 20, tzinfo=UTC), order="st-a2")
    await _done(db, sub_b, 5000, datetime(2026, 5, 15, tzinfo=UTC), order="st-b1")
    # 기간 밖 + FAILED는 제외 검증용
    await _done(db, sub_b, 99999, datetime(2026, 6, 1, tzinfo=UTC), order="st-b-out")
    db.add(Payment(subscription_id=sub_b.id, order_id="st-b-fail", amount=7777,
                   payment_type="RENEWAL", status="FAILED", idempotency_key="st-b-fail",
                   requested_at=datetime(2026, 5, 16, tzinfo=UTC),
                   service_id=sub_b.service_id, external_user_id=sub_b.external_user_id))
    await db.commit()
    return svc_a, svc_b


async def test_summary_groups_by_service_amount_desc(db, cipher):
    svc_a, svc_b = await _seed_two_services(db, cipher)
    count, amount, rows = await settlement_summary(
        db, None, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    assert count == 3 and amount == 35000          # 기간 밖/FAILED 제외
    assert [r.service_name for r in rows] == ["정산A", "정산B"]  # 금액 내림차순
    assert rows[0].count == 2 and rows[0].amount == 30000
    assert rows[1].count == 1 and rows[1].amount == 5000


async def test_summary_boundary_half_open(db, cipher):
    """[start, end) 반개구간 — end 정각 결제는 제외."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="bd@e.com")
    await _done(db, sub, 1000, datetime(2026, 5, 1, tzinfo=UTC), order="bd-start")
    await _done(db, sub, 2000, datetime(2026, 6, 1, tzinfo=UTC), order="bd-end")
    count, amount, _ = await settlement_summary(
        db, None, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    assert count == 1 and amount == 1000


async def test_summary_scope_limits_services(db, cipher):
    svc_a, svc_b = await _seed_two_services(db, cipher)
    count, amount, rows = await settlement_summary(
        db, [svc_a.id], datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    assert count == 2 and amount == 30000
    assert [r.service_name for r in rows] == ["정산A"]


async def test_summary_open_range(db, cipher):
    """start/end None이면 해당 방향 무제한."""
    svc_a, _ = await _seed_two_services(db, cipher)
    count, amount, _ = await settlement_summary(db, None, None, None)
    assert amount == 35000 + 99999                  # FAILED만 제외


async def test_settlement_split_counts_and_plan_filter(db, cipher):
    from datetime import datetime, timezone
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    UTC = timezone.utc
    svc, _, _ = await create_service(db, cipher, name="정산011")
    plan = await create_plan(db, svc, name="정산플랜")
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u@e.com")
    when = datetime(2026, 5, 10, tzinfo=UTC)
    db.add(Payment(subscription_id=sub.id, service_id=svc.id, external_user_id="u@e.com",
                   order_id="s011-sub", amount=10000, payment_type=PaymentType.RENEWAL,
                   kind=PaymentKind.SUBSCRIPTION, status=PaymentStatus.DONE,
                   idempotency_key="s011-sub", requested_at=when, approved_at=when))
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="u2@e.com",
                   order_id="s011-oo", amount=3000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="s011-oo", requested_at=when, approved_at=when))
    await db.commit()
    start, end = datetime(2026,5,1,tzinfo=UTC), datetime(2026,6,1,tzinfo=UTC)
    count, amount, rows = await settlement_summary(db, None, start, end)
    row = next(r for r in rows if r.service_name == "정산011")
    assert row.sub_count == 1 and row.one_off_count == 1     # 분리 건수
    # 요금제 필터 → 구독결제만
    count2, amount2, rows2 = await settlement_summary(db, None, start, end, plan_name="정산플랜")
    assert amount2 == 10000 and count2 == 1                  # 일반결제 제외


async def test_settlement_reflects_canceled_refund(db, cipher):
    """취소된 단건 결제 — 총매출은 원금, 환불은 canceled_amount, 순매출은 보유 수수료."""
    UTC = timezone.utc
    svc, _, _ = await create_service(db, cipher, name="정산취소")
    when = datetime(2026, 5, 10, tzinfo=UTC)
    # 정상 단건 결제(취소 안 됨)
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="u1@e.com",
                   order_id="c-done", amount=10000, payment_type="ONE_OFF",
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="c-done", requested_at=when, approved_at=when))
    # 취소된 단건 결제 — 10% 수수료(환불 9000, 보유 1000)
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="u2@e.com",
                   order_id="c-cancel", amount=10000, payment_type="ONE_OFF",
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.CANCELED,
                   idempotency_key="c-cancel", requested_at=when, approved_at=when,
                   canceled_amount=9000, cancel_fee=1000, canceled_at=when))
    await db.commit()
    start, end = datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)
    count, amount, rows = await settlement_summary(db, [svc.id], start, end)
    row = rows[0]
    assert count == 2                      # 취소 건도 집계 대상에 포함
    assert row.amount == 20000             # 총매출 = 원금 합(10000 + 10000)
    assert row.refund_amount == 9000       # 환불 = 취소 결제의 canceled_amount
    assert row.net_amount == 11000         # 순매출 = 총매출 − 환불(= 정상 10000 + 보유 수수료 1000)


async def test_settlement_splits_subscription_and_one_off(db, cipher):
    from datetime import datetime, timezone
    from app.models import Payment, PaymentKind, PaymentStatus
    UTC = timezone.utc
    svc, _, _ = await create_service(db, cipher, name="정산분리")
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u@e.com")
    when = datetime(2026, 5, 10, tzinfo=UTC)
    db.add(Payment(subscription_id=sub.id, service_id=svc.id, external_user_id="u@e.com",
                   order_id="ss-sub", amount=10000, payment_type="RENEWAL",
                   kind=PaymentKind.SUBSCRIPTION, status=PaymentStatus.DONE,
                   idempotency_key="ss-sub", requested_at=when, approved_at=when))
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="u2@e.com",
                   order_id="ss-oo", amount=3000, payment_type="ONE_OFF",
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="ss-oo", requested_at=when, approved_at=when))
    await db.commit()
    count, amount, rows = await settlement_summary(
        db, None, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    assert count == 2 and amount == 13000          # 단건 포함
    row = next(r for r in rows if r.service_name == "정산분리")
    assert row.sub_amount == 10000 and row.one_off_amount == 3000
