from datetime import timedelta

from app.core.clock import utcnow
from app.core.security import sha256_hex
from app.models import PasswordSetupToken
from tests.factories import create_user
from tests.helpers import admin_login, get_csrf


async def test_login_page_renders(client):
    resp = await client.get("/admin/login")
    assert resp.status_code == 200
    assert "로그인" in resp.text


async def test_login_success_and_dashboard(client, db):
    user, pw = await create_user(db)
    await admin_login(client, user.email, pw)
    resp = await client.get("/admin")
    assert resp.status_code == 200
    assert "대시보드" in resp.text


async def test_session_cookie_flags(client, db):
    user, pw = await create_user(db)
    resp = await client.post("/admin/login", data={"email": user.email, "password": pw})
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


async def test_wrong_password_shows_error(client, db):
    user, _ = await create_user(db)
    resp = await client.post("/admin/login",
                             data={"email": user.email, "password": "nope"})
    assert resp.status_code == 200
    assert "올바르지 않습니다" in resp.text


async def test_anonymous_redirected_to_login(client):
    resp = await client.get("/admin")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


async def test_htmx_request_gets_hx_redirect(client):
    resp = await client.get("/admin", headers={"HX-Request": "true"})
    assert resp.status_code == 204
    assert resp.headers["hx-redirect"] == "/admin/login"


async def test_logout_destroys_session(client, db, redis_client):
    user, pw = await create_user(db)
    session_id = await admin_login(client, user.email, pw)
    csrf = await get_csrf(redis_client, session_id)
    resp = await client.post("/admin/logout", data={"csrf_token": csrf})
    assert resp.status_code == 303
    after = await client.get("/admin")
    assert after.status_code == 303  # 세션 무효 — 로그인으로


async def test_logout_without_csrf_rejected(client, db):
    user, pw = await create_user(db)
    await admin_login(client, user.email, pw)
    resp = await client.post("/admin/logout", data={})
    assert resp.status_code == 403


async def test_setup_password_full_flow(client, db):
    user, _ = await create_user(db, status="PENDING")
    token = "setup-" + "z" * 26
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()

    page = await client.get(f"/admin/setup-password?token={token}")
    assert page.status_code == 200

    resp = await client.post("/admin/setup-password",
                             data={"token": token, "password": "BrandNewPass12",
                                   "password_confirm": "BrandNewPass12"})
    assert resp.status_code == 303
    await admin_login(client, user.email, "BrandNewPass12")  # 새 비밀번호로 로그인 성공


async def test_setup_password_mismatch_shows_error(client, db):
    user, _ = await create_user(db, status="PENDING")
    token = "setup-" + "y" * 26
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()
    resp = await client.post("/admin/setup-password",
                             data={"token": token, "password": "BrandNewPass12",
                                   "password_confirm": "Different12345"})
    assert resp.status_code == 200
    assert "일치하지 않습니다" in resp.text


async def test_password_reset_destroys_existing_session(client, db, redis_client):
    """ACTIVE 사용자가 setup-password로 비밀번호를 재설정하면 기존 세션이 파기된다."""
    from datetime import timedelta

    from app.core.clock import utcnow
    from app.core.security import sha256_hex
    from app.models import PasswordSetupToken
    user, pw = await create_user(db, status="ACTIVE")
    session_id = await admin_login(client, user.email, pw)
    assert (await client.get("/admin")).status_code == 200

    token = "reset-" + "q" * 26
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()
    # 별도 클라이언트로 재설정(세션 쿠키 없이)
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=client._transport.app),
                           base_url="http://test") as anon:
        resp = await anon.post("/admin/setup-password",
                               data={"token": token, "password": "ResetPassword12",
                                     "password_confirm": "ResetPassword12"})
        assert resp.status_code == 303
    # 기존 세션 무효화 — /admin 접근 시 로그인으로 리다이렉트
    after = await client.get("/admin")
    assert after.status_code == 303
