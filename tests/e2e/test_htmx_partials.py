"""요청 005: 리스트 정렬/필터/페이징의 htmx 부분 갱신."""
from tests.factories import create_user
from tests.helpers import admin_login, get_csrf


async def _admin(client, db):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)


async def _admin_csrf(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


LIST_PAGES = [
    ("/admin/services", "list-services"),
    ("/admin/plans", "list-plans"),
    ("/admin/subscriptions", "list-subs"),
    ("/admin/users", "list-users"),
    ("/admin/audit", "list-audit"),
]


async def test_full_page_contains_wrapper_and_hx_attrs(client, db):
    await _admin(client, db)
    for path, wrapper in LIST_PAGES:
        html = (await client.get(path)).text
        assert f'id="{wrapper}"' in html, path
        assert f'hx-target="#{wrapper}"' in html, path
        assert "<html" in html, path


async def test_hx_request_returns_partial_only(client, db):
    await _admin(client, db)
    for path, wrapper in LIST_PAGES:
        resp = await client.get(path, headers={"HX-Request": "true"})
        assert resp.status_code == 200, path
        assert "<html" not in resp.text, path       # 전체 페이지 아님
        assert f'id="{wrapper}"' in resp.text, path  # 교체 대상 wrapper 포함


async def test_hx_sort_param_applied(client, db):
    await _admin(client, db)
    resp = await client.get("/admin/services?sort=name&dir=asc",
                            headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert "<html" not in resp.text
    assert 'hx-target="#list-services"' in resp.text
    assert "dir=desc" in resp.text  # name 정렬 활성 → 다음 토글은 desc


async def test_detail_subs_sort_partial(client, db, redis_client, cipher):
    from tests.factories import create_plan, create_service, create_subscription
    svc, _, _ = await create_service(db, cipher, name="hx-subs-svc")
    plan = await create_plan(db, svc, name="hx-plan")
    await create_subscription(db, cipher, svc, plan, external_user_id="hx-sub-user@e.com")
    await _admin(client, db)
    resp = await client.get(f"/admin/services/{svc.id}?sort=status&dir=asc",
                            headers={"HX-Request": "true",
                                     "HX-Target": "list-svc-subs"})
    assert "<html" not in resp.text
    assert 'id="list-svc-subs"' in resp.text and "hx-sub-user@e.com" in resp.text
    assert 'id="list-svc-plans"' not in resp.text  # 구독 영역만


async def test_detail_plan_archive_partial_flow(client, db, redis_client, cipher):
    """hx-post 비활성화 → 303 → HX 헤더 유지 GET → 요금제 partial 응답 (브라우저 XHR 흐름 재현)."""
    from tests.factories import create_plan, create_service
    svc, _, _ = await create_service(db, cipher, name="hx-archive-svc")
    plan = await create_plan(db, svc, name="hx-archive-plan")
    csrf = await _admin_csrf(client, db, redis_client)
    resp = await client.post(f"/admin/plans/{plan.id}/archive",
                             data={"csrf_token": csrf,
                                   "next": f"/admin/services/{svc.id}"},
                             headers={"HX-Request": "true",
                                      "HX-Target": "list-svc-plans"})
    assert resp.status_code == 303
    follow = await client.get(resp.headers["location"],
                              headers={"HX-Request": "true",
                                       "HX-Target": "list-svc-plans"})
    assert "<html" not in follow.text
    assert 'id="list-svc-plans"' in follow.text
    assert "ARCHIVED" in follow.text  # 갱신된 상태 반영


async def test_detail_full_page_still_renders_both_lists(client, db, redis_client,
                                                         cipher):
    from tests.factories import create_service
    svc, _, _ = await create_service(db, cipher, name="hx-full-svc")
    await _admin(client, db)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "<html" in html
    assert 'id="list-svc-plans"' in html and 'id="list-svc-subs"' in html


async def test_detail_plan_delete_failure_shows_error_in_partial(client, db,
                                                                 redis_client, cipher):
    """구독 있는 요금제 삭제 실패 → ?error= 리다이렉트 → htmx follow 시 partial 안에 에러."""
    from tests.factories import create_plan, create_service, create_subscription
    svc, _, _ = await create_service(db, cipher, name="hx-delete-fail-svc")
    plan = await create_plan(db, svc, name="hx-delete-fail-plan")
    await create_subscription(db, cipher, svc, plan, external_user_id="del-fail-user@e.com")
    csrf = await _admin_csrf(client, db, redis_client)
    resp = await client.post(f"/admin/plans/{plan.id}/delete",
                             data={"csrf_token": csrf,
                                   "next": f"/admin/services/{svc.id}"},
                             headers={"HX-Request": "true",
                                      "HX-Target": "list-svc-plans"})
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    follow = await client.get(resp.headers["location"],
                              headers={"HX-Request": "true",
                                       "HX-Target": "list-svc-plans"})
    assert "<html" not in follow.text
    assert 'class="error"' in follow.text  # partial 안 에러 표시
    # 전체 페이지에서는 상단 에러 1회만 (이중 표시 없음)
    full = await client.get(resp.headers["location"])
    assert full.text.count('class="error"') == 1


async def test_service_detail_oneoff_partial(client, db, redis_client, cipher):
    """서비스 상세 일반결제 htmx partial — base 레이아웃 미포함, list-svc-oneoff wrapper 포함."""
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    from tests.factories import create_service
    svc, _, _ = await create_service(db, cipher, name="htmx일반결제")
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="hx-u@e.com",
                   order_id="hx-oo1", amount=3000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="hx-oo1", requested_at=utcnow(), approved_at=utcnow()))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    resp = await client.get(f"/admin/services/{svc.id}",
                            headers={"HX-Request": "true", "HX-Target": "list-svc-oneoff"})
    assert resp.status_code == 200
    body = resp.text
    assert 'id="list-svc-oneoff"' in body and "hx-oo1" in body
    assert "<html" not in body.lower()       # partial만 (base 레이아웃 미포함)
