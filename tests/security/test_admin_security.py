from sqlalchemy import select

from app.models import Plan
from tests.factories import create_plan, create_service, create_user
from tests.helpers import admin_login, get_csrf


async def test_bogus_session_cookie_redirects(client):
    client.cookies.set("admin_session", "forged-session-id")
    resp = await client.get("/admin")
    assert resp.status_code == 303


async def test_old_session_invalid_after_logout(client, db, redis_client):
    user, pw = await create_user(db)
    session_id = await admin_login(client, user.email, pw)
    csrf = await get_csrf(redis_client, session_id)
    await client.post("/admin/logout", data={"csrf_token": csrf})
    client.cookies.set("admin_session", session_id)  # 옛 세션 재사용 시도
    resp = await client.get("/admin")
    assert resp.status_code == 303


async def test_csrf_wrong_token_blocks_state_change(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, user.email, pw)
    resp = await client.post("/admin/plans", data={
        "csrf_token": "wrong-token", "name": "공격요금제", "price": "1000",
        "billing_cycle": "MONTH", "cycle_days": "",
        "first_payment_type": "NONE", "first_payment_value": ""})
    assert resp.status_code == 403
    assert await db.scalar(select(Plan).where(Plan.name == "공격요금제")) is None


async def test_manager_cannot_rotate_service_keys(client, db, redis_client, cipher):
    """권한 상승 시도 — SERVICE_MANAGER가 SYSTEM_ADMIN 기능 호출."""
    svc, _, _ = await create_service(db, cipher)
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    session_id = await admin_login(client, user.email, pw)
    csrf = await get_csrf(redis_client, session_id)
    resp = await client.post(f"/admin/services/{svc.id}/rotate-keys",
                             data={"csrf_token": csrf})
    assert resp.status_code == 403


async def test_lockout_via_http(client, db):
    user, pw = await create_user(db)
    for _ in range(5):
        await client.post("/admin/login", data={"email": user.email, "password": "wrong"})
    resp = await client.post("/admin/login", data={"email": user.email, "password": pw})
    assert resp.status_code == 200
    assert "잠겼습니다" in resp.text


async def test_pending_user_cannot_login_http(client, db):
    user, pw = await create_user(db, status="PENDING")
    resp = await client.post("/admin/login", data={"email": user.email, "password": pw})
    assert "비밀번호 설정이 필요합니다" in resp.text


async def test_login_errors_do_not_reveal_account_existence(client, db):
    user, _ = await create_user(db)
    r1 = await client.post("/admin/login",
                           data={"email": user.email, "password": "wrong"})
    r2 = await client.post("/admin/login",
                           data={"email": "ghost@nowhere.com", "password": "wrong"})
    assert "이메일 또는 비밀번호가 올바르지 않습니다" in r1.text
    assert "이메일 또는 비밀번호가 올바르지 않습니다" in r2.text


async def test_manager_rotate_keys_does_not_change_keys(client, db, redis_client):
    """권한 상승 차단을 상태 수준에서 검증 — 매니저 시도 후 키 해시 불변."""
    from app.core.crypto import AesGcmCipher
    from app.models import Service
    cipher = AesGcmCipher(__import__("base64").b64encode(b"\x01" * 32).decode())
    svc, _, _ = await create_service(db, cipher)
    before = svc.api_key_hash
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    session_id = await admin_login(client, user.email, pw)
    csrf = await get_csrf(redis_client, session_id)
    resp = await client.post(f"/admin/services/{svc.id}/rotate-keys",
                             data={"csrf_token": csrf})
    assert resp.status_code == 403
    await db.refresh(svc)
    fresh = await db.get(Service, svc.id)
    assert fresh.api_key_hash == before  # 키 미변경
