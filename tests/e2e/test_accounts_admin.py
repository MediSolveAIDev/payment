from sqlalchemy import select

from app.models import PasswordSetupToken, User, UserService
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


async def test_admin_creates_manager_account_with_services(client, db, redis_client,
                                                           cipher, email_sender):
    svc1, _, _ = await create_service(db, cipher, name="ca-1")
    svc2, _, _ = await create_service(db, cipher, name="ca-2")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post("/admin/users", data={
        "csrf_token": csrf, "email": "newmgr@x.com", "role": "SERVICE_MANAGER",
        "service_ids": [str(svc1.id), str(svc2.id)]})
    assert resp.status_code == 303
    user = await db.scalar(select(User).where(User.email == "newmgr@x.com"))
    assert user.role == "SERVICE_MANAGER" and user.status == "PENDING"
    assert user.service_id == svc1.id
    links = (await db.scalars(select(UserService).where(UserService.user_id == user.id))).all()
    assert {l.service_id for l in links} == {svc2.id}
    assert await db.scalar(select(PasswordSetupToken).where(
        PasswordSetupToken.user_id == user.id)) is not None
    assert any("계정 설정" in m["subject"] for m in email_sender.sent)


async def test_create_account_page_requires_admin(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, user.email, pw)
    assert (await client.get("/admin/users/new")).status_code == 403


async def test_manager_with_two_services_sees_both(client, db, redis_client, cipher):
    """다대다 핵심: 두 서비스를 담당하면 양쪽 구독을 모두 본다."""
    svc_a, _, _ = await create_service(db, cipher, name="ms-a")
    svc_b, _, _ = await create_service(db, cipher, name="ms-b")
    svc_c, _, _ = await create_service(db, cipher, name="ms-c")
    plan_a = await create_plan(db, svc_a)
    plan_b = await create_plan(db, svc_b)
    plan_c = await create_plan(db, svc_c)
    await create_subscription(db, cipher, svc_a, plan_a, external_user_id="u-in-a@e.com")
    await create_subscription(db, cipher, svc_b, plan_b, external_user_id="u-in-b@e.com")
    await create_subscription(db, cipher, svc_c, plan_c, external_user_id="u-in-c@e.com")
    # 매니저: svc_a(주) + svc_b(추가)
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc_a.id)
    db.add(UserService(user_id=mgr.id, service_id=svc_b.id))
    await db.commit()

    await admin_login(client, mgr.email, pw)
    html = (await client.get("/admin/subscriptions")).text
    assert "u-in-a@e.com" in html and "u-in-b@e.com" in html
    assert "u-in-c@e.com" not in html  # 담당 아닌 서비스는 안 보임


async def test_service_detail_assign_and_remove_manager(client, db, redis_client,
                                                        cipher, email_sender):
    svc, _, _ = await create_service(db, cipher, name="assign-svc")
    other_svc, _, _ = await create_service(db, cipher, name="other-svc")
    mgr, _ = await create_user(db, role="SERVICE_MANAGER", service_id=other_svc.id)
    csrf = await _admin(client, db, redis_client)
    # 할당
    resp = await client.post(f"/admin/services/{svc.id}/assign-manager",
                             data={"csrf_token": csrf, "user_id": str(mgr.id)})
    assert resp.status_code == 303
    link = await db.scalar(select(UserService).where(
        UserService.user_id == mgr.id, UserService.service_id == svc.id))
    assert link is not None
    # 해제
    resp = await client.post(f"/admin/services/{svc.id}/managers/{mgr.id}/remove",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    link = await db.scalar(select(UserService).where(
        UserService.user_id == mgr.id, UserService.service_id == svc.id))
    assert link is None


async def test_account_detail_assign_remove_service(client, db, redis_client, cipher):
    svc1, _, _ = await create_service(db, cipher, name="ad-1")
    svc2, _, _ = await create_service(db, cipher, name="ad-2")
    mgr, _ = await create_user(db, role="SERVICE_MANAGER", service_id=svc1.id)
    csrf = await _admin(client, db, redis_client)
    page = await client.get(f"/admin/users/{mgr.id}")
    assert page.status_code == 200 and "ad-1" in page.text
    # svc2 추가
    resp = await client.post(f"/admin/users/{mgr.id}/services",
                             data={"csrf_token": csrf, "service_id": str(svc2.id)})
    assert resp.status_code == 303
    assert await db.scalar(select(UserService).where(
        UserService.user_id == mgr.id, UserService.service_id == svc2.id)) is not None
    # svc2 해제
    resp = await client.post(f"/admin/users/{mgr.id}/services/{svc2.id}/remove",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    assert await db.scalar(select(UserService).where(
        UserService.user_id == mgr.id, UserService.service_id == svc2.id)) is None


async def test_account_edit_updates_email_and_phone(client, db, redis_client, cipher):
    target, _ = await create_user(db, role="SERVICE_MANAGER",
                                  service_id=(await create_service(db, cipher))[0].id)
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/users/{target.id}/edit", data={
        "csrf_token": csrf, "email": "renamed@x.com", "phone": "010-2222-3333"})
    assert resp.status_code == 303
    await db.refresh(target)
    assert target.email == "renamed@x.com" and target.phone == "010-2222-3333"


async def test_account_edit_duplicate_email_blocked(client, db, redis_client, cipher):
    other, _ = await create_user(db, email="taken@x.com")
    target, _ = await create_user(db, email="me@x.com")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/users/{target.id}/edit", data={
        "csrf_token": csrf, "email": "taken@x.com", "phone": ""})
    assert resp.status_code == 200
    assert "이미 존재하는 이메일" in resp.text
    await db.refresh(target)
    assert target.email == "me@x.com"  # 변경 안 됨


async def test_account_disable_and_delete(client, db, redis_client, cipher):
    target, _ = await create_user(db, role="SERVICE_MANAGER",
                                  service_id=(await create_service(db, cipher))[0].id)
    csrf = await _admin(client, db, redis_client)
    # 비활성화
    resp = await client.post(f"/admin/users/{target.id}/disable",
                             data={"csrf_token": csrf, "disabled": "true"})
    assert resp.status_code == 303
    await db.refresh(target)
    assert target.status == "DISABLED"
    # 삭제(소프트)
    resp = await client.post(f"/admin/users/{target.id}/delete",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    await db.refresh(target)
    assert target.status == "DELETED"
    # 목록에서 숨김
    assert target.email not in (await client.get("/admin/users")).text


async def test_cannot_delete_self(client, db, redis_client, cipher):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    csrf = await get_csrf(redis_client, sid)
    resp = await client.post(f"/admin/users/{admin.id}/delete", data={"csrf_token": csrf})
    assert resp.status_code == 303  # 오류 리다이렉트
    assert (await db.get(User, admin.id)).status == "ACTIVE"  # 삭제 안 됨
