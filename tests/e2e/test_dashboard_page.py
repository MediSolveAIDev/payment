"""대시보드 v2 화면 e2e (요청 010)."""
from datetime import timedelta

from app.core.clock import utcnow
from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login


async def _seed(db, cipher):
    svc, _, _ = await create_service(db, cipher, name="대시보드서비스")
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="d-act@e.com",
                                    status="ACTIVE")
    now = utcnow()
    db.add(Payment(subscription_id=sub.id, service_id=svc.id, external_user_id="d-act@e.com",
                   order_id="d-sub", amount=10000, payment_type=PaymentType.RENEWAL,
                   kind=PaymentKind.SUBSCRIPTION, status=PaymentStatus.DONE,
                   idempotency_key="d-sub", requested_at=now, approved_at=now))
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="oo@e.com",
                   order_id="d-oo", amount=4000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="d-oo", requested_at=now, approved_at=now))
    await db.commit()
    return svc


async def test_dashboard_revenue_section(client, db, redis_client, cipher):
    await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    for label in ["총매출", "구독매출", "일반매출", "환불금액"]:
        assert label in html
    assert "서비스별 매출" in html
    assert "대시보드서비스" in html


async def test_dashboard_subscription_section(client, db, redis_client, cipher):
    await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    # 카드 제거 — 값은 도넛 옆 클릭 리스트로 표현(구 카드 라벨 '현재 구독' 부재)
    assert "현재 구독" not in html
    assert "구독 상태" in html             # 도넛(전체 상태)
    assert "전체 상태" in html             # 도넛이 전체 상태 기준임을 명시
    # 도넛 옆 흐름 지표(클릭 시 상세)
    for label in ["신규 구독", "구독 취소", "구독 만료", "미결제"]:
        assert label in html
    assert 'href="/admin/subscriptions?status=ACTIVE"' in html       # 도넛 상태 범례 링크
    assert 'href="/admin/subscriptions?status=EXPIRED"' in html      # 만료 → 상세
    assert 'href="/admin/payments?status=FAILED' in html            # 미결제 → 상세
    assert "최근 30일" in html             # 일별 추이
    assert "서비스별 구독" in html         # 서비스별 구독 표


async def test_dashboard_twelve_month_charts(client, db, redis_client, cipher):
    await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    assert "최근 12개월 구독" in html
    assert "최근 12개월 일반매출" in html


async def test_dashboard_manager_scope_no_service_tables(client, db, redis_client, cipher):
    svc = await _seed(db, cipher)
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, mgr.email, pw)
    html = (await client.get("/admin")).text
    assert "서비스별 매출" not in html and "서비스별 구독" not in html
    assert "총매출" in html                # 카드는 스코프 집계로 노출


async def test_dashboard_recent_subs_and_trial_payment(client, db, redis_client, cipher):
    """최근 구독 패널(1.1.1) + 트라이얼/0원 구독이 최근 결제에 0원으로 표시(1.1.2)."""
    svc, _, _ = await create_service(db, cipher, name="레일서비스")
    plan = await create_plan(db, svc, trial_enabled=True, trial_days=14)
    # 트라이얼 구독(Payment 없음) — 최근 결제에 0원·체험으로 합쳐 표시되어야 함
    await create_subscription(db, cipher, svc, plan, external_user_id="trial-u@e.com",
                              status="TRIAL")
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    assert "최근 구독" in html                 # 신규 패널
    assert "trial-u@e.com" in html                   # 트라이얼 구독이 레일에 노출
    assert "0원" in html and "체험" in html     # 최근 결제에 0원·체험 표시


async def test_dashboard_shows_live_clock(client, db, redis_client, cipher):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    assert 'id="dash-clock"' in html          # 시계 엘리먼트
    assert "Asia/Seoul" in html                # KST 타임존
    assert "setInterval" in html               # 매초 갱신
