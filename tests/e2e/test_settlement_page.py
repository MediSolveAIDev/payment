"""정산 화면 e2e (요청 009)."""
from datetime import datetime, timezone

from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login

UTC = timezone.utc


async def _seed(db, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="정산서비스A")
    svc_b, _, _ = await create_service(db, cipher, name="정산서비스B")
    plan_a = await create_plan(db, svc_a)
    plan_b = await create_plan(db, svc_b)
    sub_a = await create_subscription(db, cipher, svc_a, plan_a, external_user_id="se-a")
    sub_b = await create_subscription(db, cipher, svc_b, plan_b, external_user_id="se-b")
    when = datetime(2026, 5, 10, tzinfo=UTC)
    db.add(Payment(subscription_id=sub_a.id, order_id="se-pay-a", amount=10000,
                   payment_type="RENEWAL", status="DONE", idempotency_key="se-pay-a",
                   requested_at=when, approved_at=when,
                   service_id=sub_a.service_id, external_user_id=sub_a.external_user_id))
    db.add(Payment(subscription_id=sub_b.id, order_id="se-pay-b", amount=5000,
                   payment_type="RENEWAL", status="DONE", idempotency_key="se-pay-b",
                   requested_at=when, approved_at=when,
                   service_id=sub_b.service_id, external_user_id=sub_b.external_user_id))
    # 단건 결제 (서비스A에 추가)
    db.add(Payment(subscription_id=None, order_id="se-pay-oo", amount=2000,
                   payment_type="ONE_OFF", status="DONE", idempotency_key="se-pay-oo",
                   requested_at=when, approved_at=when, kind=PaymentKind.ONE_OFF,
                   service_id=svc_a.id, external_user_id="se-oo"))
    await db.commit()
    return svc_a, svc_b, sub_a


async def test_settlement_all_mode_lists_services(client, db, redis_client, cipher):
    svc_a, svc_b, _ = await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get(
        "/admin/settlement?from=2026-05-01&to=2026-05-31")).text
    assert "17,000" in html                        # 전체 합계 (구독 15,000 + 단건 2,000)
    assert "정산서비스A" in html and "정산서비스B" in html
    # 상세보기 링크가 기간을 유지한 채 service_id를 채움
    assert f"service_id={svc_a.id}" in html
    assert "from=2026-05-01" in html and "to=2026-05-31" in html
    assert "승인일" in html                         # 기준 안내 문구


async def test_settlement_service_mode_lists_payments(client, db, redis_client, cipher):
    svc_a, _, sub_a = await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get(
        f"/admin/settlement?from=2026-05-01&to=2026-05-31&service_id={svc_a.id}")).text
    assert "12,000" in html                        # 해당 서비스 합계(구독 10,000 + 단건 2,000)
    assert "se-pay-a" in html                      # 구독 결제 행
    assert "se-pay-oo" in html                     # 단건 결제도 서비스 건별 목록에 포함
    assert "se-pay-b" not in html                  # 타 서비스 제외
    assert f'href="/admin/subscriptions/{sub_a.id}"' in html  # 상세보기 → 구독 상세


async def test_settlement_default_period_renders(client, db, redis_client, cipher):
    """파라미터 없으면 이번달 1일~오늘 기본값으로 렌더."""
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    resp = await client.get("/admin/settlement")
    assert resp.status_code == 200
    from app.core.clock import utcnow
    assert utcnow().strftime("%Y-%m-01") in resp.text   # from 기본값 렌더


async def test_settlement_manager_scope(client, db, redis_client, cipher):
    svc_a, svc_b, _ = await _seed(db, cipher)
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc_a.id)
    await admin_login(client, mgr.email, pw)
    html = (await client.get(
        "/admin/settlement?from=2026-05-01&to=2026-05-31")).text
    assert "정산서비스A" in html and "정산서비스B" not in html
    assert "12,000" in html                                # 스코프(svc_a) 합계 = 구독 10,000 + 단건 2,000
    assert "15,000" not in html                            # 타 서비스 금액 제외 (svc_b)
    # 타 서비스 service_id 직접 요청 → 404
    resp = await client.get(f"/admin/settlement?service_id={svc_b.id}")
    assert resp.status_code == 404


async def test_settlement_nav_menu(client, db, redis_client, cipher):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    assert '/admin/settlement' in html and "정산" in html


async def test_settlement_month_picker_renders(client, db, redis_client, cipher):
    """월 선택 input — 선택한 달의 from(YYYY-MM)으로 프리필되고 JS가 from/to를 세팅."""
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin/settlement?from=2026-05-01&to=2026-05-31")).text
    assert 'type="month"' in html
    assert 'value="2026-05"' in html        # from_filter(2026-05-01)에서 월 프리필
    assert "data-settle-month" in html      # JS 훅


async def test_settlement_shows_split_and_oneoff_detail(client, db, redis_client, cipher):
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc, _, _ = await create_service(db, cipher, name="정산상세S")
    when = datetime(2026, 5, 10, tzinfo=UTC)
    p = Payment(subscription_id=None, service_id=svc.id, external_user_id="oo-u",
                order_id="s-oo-det", amount=3000, payment_type=PaymentType.ONE_OFF,
                kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                idempotency_key="s-oo-det", requested_at=when, approved_at=when)
    db.add(p); await db.commit(); await db.refresh(p)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    # 전체 모드: 구독/일반 분리 금액·건수 + 총매출/환불/순매출
    html = (await client.get("/admin/settlement?from=2026-05-01&to=2026-05-31")).text
    assert "구독 매출" in html and "일반결제 매출" in html
    assert "총매출" in html and "환불" in html and "순매출" in html
    # 서비스별 모드: 일반결제 상세보기 → 결제상세
    html2 = (await client.get(
        f"/admin/settlement?from=2026-05-01&to=2026-05-31&service_id={svc.id}")).text
    assert f"/admin/payments/{p.id}" in html2
    # 요금제 select 노출
    assert 'name="plan_name"' in html2


async def test_settlement_all_mode_shows_sub_and_one_off_columns(client, db, redis_client, cipher):
    """전체 모드 표에 구독/일반 금액 컬럼이 표시되고 단건 결제가 합계에 포함된다."""
    svc_a, _, _ = await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin/settlement?from=2026-05-01&to=2026-05-31")).text
    # 컬럼 헤더 확인
    assert "<th>구독</th>" in html
    assert "<th>일반</th>" in html
    # 총합: 구독 10000+5000=15000, 단건 2000 → 전체 17000
    assert "17,000" in html
    # 정산서비스A 행: 구독 10000 + 단건 2000 = 12000
    assert "12,000" in html
    # 단건 금액(2,000원) 표시
    assert "2,000" in html


async def test_settlement_service_mode_plan_filter_excludes_oneoff(client, db, redis_client, cipher):
    """서비스별 모드에서 요금제 필터 지정 시 ONE_OFF 결제는 제외된다."""
    svc, _, _ = await create_service(db, cipher, name="정산플랜모드")
    plan = await create_plan(db, svc, name="모드플랜")
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="m-sub")
    when = datetime(2026, 5, 10, tzinfo=UTC)
    db.add(Payment(subscription_id=sub.id, service_id=svc.id, external_user_id="m-sub",
                   order_id="sm-sub", amount=10000, payment_type=PaymentType.RENEWAL,
                   kind=PaymentKind.SUBSCRIPTION, status=PaymentStatus.DONE,
                   idempotency_key="sm-sub", requested_at=when, approved_at=when))
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="m-oo",
                   order_id="sm-oo", amount=3000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="sm-oo", requested_at=when, approved_at=when))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get(
        f"/admin/settlement?from=2026-05-01&to=2026-05-31&service_id={svc.id}&plan_name=모드플랜")).text
    body = html[html.find("<tbody>"):]
    assert "sm-sub" in body and "sm-oo" not in body   # 요금제 필터 → 일반결제 제외
