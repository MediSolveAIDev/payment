"""결제서버 킬스위치 E2E 테스트 (요청 013).

킬스위치 ON 상태에서:
- 외부 API(HMAC 인증 포함)가 503 + SERVER_DISABLED + 사유를 반환한다.
- 어드민 라우트는 503 영향을 받지 않고 정상(200) 응답한다.
"""
from tests.factories import create_plan, create_service, create_user
from tests.helpers import admin_login, api_request


async def test_external_api_returns_503_when_server_disabled(client, db, cipher):
    """킬스위치 ON 후 외부 API(GET /api/v1/plans)가 503 + SERVER_DISABLED + 사유를 반환한다."""
    from app.services import app_settings

    svc, api_key, secret = await create_service(db, cipher)
    await create_plan(db, svc, name="베이직")

    # 킬스위치 ON: server_disabled=True 로 직접 설정
    gs = await app_settings.get_global_settings(db)
    gs.server_disabled = True
    gs.disabled_reason = "긴급 점검 중"
    await db.commit()

    # 정상 HMAC 서명 요청도 503 으로 차단되어야 한다
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "SERVER_DISABLED"
    # disabled_reason 이 응답 메시지에 포함되어야 한다
    assert "점검" in body["error"]["message"]


async def test_admin_page_unaffected_when_server_disabled(client, db, cipher):
    """킬스위치 ON 상태에서도 어드민 페이지(GET /admin/services)는 200 반환한다."""
    from app.services import app_settings

    # 킬스위치 ON
    gs = await app_settings.get_global_settings(db)
    gs.server_disabled = True
    gs.disabled_reason = "긴급 점검 중"
    await db.commit()

    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)

    resp = await client.get("/admin/services")
    # 어드민 라우트는 authenticate_service 를 거치지 않으므로 킬스위치 영향 없음
    assert resp.status_code == 200
