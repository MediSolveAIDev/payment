from sqlalchemy import select

from app.models import Plan, UserService
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


async def test_admin_creates_plan_from_service_detail(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="sp-create")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/plans", data={
        "csrf_token": csrf, "name": "프로", "price": "29000",
        "billing_cycle": "MONTH", "cycle_days": "",
        "first_payment_type": "DISCOUNT_PERCENT", "first_payment_value": "30"})
    assert resp.status_code == 303
    # saved_redirect 로 ?saved= 파람이 붙으므로 startswith로 완화
    assert resp.headers["location"].startswith(f"/admin/services/{svc.id}")
    plan = await db.scalar(select(Plan).where(Plan.name == "프로"))
    assert plan.service_id == svc.id and plan.price == 29000


async def test_service_detail_shows_plans(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="sp-show")
    await create_plan(db, svc, name="베이직플랜")
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "요금제 관리" in html and "베이직플랜" in html and "요금제 추가" in html


async def test_admin_edits_plan_returns_to_service(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="sp-edit")
    plan = await create_plan(db, svc, name="구플랜", price=10000)
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/plans/{plan.id}", data={
        "csrf_token": csrf, "name": "신플랜", "price": "15000",
        "first_payment_type": "NONE", "first_payment_value": "",
        "next": f"/admin/services/{svc.id}"})
    assert resp.status_code == 303
    # saved_redirect 로 ?saved= 파람이 붙으므로 startswith로 완화
    assert resp.headers["location"].startswith(f"/admin/services/{svc.id}")
    await db.refresh(plan)
    assert plan.name == "신플랜" and plan.price == 15000


async def test_manager_manages_secondary_service_plan(client, db, redis_client, cipher):
    """다중 서비스 담당자가 추가 부여된 서비스의 요금제를 관리할 수 있다."""
    primary, _, _ = await create_service(db, cipher, name="sp-primary")
    secondary, _, _ = await create_service(db, cipher, name="sp-secondary")
    plan = await create_plan(db, secondary, name="2차플랜", price=5000)
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=primary.id)
    db.add(UserService(user_id=mgr.id, service_id=secondary.id))
    await db.commit()
    sid = await admin_login(client, mgr.email, pw)
    csrf = await get_csrf(redis_client, sid)
    resp = await client.post(f"/admin/plans/{plan.id}", data={
        "csrf_token": csrf, "name": "2차수정", "price": "6000",
        "first_payment_type": "NONE", "first_payment_value": ""})
    assert resp.status_code == 303
    await db.refresh(plan)
    assert plan.name == "2차수정"


async def test_manager_cannot_manage_unassigned_service_plan(client, db, redis_client, cipher):
    own, _, _ = await create_service(db, cipher, name="sp-own")
    other, _, _ = await create_service(db, cipher, name="sp-other")
    plan = await create_plan(db, other, name="남의플랜")
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=own.id)
    sid = await admin_login(client, mgr.email, pw)
    csrf = await get_csrf(redis_client, sid)
    resp = await client.post(f"/admin/plans/{plan.id}", data={
        "csrf_token": csrf, "name": "탈취", "price": "1",
        "first_payment_type": "NONE", "first_payment_value": ""})
    assert resp.status_code == 404
    await db.refresh(plan)
    assert plan.name == "남의플랜"


async def test_plan_delete_conflict_returns_to_service_with_error(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="sp-del")
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/plans/{plan.id}/delete", data={
        "csrf_token": csrf, "next": f"/admin/services/{svc.id}"},
        follow_redirects=True)
    assert "삭제할 수 없습니다" in resp.text
