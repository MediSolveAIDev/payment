import re

from sqlalchemy import select

from app.models import Plan, Service, User
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login, get_csrf


async def _login_admin(client, db, redis_client):
    user, pw = await create_user(db, role="SYSTEM_ADMIN")
    session_id = await admin_login(client, user.email, pw)
    return await get_csrf(redis_client, session_id)


async def _login_manager(client, db, redis_client, service):
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=service.id)
    session_id = await admin_login(client, user.email, pw)
    return await get_csrf(redis_client, session_id)


async def test_register_service_shows_keys_once(client, db, redis_client):
    mgr, _ = await create_user(db, role="SERVICE_MANAGER",
                               email="mgr@medisolveai.com", service_id=None)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "mediness",
        "manager_ids": [str(mgr.id)], "primary_user_id": str(mgr.id),
        "allowed_ips": "10.0.0.1, 10.0.0.2"})
    assert resp.status_code == 200
    api_key = re.search(r'data-key="(svc_[^"]+)"', resp.text).group(1)
    secret = re.search(r'data-secret="([^"]+)"', resp.text).group(1)
    assert api_key and secret

    svc = await db.scalar(select(Service).where(Service.name == "mediness"))
    assert svc.allowed_ips == ["10.0.0.1", "10.0.0.2"]
    assert svc.manager_email == "mgr@medisolveai.com"
    await db.refresh(mgr)
    assert mgr.service_id == svc.id
    # 상세 페이지에는 키가 다시 노출되지 않음
    detail = await client.get(f"/admin/services/{svc.id}")
    assert api_key not in detail.text
    assert secret not in detail.text


async def test_register_service_invalid_ip_shows_error(client, db, redis_client):
    mgr, _ = await create_user(db, role="SERVICE_MANAGER", service_id=None)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "bad",
        "manager_ids": [str(mgr.id)], "primary_user_id": str(mgr.id),
        "allowed_ips": "not-an-ip"})
    assert resp.status_code == 200
    assert "유효하지 않은 IP" in resp.text
    # 에러 재렌더에도 담당자 계정 목록 유지
    assert str(mgr.id) in resp.text


async def test_rotate_keys_invalidates_old_hash(client, db, redis_client, cipher):
    svc, old_key, _ = await create_service(db, cipher)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/rotate-keys",
                             data={"csrf_token": csrf})
    assert resp.status_code == 200
    new_key = re.search(r'data-key="(svc_[^"]+)"', resp.text).group(1)
    assert new_key != old_key


async def test_update_ips(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/ips",
                             data={"csrf_token": csrf, "allowed_ips": "192.168.1.1"})
    assert resp.status_code == 303
    await db.refresh(svc)
    assert svc.allowed_ips == ["192.168.1.1"]


async def test_delete_service_with_subscription_blocked(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/delete",
                             data={"csrf_token": csrf}, follow_redirects=True)
    assert "삭제할 수 없습니다" in resp.text
    assert await db.get(Service, svc.id) is not None


async def test_manager_cannot_access_services_admin(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    await _login_manager(client, db, redis_client, svc)
    resp = await client.get("/admin/services")
    assert resp.status_code == 403


async def test_manager_creates_plan(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post("/admin/plans", data={
        "csrf_token": csrf, "name": "프로", "price": "29000",
        "billing_cycle": "MONTH", "cycle_days": "",
        "first_payment_type": "DISCOUNT_PERCENT", "first_payment_value": "30"},
        follow_redirects=True)
    assert resp.status_code == 200
    plan = await db.scalar(select(Plan).where(Plan.name == "프로"))
    assert plan.price == 29000
    assert plan.service_id == svc.id


async def test_manager_plan_validation_error_rendered(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post("/admin/plans", data={
        "csrf_token": csrf, "name": "x", "price": "0", "billing_cycle": "MONTH",
        "cycle_days": "", "first_payment_type": "NONE", "first_payment_value": ""})
    assert resp.status_code == 200
    assert "1원 이상" in resp.text


async def test_manager_edits_and_archives_plan(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post(f"/admin/plans/{plan.id}", data={
        "csrf_token": csrf, "name": "수정됨", "price": "15000",
        "billing_cycle": "WEEK",   # 결제 주기는 수정 불가 — 보내도 무시되어야 함(요청)
        "first_payment_type": "NONE", "first_payment_value": ""},
        follow_redirects=True)
    assert resp.status_code == 200
    await db.refresh(plan)
    assert plan.name == "수정됨"
    assert plan.billing_cycle == "MONTH"   # 결제 주기는 변경되지 않고 생성 시 값(MONTH) 유지

    await client.post(f"/admin/plans/{plan.id}/archive", data={"csrf_token": csrf})
    await db.refresh(plan)
    assert plan.status == "ARCHIVED"

    # 비활성화한 요금제를 다시 활성화 → ACTIVE 복귀
    await client.post(f"/admin/plans/{plan.id}/activate", data={"csrf_token": csrf})
    await db.refresh(plan)
    assert plan.status == "ACTIVE"


async def test_manager_cannot_touch_other_service_plan(client, db, redis_client, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="own-svc")
    svc_b, _, _ = await create_service(db, cipher, name="other-svc")
    plan_b = await create_plan(db, svc_b)
    csrf = await _login_manager(client, db, redis_client, svc_a)
    resp = await client.post(f"/admin/plans/{plan_b.id}", data={
        "csrf_token": csrf, "name": "해킹", "price": "1",
        "first_payment_type": "NONE", "first_payment_value": ""})
    assert resp.status_code == 404
    await db.refresh(plan_b)
    assert plan_b.name != "해킹"


async def test_manager_cannot_archive_or_delete_other_service_plan(client, db, redis_client, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="arch-own")
    svc_b, _, _ = await create_service(db, cipher, name="arch-other")
    plan_b = await create_plan(db, svc_b)
    csrf = await _login_manager(client, db, redis_client, svc_a)
    arch = await client.post(f"/admin/plans/{plan_b.id}/archive",
                             data={"csrf_token": csrf})
    assert arch.status_code == 404
    dele = await client.post(f"/admin/plans/{plan_b.id}/delete",
                             data={"csrf_token": csrf})
    assert dele.status_code == 404
    # 활성화도 동일하게 타 서비스 요금제는 404로 차단(스코프)
    act = await client.post(f"/admin/plans/{plan_b.id}/activate",
                            data={"csrf_token": csrf})
    assert act.status_code == 404
    await db.refresh(plan_b)
    assert plan_b.status == "ACTIVE"


async def test_plan_delete_conflict_error_shown_in_list(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post(f"/admin/plans/{plan.id}/delete",
                             data={"csrf_token": csrf}, follow_redirects=True)
    assert "삭제할 수 없습니다" in resp.text


async def test_new_service_form_lists_manager_accounts(client, db, redis_client):
    mgr, _ = await create_user(db, role="SERVICE_MANAGER",
                               email="pick-me@x.com", service_id=None)
    await _login_admin(client, db, redis_client)
    resp = await client.get("/admin/services/new")
    assert 'name="manager_ids"' in resp.text
    assert 'name="primary_user_id"' in resp.text
    assert "pick-me@x.com" in resp.text
    assert 'name="manager_email"' not in resp.text


async def test_new_service_form_no_managers_shows_guide(client, db, redis_client):
    await _login_admin(client, db, redis_client)
    resp = await client.get("/admin/services/new")
    assert "/admin/users/new" in resp.text
    assert 'name="manager_ids"' not in resp.text


async def test_register_without_primary_shows_error(client, db, redis_client):
    mgr, _ = await create_user(db, role="SERVICE_MANAGER", service_id=None)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "no-primary",
        "manager_ids": [str(mgr.id)], "allowed_ips": "10.0.0.1"})
    assert resp.status_code == 200
    assert "담당자를 1명 이상 선택" in resp.text


async def test_register_with_primary_only_auto_includes(client, db, redis_client):
    """체크박스 미선택 + 대표만 선택 → 서버가 자동 포함해 등록 성공."""
    mgr, _ = await create_user(db, role="SERVICE_MANAGER",
                               email="auto-inc@x.com", service_id=None)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "auto-inc-svc",
        "primary_user_id": str(mgr.id), "allowed_ips": "10.0.0.1"})
    assert resp.status_code == 200
    svc = await db.scalar(select(Service).where(Service.name == "auto-inc-svc"))
    assert svc.manager_email == "auto-inc@x.com"
    await db.refresh(mgr)
    assert mgr.service_id == svc.id


async def test_plans_list_filters_service_cycle_status(client, db, redis_client, cipher):
    """요금제 리스트 필터 — 서비스/주기/상태 + 요금제명 검색."""
    svc_a, _, _ = await create_service(db, cipher, name="플랜필터A")
    svc_b, _, _ = await create_service(db, cipher, name="플랜필터B")
    await create_plan(db, svc_a, name="월간요금제", billing_cycle="MONTH", status="ACTIVE")
    await create_plan(db, svc_a, name="연간요금제", billing_cycle="YEAR", status="ACTIVE")
    await create_plan(db, svc_b, name="보관요금제", billing_cycle="MONTH", status="ARCHIVED")
    await _login_admin(client, db, redis_client)

    def tbody(html):  # 드롭다운 옵션 텍스트와 섞이지 않게 테이블 본문만
        return html[html.find("<tbody>"):]

    # 서비스 필터
    body = tbody((await client.get(f"/admin/plans?service_id={svc_a.id}")).text)
    assert "월간요금제" in body and "연간요금제" in body and "보관요금제" not in body
    # 주기 필터
    body = tbody((await client.get("/admin/plans?billing_cycle=YEAR")).text)
    assert "연간요금제" in body and "월간요금제" not in body
    # 상태 필터
    body = tbody((await client.get("/admin/plans?status=ARCHIVED")).text)
    assert "보관요금제" in body and "월간요금제" not in body
    # 요금제명 검색(q)
    body = tbody((await client.get("/admin/plans?q=연간")).text)
    assert "연간요금제" in body and "월간요금제" not in body
    # 필터 컨트롤 렌더(서비스/주기 select)
    html = (await client.get("/admin/plans")).text
    assert 'name="service_id"' in html and 'name="billing_cycle"' in html
    assert "플랜필터A" in html      # 서비스 옵션


async def test_plans_list_plan_name_filter(client, db, redis_client, cipher):
    """요금제 조건 — 요금제명 select 드롭다운으로 필터."""
    svc, _, _ = await create_service(db, cipher, name="플랜선택svc")
    await create_plan(db, svc, name="베이직", billing_cycle="MONTH")
    await create_plan(db, svc, name="프리미엄", billing_cycle="MONTH")
    await _login_admin(client, db, redis_client)

    # 드롭다운 옵션에 요금제명이 노출됨
    html = (await client.get("/admin/plans")).text
    assert 'name="plan_name"' in html
    assert "베이직" in html and "프리미엄" in html
    # 선택 시 해당 요금제만 (테이블 본문 기준 — 옵션 목록 제외)
    body = (await client.get("/admin/plans?plan_name=베이직")).text
    body = body[body.find("<tbody>"):]
    assert "베이직" in body and "프리미엄" not in body


async def test_plan_options_scoped_to_selected_service(client, db, redis_client, cipher):
    """요금제 셀렉트는 선택한 서비스의 요금제만 가져온다."""
    svc_a, _, _ = await create_service(db, cipher, name="옵션svcA")
    svc_b, _, _ = await create_service(db, cipher, name="옵션svcB")
    await create_plan(db, svc_a, name="A전용플랜")
    await create_plan(db, svc_b, name="B전용플랜")
    await _login_admin(client, db, redis_client)

    # 전체(서비스 미선택)면 양쪽 다 옵션에 노출
    html = (await client.get("/admin/plans")).text
    assert "A전용플랜" in html and "B전용플랜" in html
    # 서비스 A 선택 시 A의 요금제만 옵션에 노출
    html = (await client.get(f"/admin/plans?service_id={svc_a.id}")).text
    assert "A전용플랜" in html and "B전용플랜" not in html


async def test_invalid_extra_info_returns_form_error_not_500_create(client, db, redis_client, cipher):
    """추가정보에 값만 있고 키가 비면 500이 아니라 폼 오류로 처리된다 (요청 013, 키/값 행 입력).

    Fix 1 검증: _form_plan_fields(_collect_extra_info)가 던지는 InputValidationError가
    plans_create/service_plan_create 라우트의 try 블록 안에서 DomainError로 잡혀
    폼 오류 렌더(200)로 처리돼야 한다.
    """
    svc, _, _ = await create_service(db, cipher, name="extra-info-test")
    # --- plans_create 경로 (SERVICE_MANAGER, /admin/plans POST) ---
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post("/admin/plans", data={
        "csrf_token": csrf, "name": "테스트", "price": "10000",
        "billing_cycle": "MONTH", "cycle_days": "",
        "first_payment_type": "NONE", "first_payment_value": "",
        # 키 없는 값 — _collect_extra_info가 InputValidationError를 발생시킨다
        "extra_key": "", "extra_value": "키없는값"})
    assert resp.status_code == 200, "500이 아니라 폼 오류(200)여야 한다"
    assert "추가정보 키를 입력하세요" in resp.text, "오류 메시지가 폼에 표시돼야 한다"

    # --- service_plan_create 경로 (SYSTEM_ADMIN, /admin/services/{id}/plans POST) ---
    csrf_admin = await _login_admin(client, db, redis_client)
    resp2 = await client.post(f"/admin/services/{svc.id}/plans", data={
        "csrf_token": csrf_admin, "name": "관리자테스트", "price": "5000",
        "billing_cycle": "MONTH", "cycle_days": "",
        "first_payment_type": "NONE", "first_payment_value": "",
        "extra_key": "", "extra_value": "키없는값"})
    assert resp2.status_code == 200, "서비스 상세 경유 경로도 500이 아니라 폼 오류(200)여야 한다"
    assert "추가정보 키를 입력하세요" in resp2.text


async def test_extra_info_rows_stored(client, db, redis_client, cipher):
    """추가정보 키/값 행이 dict로 수집되어 요금제에 저장된다 (요청 013).

    빈 행(키·값 모두 빈 행)은 무시되고, 채워진 행만 extra_info에 들어간다.
    """
    svc, _, _ = await create_service(db, cipher, name="extra-info-rows")
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post("/admin/plans", data={
        "csrf_token": csrf, "name": "행입력플랜", "price": "10000",
        "billing_cycle": "MONTH", "cycle_days": "",
        "first_payment_type": "NONE", "first_payment_value": "",
        # 두 개의 채워진 행 + 한 개의 빈 행(무시되어야 함)
        "extra_key": ["용량", "사용자수", ""],
        "extra_value": ["10GB", "5명", ""]})
    assert resp.status_code == 303, "정상 저장이면 상세로 리다이렉트(303)"
    plan = (await db.scalars(select(Plan).where(Plan.name == "행입력플랜"))).first()
    assert plan is not None and plan.extra_info == {"용량": "10GB", "사용자수": "5명"}


async def test_invalid_extra_info_returns_form_error_not_500_update(client, db, redis_client, cipher):
    """추가정보 키 없는 값 제출 시 plans_update 경로도 500이 아니라 폼 오류로 처리된다 (요청 013).

    Fix 1 검증: plans_update 라우트의 try 블록이 _form_plan_fields 호출을 포함하므로
    _collect_extra_info가 던지는 InputValidationError를 폼 오류로 처리해야 한다.
    """
    svc, _, _ = await create_service(db, cipher, name="extra-info-update-test")
    plan = await create_plan(db, svc, name="수정대상플랜")
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post(f"/admin/plans/{plan.id}", data={
        "csrf_token": csrf, "name": "수정대상플랜", "price": "10000",
        "first_payment_type": "NONE", "first_payment_value": "",
        # 키 없는 값 — _collect_extra_info가 InputValidationError를 발생시킨다
        "extra_key": "", "extra_value": "키없는값"})
    assert resp.status_code == 200, "plans_update도 500이 아니라 폼 오류(200)여야 한다"
    assert "추가정보 키를 입력하세요" in resp.text, "오류 메시지가 폼에 표시돼야 한다"


async def test_plan_bonus_days_extends_subscriptions(client, db, redis_client, cipher):
    """요금제 사용일추가 라우트 — 열린 구독 만료일·다음결제 +N일, EXPIRED 제외, 완료 메시지."""
    from datetime import timedelta
    from app.core.clock import utcnow
    svc, _, _ = await create_service(db, cipher, name="보너스서비스")
    plan = await create_plan(db, svc)
    base = utcnow().replace(microsecond=0)
    act = await create_subscription(db, cipher, svc, plan, external_user_id="bn-act",
                                    status="ACTIVE", period_end=base, next_billing_at=base)
    exp = await create_subscription(db, cipher, svc, plan, external_user_id="bn-exp",
                                    status="EXPIRED", period_end=base, next_billing_at=None)
    csrf = await _login_admin(client, db, redis_client)   # SYSTEM_ADMIN도 사용 가능
    resp = await client.post(f"/admin/plans/{plan.id}/bonus-days",
                             data={"csrf_token": csrf, "days": "30",
                                   "next": "/admin/plans"})
    assert resp.status_code == 303
    assert "saved" in resp.headers.get("location", "")    # 완료 모달 트리거
    await db.refresh(act); await db.refresh(exp)
    assert act.current_period_end == base + timedelta(days=30)
    assert act.next_billing_at == base + timedelta(days=30)
    assert exp.current_period_end == base                 # EXPIRED는 변경 없음


async def test_plans_menu_paginates_15_per_page(client, db, redis_client, cipher):
    """요금제 메뉴(/admin/plans) — 페이지당 15건 페이징 동작 검증.

    16건 생성 시 1페이지 1–15, 2페이지 16–16으로 분할된다(PER_PAGE_DEFAULT=15).
    """
    svc, _, _ = await create_service(db, cipher, name="plans-page-svc")
    for i in range(16):
        await create_plan(db, svc, name=f"pg-plan-{i:02d}")
    await _login_admin(client, db, redis_client)
    p1 = (await client.get("/admin/plans")).text
    assert "총 16건 중 1–15" in p1            # 1페이지: 15건
    p2 = (await client.get("/admin/plans?page=2")).text
    assert "총 16건 중 16–16" in p2           # 2페이지: 나머지 1건
