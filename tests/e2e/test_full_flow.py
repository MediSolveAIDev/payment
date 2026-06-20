import re
from datetime import timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.clock import utcnow
from app.models import Payment, Plan, Subscription, User
from app.scheduler.runner import run_renewals
from tests.factories import create_user
from tests.helpers import admin_login, api_request, get_csrf


async def _register_card_api(client, api_key, hmac_secret, external_user_id, *,
                             customer_key, auth_key="auth-key"):
    """카드 등록 API 헬퍼 — POST /api/v1/cards 호출.

    카드 보관함 전환(Task 7+) 이후 구독/결제 전에 반드시 카드를 먼저 등록해야 한다.
    """
    resp = await api_request(client, "POST", "/api/v1/cards", api_key, hmac_secret,
                             json_body={"external_user_id": external_user_id,
                                        "customer_key": customer_key,
                                        "auth_key": auth_key})
    assert resp.status_code == 201, f"카드 등록 실패: {resp.status_code} {resp.text}"
    return resp


async def test_full_subscription_lifecycle(client, app, db, redis_client, cipher,
                                           fake_toss, email_sender):
    # 1) 시스템 관리자: 담당자 계정 생성(설정 메일) → 서비스 등록(계정 선택) → 키 1회 발급
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    session_id = await admin_login(client, admin.email, pw)
    csrf = await get_csrf(redis_client, session_id)
    resp = await client.post("/admin/users", data={
        "csrf_token": csrf, "email": "mgr-e2e@medisolveai.com",
        "role": "SERVICE_MANAGER"})
    assert resp.status_code == 303
    manager = await db.scalar(select(User).where(
        User.email == "mgr-e2e@medisolveai.com"))
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "e2e-service",
        "manager_ids": [str(manager.id)], "primary_user_id": str(manager.id),
        "allowed_ips": "127.0.0.1"})
    assert resp.status_code == 200
    api_key = re.search(r'data-key="(svc_[^"]+)"', resp.text).group(1)
    hmac_secret = re.search(r'data-secret="([^"]+)"', resp.text).group(1)

    # 2) 담당자: 메일의 토큰으로 비밀번호 설정 → 로그인 → 요금제 생성
    setup_mail = email_sender.sent[0]
    token = re.search(r"token=([A-Za-z0-9_\-]+)", setup_mail["body"]).group(1)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as mgr:
        await mgr.post("/admin/setup-password", data={
            "token": token, "password": "ManagerPass12",
            "password_confirm": "ManagerPass12"})
        mgr_session = await admin_login(mgr, "mgr-e2e@medisolveai.com", "ManagerPass12")
        mgr_csrf = await get_csrf(redis_client, mgr_session)
        create_resp = await mgr.post("/admin/plans", data={
            "csrf_token": mgr_csrf, "name": "E2E 요금제", "price": "15000",
            "billing_cycle": "MONTH", "cycle_days": "",
            "first_payment_type": "DISCOUNT_PERCENT", "first_payment_value": "50"})
        assert create_resp.status_code == 303
    plan = await db.scalar(select(Plan).where(Plan.name == "E2E 요금제"))

    # 3) 외부 서비스: 카드 선등록 후 HMAC 서명 API로 구독 생성 (첫구독 50% 할인 → 7,500원)
    # 카드 보관함 전환(Task 7+): 구독 요청 전에 반드시 카드를 먼저 등록해야 한다.
    await _register_card_api(client, api_key, hmac_secret, "e2e-user",
                             customer_key="ck-e2e-user", auth_key="auth-from-widget")
    resp = await api_request(client, "POST", "/api/v1/subscriptions",
                             api_key, hmac_secret,
                             json_body={"external_user_id": "e2e-user",
                                        "plan_id": str(plan.id)})
    assert resp.status_code == 201
    assert resp.json()["status"] == "ACTIVE"
    assert fake_toss.charges[0]["amount"] == 7500

    # 4) 만료일 도래 → 스케줄러 배치 → 정가로 자동연장
    sub = await db.scalar(select(Subscription))
    past = utcnow() - timedelta(minutes=5)
    sub.current_period_start = past - timedelta(days=31)
    sub.current_period_end = past
    sub.next_billing_at = past
    await db.commit()
    stats = await run_renewals(app)
    assert stats["renewed"] == 1
    assert fake_toss.charges[1]["amount"] == 15000  # 갱신은 정가

    # 5) 취소 → 재개 → 다시 취소 → 만료 처리
    # 카드 보관함 전환(Task 7+) 이후 구독 만료 시 빌링키를 삭제하지 않는다.
    # 카드는 영속적 자원이므로 구독이 끝나도 카드(빌링키)가 보존된다.
    cancel = await api_request(client, "POST",
                               "/api/v1/subscriptions/e2e-user/cancel",
                               api_key, hmac_secret)
    assert cancel.json()["status"] == "CANCELED"
    resume = await api_request(client, "POST",
                               "/api/v1/subscriptions/e2e-user/resume",
                               api_key, hmac_secret)
    assert resume.json()["status"] == "ACTIVE"
    await api_request(client, "POST", "/api/v1/subscriptions/e2e-user/cancel",
                      api_key, hmac_secret)
    await db.refresh(sub)
    sub.current_period_end = utcnow() - timedelta(minutes=1)
    await db.commit()
    stats = await run_renewals(app)
    assert stats["expired"] == 1
    status_resp = await api_request(client, "GET",
                                    "/api/v1/subscriptions/e2e-user",
                                    api_key, hmac_secret)
    assert status_resp.json()["status"] == "EXPIRED"
    # 카드 보관함 전환 이후 구독 만료 시 빌링키를 삭제하지 않음 — 카드는 영속
    assert not fake_toss.deleted

    # 6) 결제 이력 API + 관리자 대시보드 반영
    pays = await api_request(client, "GET", "/api/v1/payments/e2e-user",
                             api_key, hmac_secret)
    assert len(pays.json()["payments"]) == 2
    dash = await client.get("/admin")
    assert dash.status_code == 200
    payments = (await db.scalars(select(Payment))).all()
    assert len(payments) == 2
