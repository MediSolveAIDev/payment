"""대시보드 v2 집계 통합 테스트 (요청 010)."""
from datetime import datetime, timedelta, timezone

from app.core.clock import utcnow
from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
from app.services.audit import record_audit
from app.services.dashboard import build_dashboard
from tests.factories import create_plan, create_service, create_subscription

UTC = timezone.utc


async def _pay(db, *, svc, amount, status="DONE", kind=PaymentKind.SUBSCRIPTION,
               sub=None, order, approved=None, requested=None):
    now = requested or utcnow()
    db.add(Payment(
        subscription_id=(sub.id if sub else None), service_id=svc.id,
        external_user_id=(sub.external_user_id if sub else "oo@e.com"),
        order_id=order, amount=amount,
        payment_type=(PaymentType.RENEWAL if kind == PaymentKind.SUBSCRIPTION else PaymentType.ONE_OFF),
        kind=kind, status=status, idempotency_key=order,
        requested_at=now, approved_at=(approved or now if status == "DONE" else None)))
    await db.commit()


def _card(data, label):
    for c in data.revenue_cards:
        if c.label == label:
            return c
    raise AssertionError(f"카드 없음: {label}")


def _flow(data, label):
    """도넛 옆 흐름 지표(sub_flow) 값."""
    for it in data.sub_flow:
        if it["label"] == label:
            return it["value"]
    raise AssertionError(f"흐름 지표 없음: {label}")


def _status(data, ko_label):
    """도넛(status_breakdown)에서 한글 상태 라벨의 값. 없으면 0."""
    for row in data.status_breakdown:
        if row["label"] == ko_label:
            return row["value"]
    return 0


async def test_revenue_cards_total_sub_oneoff_refund(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u@e.com", status="ACTIVE")
    await _pay(db, svc=svc, sub=sub, amount=10000, kind=PaymentKind.SUBSCRIPTION, order="r-sub")
    await _pay(db, svc=svc, amount=4000, kind=PaymentKind.ONE_OFF, order="r-oo")
    await _pay(db, svc=svc, sub=sub, amount=3000, status="CANCELED", order="r-refund")  # 환불
    data = await build_dashboard(db, None)
    assert _card(data, "총매출").value == "14,000원"      # 구독10k+일반4k (CANCELED 제외)
    assert _card(data, "구독매출").value == "10,000원"
    assert _card(data, "일반매출").value == "4,000원"
    assert _card(data, "환불금액").value == "3,000원"      # CANCELED 합


async def test_canceled_oneoff_fee_counts_as_revenue(db, cipher):
    """일반결제를 취소(부분환불)하면 보유한 취소 수수료가 매출(총/일반)에 잡힌다."""
    svc, _, _ = await create_service(db, cipher)
    now = utcnow()
    # 정상 일반결제 10,000원
    await _pay(db, svc=svc, amount=10000, kind=PaymentKind.ONE_OFF, order="oo-done")
    # 취소된 일반결제 10,000원 — 환불 9,000원 / 수수료 1,000원 (승인 후 취소)
    db.add(Payment(
        service_id=svc.id, external_user_id="oo@e.com", order_id="oo-cancel",
        amount=10000, payment_type=PaymentType.ONE_OFF, kind=PaymentKind.ONE_OFF,
        status="CANCELED", idempotency_key="oo-cancel",
        requested_at=now, approved_at=now,
        canceled_amount=9000, cancel_fee=1000, canceled_at=now))
    await db.commit()
    data = await build_dashboard(db, None)
    # 일반매출 = 10,000(DONE) + 1,000(취소 수수료) = 11,000
    assert _card(data, "일반매출").value == "11,000원"
    assert _card(data, "총매출").value == "11,000원"
    # 환불금액 = 9,000(보유 수수료 제외한 실제 환불액)
    assert _card(data, "환불금액").value == "9,000원"


async def test_sub_cards_counts_and_expired_from_audit(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    s1 = await create_subscription(db, cipher, svc, plan, external_user_id="c1@e.com")
    s2 = await create_subscription(db, cipher, svc, plan, external_user_id="c2@e.com")
    s3 = await create_subscription(db, cipher, svc, plan, external_user_id="e1@e.com")
    await create_subscription(db, cipher, svc, plan, external_user_id="t1@e.com", status="TRIAL")
    await record_audit(db, actor_type="SERVICE", action="subscription.cancel",
                       target_type="subscription", target_id=str(s1.id))
    await record_audit(db, actor_type="SYSTEM", action="subscription.suspended",
                       target_type="subscription", target_id=str(s2.id))
    await record_audit(db, actor_type="SYSTEM", action="subscription.expired",
                       target_type="subscription", target_id=str(s3.id))
    await db.commit()
    data = await build_dashboard(db, None)
    assert _flow(data, "구독 취소") == 2                   # 사용자취소1 + 결제만료1 (흐름)
    assert _flow(data, "구독 만료") == 1                   # 감사 subscription.expired (흐름)
    assert _status(data, "체험") == 1                      # TRIAL → 도넛 상태
    # 도넛 합계 = 전체 상태(ACTIVE 3 + TRIAL 1, 만료 없음)
    assert sum(r["value"] for r in data.status_breakdown) == 4


async def test_status_donut_includes_expired(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="a1@e.com", status="ACTIVE")
    await create_subscription(db, cipher, svc, plan, external_user_id="x1@e.com", status="EXPIRED")
    data = await build_dashboard(db, None)
    labels = {row["label"]: row["value"] for row in data.status_breakdown}
    assert labels.get("활성") == 1
    assert labels.get("만료") == 1           # 만료가 도넛에 포함
    assert sum(data.status_breakdown[i]["value"] for i in range(len(data.status_breakdown))) == 2


async def test_twelve_month_series_subs_and_one_off(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u@e.com", status="ACTIVE")
    await _pay(db, svc=svc, amount=4000, kind=PaymentKind.ONE_OFF, order="m-oo")
    data = await build_dashboard(db, None)
    assert len(data.subs_months) == 12
    assert data.subs_months[-1]["total"] == 1      # 전체구독수(이번달)
    assert data.subs_months[-1]["new"] == 1       # 신규구독수(이번달)
    assert len(data.one_off_months) == 12
    assert data.one_off_months[-1]["value"] == 4000


async def test_daily_trend_30_days(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="d1@e.com", status="ACTIVE")
    data = await build_dashboard(db, None)
    assert len(data.daily_trend) == 30
    last = data.daily_trend[-1]
    assert set(last) >= {"label", "total", "new", "canceled", "expired"}
    assert last["total"] == 1 and last["new"] == 1


async def test_sub_cards_cancel_scoped_to_service(db, cipher):
    """취소 카운트는 target 구독의 서비스로 스코프 제한."""
    svc_a, _, _ = await create_service(db, cipher, name="스코프A")
    svc_b, _, _ = await create_service(db, cipher, name="스코프B")
    plan_a = await create_plan(db, svc_a)
    plan_b = await create_plan(db, svc_b)
    sa = await create_subscription(db, cipher, svc_a, plan_a, external_user_id="sa@e.com")
    sb = await create_subscription(db, cipher, svc_b, plan_b, external_user_id="sb@e.com")
    await record_audit(db, actor_type="SERVICE", action="subscription.cancel",
                       target_type="subscription", target_id=str(sa.id))
    await record_audit(db, actor_type="SERVICE", action="subscription.cancel",
                       target_type="subscription", target_id=str(sb.id))
    await db.commit()
    # svc_a 매니저 스코프 → 자기 서비스 취소 1건만 (도넛 옆 흐름 지표)
    scoped = await build_dashboard(db, [svc_a.id])
    assert _flow(scoped, "구독 취소") == 1
    # 전체(admin) → 2건
    allv = await build_dashboard(db, None)
    assert _flow(allv, "구독 취소") == 2


async def test_series_buckets_multi_period(db, cipher):
    """멀티-기간 버킷 검증 — 재작성 전/후 동일 결과 보장."""
    from dateutil.relativedelta import relativedelta as rd

    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 이번달 신규 2개
    await create_subscription(db, cipher, svc, plan, external_user_id="cur1@e.com", status="ACTIVE")
    await create_subscription(db, cipher, svc, plan, external_user_id="cur2@e.com", status="ACTIVE")

    # 지난달 중간 시점 신규 1개 — created_at을 지난달 15일로 조작
    last_month_15 = month_start - rd(months=1) + timedelta(days=14)
    old = await create_subscription(db, cipher, svc, plan, external_user_id="old1@e.com", status="ACTIVE")
    old.created_at = last_month_15
    await db.commit()

    data = await build_dashboard(db, None)

    # 12개월 시리즈 길이 보장
    assert len(data.subs_months) == 12
    assert len(data.one_off_months) == 12

    # 이번달([-1]) 신규 = 2 (cur1, cur2)
    assert data.subs_months[-1]["new"] == 2, f"이번달 신규 expected 2, got {data.subs_months[-1]['new']}"
    # 전체 스냅샷(이번달 말/now) = 3 (cur1, cur2, old1 모두 ACTIVE)
    assert data.subs_months[-1]["total"] == 3, f"이번달 전체 expected 3, got {data.subs_months[-1]['total']}"
    # 지난달([-2]) 신규 = 1 (old1만 지난달 15일에 생성)
    assert data.subs_months[-2]["new"] == 1, f"지난달 신규 expected 1, got {data.subs_months[-2]['new']}"

    # 30일 트렌드 길이 보장
    assert len(data.daily_trend) == 30
    # 오늘([-1]) 신규 = 2 (cur1, cur2; old1은 지난달 15일 생성이라 오늘 버킷에는 없음)
    assert data.daily_trend[-1]["new"] == 2, f"오늘 신규 expected 2, got {data.daily_trend[-1]['new']}"


async def test_service_revenue_and_subs_admin_only(db, cipher):
    svc, _, _ = await create_service(db, cipher, name="서비스X")
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u@e.com", status="ACTIVE")
    await _pay(db, svc=svc, sub=sub, amount=10000, kind=PaymentKind.SUBSCRIPTION, order="sv-sub")
    await _pay(db, svc=svc, amount=2000, kind=PaymentKind.ONE_OFF, order="sv-oo")
    data = await build_dashboard(db, None)
    rev = next(r for r in data.service_revenue if r["name"] == "서비스X")
    assert rev["total"] == 12000 and rev["sub"] == 10000 and rev["one_off"] == 2000
    subs = next(r for r in data.service_subs if r["name"] == "서비스X")
    assert subs["open"] == 1 and subs["new"] == 1 and subs["revenue"] == 10000
    # 매니저 스코프: 서비스별 표 없음
    scoped = await build_dashboard(db, [svc.id])
    assert scoped.service_revenue == [] and scoped.service_subs == []
    assert _card(scoped, "구독매출").value == "10,000원"   # 카드는 스코프 집계
