"""메일 발송 결과 flash → 토스트 표시 (base.html data-flash)."""
from urllib.parse import quote

from tests.factories import create_service, create_user
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


async def test_render_injects_flash_from_query_params(client, db, redis_client):
    await _admin(client, db, redis_client)
    resp = await client.get("/admin/users?flash=hello&flash_type=error")
    assert 'data-flash="hello"' in resp.text
    assert 'data-flash-type="error"' in resp.text


async def test_flash_without_type_defaults_to_complete(client, db, redis_client):
    await _admin(client, db, redis_client)
    resp = await client.get("/admin/users?flash=hello")
    assert 'data-flash-type="complete"' in resp.text


async def test_no_flash_param_no_data_flash_attr(client, db, redis_client):
    await _admin(client, db, redis_client)
    resp = await client.get("/admin/users")
    assert "data-flash" not in resp.text


def test_email_flash_qs_success():
    from app.admin.flash import email_flash_qs
    assert email_flash_qs(True, "메일을 발송했습니다") == f"flash={quote('메일을 발송했습니다')}"


def test_email_flash_qs_failure():
    from app.admin.flash import EMAIL_FAIL_MSG, email_flash_qs
    qs = email_flash_qs(False, "메일을 발송했습니다")
    assert qs == f"flash={quote(EMAIL_FAIL_MSG)}&flash_type=error"


async def test_reset_password_success_flash(client, db, redis_client):
    csrf = await _admin(client, db, redis_client)
    target, _ = await create_user(db, role="SYSTEM_ADMIN")
    resp = await client.post(f"/admin/users/{target.id}/reset-password",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    assert quote("비밀번호 재설정 메일을 발송했습니다") in resp.headers["location"]


async def test_reset_password_failure_flash(client, db, redis_client, email_sender):
    email_sender.fail = True
    csrf = await _admin(client, db, redis_client)
    target, _ = await create_user(db, role="SYSTEM_ADMIN")
    resp = await client.post(f"/admin/users/{target.id}/reset-password",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    assert "flash_type=error" in resp.headers["location"]


async def test_create_account_success_flash(client, db, redis_client, cipher):
    """계정 생성 성공 시 flash(이메일 결과 토스트) + saved(완료 모달) 모두 Location에 포함."""
    from urllib.parse import unquote_plus
    svc, _, _ = await create_service(db, cipher, name="flash-acc-svc")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post("/admin/users", data={
        "csrf_token": csrf, "email": "flash-mgr@x.com", "role": "SERVICE_MANAGER",
        "service_ids": [str(svc.id)]})
    assert resp.status_code == 303
    location = resp.headers.get("location", "")
    # urlencode(+) / quote(%20) 방식 모두 대응: unquote_plus로 디코딩 후 확인
    decoded = unquote_plus(location)
    assert "계정 설정 메일을 발송했습니다" in decoded
    assert "saved" in location   # 완료 모달 트리거도 함께 전달


async def test_create_account_failure_flash(client, db, redis_client, cipher,
                                            email_sender):
    email_sender.fail = True
    svc, _, _ = await create_service(db, cipher, name="flash-acc-fail")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post("/admin/users", data={
        "csrf_token": csrf, "email": "flash-mgr2@x.com", "role": "SERVICE_MANAGER",
        "service_ids": [str(svc.id)]})
    assert resp.status_code == 303
    assert "flash_type=error" in resp.headers["location"]


