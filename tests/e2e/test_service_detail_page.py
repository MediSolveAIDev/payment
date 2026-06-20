"""서비스 상세 화면 개선(요청 004) e2e + 취소 정책(요청 012) e2e."""
from sqlalchemy import select

from app.models import AuditLog, Service
from app.toss.fake import FakeTossClient
from tests.factories import (create_card, create_card_direct, create_plan,
                             create_service, create_subscription, create_user)
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


async def test_update_ips_newline_separated(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="ips-newline-svc")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/ips",
                             data={"csrf_token": csrf,
                                   "allowed_ips": "10.1.1.1\n10.1.1.2"})
    assert resp.status_code == 303
    svc = await db.scalar(select(Service).where(Service.id == svc.id))
    await db.refresh(svc)
    assert svc.allowed_ips == ["10.1.1.1", "10.1.1.2"]


async def test_detail_page_renders_ip_octet_rows(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="ips-oct-svc",
                                     allowed_ips=["10.2.2.1", "10.2.2.2"])
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "data-ip-rows" in html and "data-ip-add" in html
    assert 'type="hidden" name="allowed_ips"' in html
    # 기존 IP가 옥텟 값으로 프리필 (10.2.2.1 → 10/2/2/1)
    assert html.count('class="ip-oct"') == 8  # 2개 IP × 4칸
    assert 'value="10"' in html


async def test_update_ips_still_accepts_newline_payload(client, db, redis_client,
                                                        cipher):
    """hidden allowed_ips로 합성된 줄단위 payload — 서버 파서 불변."""
    svc, _, _ = await create_service(db, cipher, name="ips-oct-post-svc")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/ips",
                             data={"csrf_token": csrf,
                                   "allowed_ips": "10.9.9.1\n10.9.9.2"})
    assert resp.status_code == 303
    svc = await db.scalar(select(Service).where(Service.id == svc.id))
    await db.refresh(svc)
    assert svc.allowed_ips == ["10.9.9.1", "10.9.9.2"]


async def test_plan_form_has_amount_preview(client, db, redis_client, cipher):
    """금액 미리보기 박스가 생성/수정 폼 양쪽에 렌더링되는지 검증(계산은 JS, 앵커는 billing_math 단위 테스트)."""
    svc, _, _ = await create_service(db, cipher, name="preview-svc")
    plan = await create_plan(db, svc, name="preview-plan", price=10000)
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, mgr.email, pw)
    new_form = (await client.get("/admin/plans/new")).text
    assert 'id="amount-preview"' in new_form
    assert 'id="amt-first"' in new_form and 'id="amt-next"' in new_form
    edit_form = (await client.get(f"/admin/plans/{plan.id}/edit")).text
    assert 'id="amount-preview"' in edit_form


async def test_plan_tables_show_first_and_recurring_amounts(client, db, redis_client,
                                                            cipher):
    """정가 10,000 / 상시할인 5% → 정기 9,500 / 첫구독 1,000원 할인 → 첫 결제 9,000.
    요청 005: 첫 결제는 정가 기준 (상시 할인 미적용).
    """
    svc, _, _ = await create_service(db, cipher, name="amount-col-svc")
    await create_plan(db, svc, name="amount-plan", price=10000,
                      first_payment_type="DISCOUNT_AMOUNT", first_payment_value=1000,
                      recurring_discount_type="DISCOUNT_PERCENT",
                      recurring_discount_value=5)
    await _admin(client, db, redis_client)
    detail = (await client.get(f"/admin/services/{svc.id}")).text
    # 요청 005: 첫 결제는 정가 기준 — 10,000 − 1,000 = 9,000 (상시 5% 무시)
    assert "첫 결제액" in detail and "9,000" in detail and "9,500" in detail
    plans_page = (await client.get("/admin/plans")).text
    assert "첫 결제액" in plans_page and "9,000" in plans_page and "9,500" in plans_page


async def test_detail_action_buttons_in_page_head(client, db, redis_client, cipher):
    """요청 005: 비활성화/키재발급/삭제/키복사 버튼이 상단 서비스 이름 옆에 위치."""
    svc, _, _ = await create_service(db, cipher, name="head-btn-svc")
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    # 버튼들이 개요(h2)보다 앞(page-head 영역)에 등장
    assert html.index("키 재발급") < html.index("개요")
    assert html.index("키 복사") < html.index("개요")


async def test_detail_managers_in_overview_with_edit_delete(client, db, redis_client,
                                                            cipher):
    """요청 005: 관리자 할당 카드 제거 — 개요 카드에 담당자 목록(수정/삭제)+추가 토글 통합."""
    svc, _, _ = await create_service(db, cipher, name="mgr-overview-svc")
    mgr, _ = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "관리자 할당" not in html  # 별도 카드 제거
    assert f'href="/admin/users/{mgr.id}/edit"' in html  # 수정 버튼(링크)
    assert f"/admin/services/{svc.id}/managers/{mgr.id}/remove" in html  # 삭제 폼
    assert "담당자 추가" in html
    assert 'data-toggle="#assign-form"' in html  # 추가 버튼 → 인라인 폼 토글


async def test_detail_subscriptions_list_scoped_and_filtered(client, db, redis_client,
                                                             cipher):
    svc, _, _ = await create_service(db, cipher, name="sublist-svc")
    other, _, _ = await create_service(db, cipher, name="sublist-other")
    plan = await create_plan(db, svc, name="sub-plan")
    other_plan = await create_plan(db, other, name="other-plan")
    await create_subscription(db, cipher, svc, plan, external_user_id="sub-user-a")
    await create_subscription(db, cipher, svc, plan, external_user_id="sub-user-b",
                              status="CANCELED")
    await create_subscription(db, cipher, other, other_plan,
                              external_user_id="other-svc-user")
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "sub-user-a" in html and "sub-user-b" in html
    assert "other-svc-user" not in html  # 타 서비스 구독 미표시
    # status 필터
    html = (await client.get(f"/admin/services/{svc.id}?status=CANCELED")).text
    assert "sub-user-b" in html and "sub-user-a" not in html
    # 사용자 검색
    html = (await client.get(f"/admin/services/{svc.id}?q=user-a")).text
    assert "sub-user-a" in html and "sub-user-b" not in html


async def test_detail_subscriptions_paging(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="subpage-svc")
    plan = await create_plan(db, svc, name="page-plan")
    for i in range(16):  # PER_PAGE_DEFAULT=15 초과 → 2페이지
        await create_subscription(db, cipher, svc, plan,
                                  external_user_id=f"pg-user-{i:02d}")
    await _admin(client, db, redis_client)
    p1 = (await client.get(f"/admin/services/{svc.id}")).text
    assert "총 16건" in p1
    p2 = (await client.get(f"/admin/services/{svc.id}?page=2")).text
    assert "총 16건" in p2
    assert p2.count("pg-user-") == 1  # 16건 중 마지막 1건만 2페이지에 표시


async def test_detail_oneoff_paging(client, db, redis_client, cipher):
    """일반결제 섹션 10건씩 페이징 — 11건 생성 시 2페이지로 분할(opage 파라미터).

    opage는 구독 탭의 page와 분리되어 서로 영향 없이 페이징한다.
    """
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc, _, _ = await create_service(db, cipher, name="oneoff-page-svc")
    for i in range(11):
        db.add(Payment(subscription_id=None, service_id=svc.id,
                       external_user_id=f"oo-{i:02d}", order_id=f"oop-{i:02d}",
                       amount=1000, payment_type=PaymentType.ONE_OFF,
                       kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                       idempotency_key=f"oop-{i:02d}", requested_at=utcnow(),
                       approved_at=utcnow()))
    await db.commit()
    await _admin(client, db, redis_client)
    p1 = (await client.get(f"/admin/services/{svc.id}")).text
    assert p1.count("oop-") == 10           # 페이지당 10건만 노출
    p2 = (await client.get(f"/admin/services/{svc.id}?opage=2")).text
    assert p2.count("oop-") == 1            # 나머지 1건은 2페이지


async def test_detail_oneoff_htmx_partial(client, db, redis_client, cipher):
    """일반결제 페이저 클릭(htmx) — base 레이아웃 없이 list-svc-oneoff partial만 응답."""
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc, _, _ = await create_service(db, cipher, name="oneoff-htmx-svc")
    for i in range(11):
        db.add(Payment(subscription_id=None, service_id=svc.id,
                       external_user_id=f"oh-{i:02d}", order_id=f"ohp-{i:02d}",
                       amount=1000, payment_type=PaymentType.ONE_OFF,
                       kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                       idempotency_key=f"ohp-{i:02d}", requested_at=utcnow(),
                       approved_at=utcnow()))
    await db.commit()
    await _admin(client, db, redis_client)
    resp = await client.get(f"/admin/services/{svc.id}?opage=2",
                            headers={"HX-Request": "true",
                                     "HX-Target": "list-svc-oneoff"})
    body = resp.text
    assert "<!doctype" not in body.lower()  # 전체 페이지 아님
    assert 'id="list-svc-oneoff"' in body
    assert body.count("ohp-") == 1


async def test_detail_events_paging(client, db, redis_client, cipher):
    """이벤트 섹션 10건씩 페이징 — IP 갱신 11회로 이벤트 11건 생성 시 2페이지로 분할.

    page 파라미터는 구독/단건결제 탭 전용이므로 이벤트는 epage 파라미터를 쓴다.
    """
    from app.services import registry
    svc, _, _ = await create_service(db, cipher, name="evt-page-svc",
                                     allowed_ips=["10.0.0.1"])
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    for i in range(2, 13):  # 11회 → "허용 IP 변경" 이벤트 11건
        await registry.update_allowed_ips(db, svc.id, [f"10.0.0.{i}"],
                                          actor_user_id=admin.id)
    p1 = (await client.get(f"/admin/services/{svc.id}")).text
    assert p1.count("허용 IP 변경") == 10           # 페이지당 10건만 노출
    assert 'id="list-svc-events"' in p1            # 이벤트 partial 래퍼
    p2 = (await client.get(f"/admin/services/{svc.id}?epage=2")).text
    assert p2.count("허용 IP 변경") == 1            # 나머지 1건은 2페이지


async def test_detail_events_htmx_partial(client, db, redis_client, cipher):
    """이벤트 페이저 클릭(htmx) — base 레이아웃 없이 list-svc-events partial만 응답."""
    from app.services import registry
    svc, _, _ = await create_service(db, cipher, name="evt-htmx-svc",
                                     allowed_ips=["10.0.0.1"])
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    for i in range(2, 13):
        await registry.update_allowed_ips(db, svc.id, [f"10.0.0.{i}"],
                                          actor_user_id=admin.id)
    resp = await client.get(f"/admin/services/{svc.id}?epage=2",
                            headers={"HX-Request": "true",
                                     "HX-Target": "list-svc-events"})
    body = resp.text
    assert "<!doctype" not in body.lower()         # 전체 페이지 아님
    assert 'id="list-svc-events"' in body
    assert body.count("허용 IP 변경") == 1


async def test_keys_modal_shows_keys_and_audits(client, db, redis_client, cipher):
    svc, api_key, secret = await create_service(db, cipher, name="keys-modal-svc")
    await _admin(client, db, redis_client)
    # 상세 페이지에 키 복사 버튼(htmx 모달 로드)
    detail = (await client.get(f"/admin/services/{svc.id}")).text
    assert f'hx-get="/admin/services/{svc.id}/keys-modal"' in detail
    # 모달 fragment: 평문 키 + 복사 버튼
    resp = await client.get(f"/admin/services/{svc.id}/keys-modal")
    modal = resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert api_key in modal and secret in modal
    assert f'data-copy="{api_key}"' in modal
    # 감사 로그 기록
    row = await db.scalar(select(AuditLog).where(
        AuditLog.action == "service.keys_viewed"))
    assert row is not None and str(svc.id) == row.target_id


async def test_keys_modal_legacy_service_without_encrypted_key(client, db,
                                                               redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="keys-legacy-svc")
    svc.api_key_encrypted = None  # 마이그레이션 이전 서비스 시뮬레이션
    await db.commit()
    await _admin(client, db, redis_client)
    modal = (await client.get(f"/admin/services/{svc.id}/keys-modal")).text
    assert "키 재발급 후 복사할 수 있습니다" in modal


async def test_keys_modal_decrypt_failure_shows_error(client, db, redis_client,
                                                      cipher):
    svc, _, _ = await create_service(db, cipher, name="keys-broken-svc")
    svc.api_key_encrypted = "not-a-valid-token"  # 손상된 암호문 시뮬레이션
    await db.commit()
    await _admin(client, db, redis_client)
    resp = await client.get(f"/admin/services/{svc.id}/keys-modal")
    assert resp.status_code == 200
    assert "키 복호화에 실패했습니다" in resp.text


async def test_plan_tables_discount_column_and_tooltips(client, db, redis_client,
                                                        cipher):
    svc, _, _ = await create_service(db, cipher, name="tooltip-svc")
    await create_plan(db, svc, name="tooltip-plan", price=10000,
                      first_payment_type="DISCOUNT_AMOUNT", first_payment_value=1000,
                      recurring_discount_type="DISCOUNT_PERCENT",
                      recurring_discount_value=5)
    await _admin(client, db, redis_client)
    detail = (await client.get(f"/admin/services/{svc.id}")).text
    # 상시할인 컬럼 + 툴팁
    assert "상시할인" in detail
    assert 'title="정가 10,000원 − 첫구독 할인 1,000원 = 9,000원"' in detail
    assert 'title="정가 10,000원 − 상시 할인 5% = 9,500원"' in detail
    # 정기 결제액 셀의 할인 배지(↓) 제거
    assert "%↓" not in detail and "원↓" not in detail
    plans_page = (await client.get("/admin/plans")).text
    assert "상시할인" in plans_page
    assert 'title="정가 10,000원 − 상시 할인 5% = 9,500원"' in plans_page


async def test_plan_table_column_order(client, db, redis_client, cipher):
    """컬럼 순서: 이름|정가|체험|첫구독 할인|첫 결제액|상시할인|정기 결제액|주기|상태."""
    svc, _, _ = await create_service(db, cipher, name="col-order-svc")
    await create_plan(db, svc, name="col-order-plan", price=10000,
                      trial_enabled=True, trial_days=14,
                      first_payment_type="DISCOUNT_AMOUNT", first_payment_value=1000)
    await _admin(client, db, redis_client)
    detail = (await client.get(f"/admin/services/{svc.id}")).text
    thead = detail[detail.index("요금제 관리"):detail.index("</thead>",
                                                       detail.index("요금제 관리"))]
    order = ["이름", "정가", "체험", "첫구독 할인", "첫 결제액",
             "상시할인", "정기 결제액", "주기(반복회차)", "상태"]
    idx = [thead.index(c) for c in order]
    assert idx == sorted(idx), f"컬럼 순서 불일치: {order}"
    # 전역 리스트: 체험 컬럼 + 한글 첫구독 할인 표기
    plans_page = (await client.get("/admin/plans")).text
    assert "체험" in plans_page and "14일" in plans_page
    assert "1,000원 할인" in plans_page
    assert "DISCOUNT_AMOUNT 1000" not in plans_page  # 영문 enum 표기 제거


async def test_detail_overview_has_no_manager_kv_and_shows_primary_badge(
        client, db, redis_client, cipher):
    """개요에서 담당자 kv 제거, 담당자 계정 섹션에 대표 배지."""
    svc, _, _ = await create_service(db, cipher, name="primary-ui-svc",
                                     manager_email="prim@x.com")
    prim, _ = await create_user(db, role="SERVICE_MANAGER", email="prim@x.com",
                                service_id=svc.id)
    other, _ = await create_user(db, role="SERVICE_MANAGER", email="other@x.com",
                                 service_id=svc.id)
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    # 개요 kv 제거 — '담당자' 단독 kv 라벨이 없어야 함(담당자 계정 섹션 제목은 존재)
    assert '<span class="muted">담당자</span>' not in html
    assert "담당자 계정" in html
    assert "대표" in html
    # 대표 행에는 해제(remove) 폼이 없고, 비대표 행에는 대표 지정 버튼이 있다
    assert f"/admin/services/{svc.id}/managers/{prim.id}/remove" not in html
    assert f"/admin/services/{svc.id}/managers/{other.id}/remove" in html
    assert f"/admin/services/{svc.id}/primary-manager" in html


async def test_set_primary_manager_via_post(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="primary-post-svc",
                                     manager_email="p1@x.com")
    p1, _ = await create_user(db, role="SERVICE_MANAGER", email="p1@x.com",
                              service_id=svc.id)
    p2, _ = await create_user(db, role="SERVICE_MANAGER", email="p2@x.com",
                              service_id=svc.id)
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/primary-manager",
                             data={"csrf_token": csrf, "user_id": str(p2.id)})
    assert resp.status_code == 303
    await db.refresh(svc)
    assert svc.manager_email == "p2@x.com"


async def test_remove_primary_manager_blocked(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="primary-block-svc",
                                     manager_email="keep@x.com")
    keep, _ = await create_user(db, role="SERVICE_MANAGER", email="keep@x.com",
                                service_id=svc.id)
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/managers/{keep.id}/remove",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    from urllib.parse import unquote
    assert "대표 담당자는 해제할 수 없습니다" in unquote(resp.headers["location"])
    await db.refresh(keep)
    assert keep.service_id == svc.id  # 해제되지 않음


async def test_service_detail_shows_one_off_payments(client, db, redis_client, cipher):
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc, _, _ = await create_service(db, cipher, name="상세일반결제")
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="oo-u",
                   order_id="det-oo1", amount=5000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="det-oo1", requested_at=utcnow(), approved_at=utcnow()))
    # 타 서비스 결제는 표시되지 않아야 함 (크로스 서비스 격리)
    other, _, _ = await create_service(db, cipher, name="타서비스")
    db.add(Payment(subscription_id=None, service_id=other.id, external_user_id="other-u",
                   order_id="det-other", amount=9000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="det-other", requested_at=utcnow(), approved_at=utcnow()))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "일반결제" in html
    assert "det-oo1" in html and "oo-u" in html
    assert "det-other" not in html  # 타 서비스 결제 미표시
    # 주문번호(행) 클릭 → 결제 상세로 이동하는 링크가 있어야 한다
    p = await db.scalar(select(Payment).where(Payment.order_id == "det-oo1"))
    assert f"/admin/payments/{p.id}" in html


# ─── 취소 정책 카드 분리 (요청 013) ─────────────────────────────────────────

async def test_cancel_policy_separate_card(client, db, redis_client, cipher):
    """취소정책 폼이 허용 IP 카드와 별도 카드로 분리되어 cancel-policy action으로 존재 (요청 013)."""
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    svc, _, _ = await create_service(db, cipher)
    r = await client.get(f"/admin/services/{svc.id}")
    body = r.text
    # 취소정책 폼이 허용 IP 카드와 분리되어 cancel-policy action으로 존재
    assert f"/admin/services/{svc.id}/cancel-policy" in body
    assert "취소 허용" in body


# ─── 취소 정책 (요청 012) ────────────────────────────────────────────────────

async def test_service_create_with_cancel_policy(client, db, redis_client, cipher):
    """서비스 등록 폼에서 취소 정책(수수료율) 지정 → DB 저장 검증 (요청 012)."""
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    csrf = await get_csrf(redis_client, sid)
    # 담당자 계정 없이는 서비스 등록 폼이 비어있음 — 담당자 생성 필요
    from tests.factories import create_user as make_user
    mgr, _ = await make_user(db, role="SERVICE_MANAGER", service_id=None)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf,
        "name": "정책테스트서비스",
        "manager_ids": [str(mgr.id)],
        "primary_user_id": str(mgr.id),
        "allowed_ips": "10.0.0.1",
        "cancellation_enabled": "on",   # 체크박스
        "cancellation_fee_percent": "15",
    })
    # 서비스 등록 성공 — keys.html 렌더(200) 또는 303 리다이렉트
    assert resp.status_code in (200, 303)
    # DB에서 저장 값 확인
    svc = (await db.scalars(select(Service).where(Service.name == "정책테스트서비스"))).first()
    assert svc is not None, "서비스가 생성되지 않았습니다"
    assert svc.cancellation_enabled is True
    assert svc.cancellation_fee_percent == 15


async def test_service_cancel_policy_update_via_form(client, db, redis_client, cipher):
    """서비스 상세 취소 정책 폼 POST → DB 갱신 검증 (요청 012)."""
    svc, _, _ = await create_service(db, cipher, name="cancel-policy-svc")
    # 초기값 확인: enabled=True, fee=0
    assert svc.cancellation_enabled is True
    assert svc.cancellation_fee_percent == 0

    csrf = await _admin(client, db, redis_client)
    # 취소 비허용 + 수수료 20%로 변경
    resp = await client.post(f"/admin/services/{svc.id}/cancel-policy", data={
        "csrf_token": csrf,
        # cancellation_enabled 미전송 → False (체크박스 미체크)
        "cancellation_fee_percent": "20",
    })
    assert resp.status_code == 303
    # DB에서 갱신 값 확인
    await db.refresh(svc)
    assert svc.cancellation_enabled is False
    assert svc.cancellation_fee_percent == 20

    # 다시 취소 허용 + 수수료 5%로 변경
    resp = await client.post(f"/admin/services/{svc.id}/cancel-policy", data={
        "csrf_token": csrf,
        "cancellation_enabled": "on",
        "cancellation_fee_percent": "5",
    })
    assert resp.status_code == 303
    await db.refresh(svc)
    assert svc.cancellation_enabled is True
    assert svc.cancellation_fee_percent == 5


async def test_detail_shows_registered_cards(client, db, redis_client, cipher):
    """등록 카드 섹션 — 이 서비스에 등록된 카드의 사용자·마스킹 번호·발급사·해시 표시.

    FakeTossClient로 카드를 등록하면 card_info={number, issuerCode}가 채워진다.
    타 서비스에 등록된 카드는 이 서비스 상세에 표시되지 않아야 한다(서비스 스코프).
    """
    svc, _, _ = await create_service(db, cipher, name="card-list-svc")
    other, _, _ = await create_service(db, cipher, name="card-other-svc")
    fake = FakeTossClient()
    await create_card(db, fake, cipher, svc, external_user_id="card-user-a")
    await create_card(db, fake, cipher, other, external_user_id="other-card-user")
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "등록 카드" in html                       # 섹션 헤더
    assert 'id="list-svc-cards"' in html              # htmx 대상 컨테이너
    assert "card-user-a" in html                      # 사용자 ID
    assert "1234-****-****-5678" in html              # 마스킹 카드번호(FakeToss)
    assert "other-card-user" not in html              # 타 서비스 카드 미표시


async def test_detail_cards_htmx_partial_and_paging(client, db, redis_client, cipher):
    """등록 카드 페이저 클릭(htmx, kpage) — base 레이아웃 없이 partial만 응답.

    11건 등록 → 10건/페이지로 2페이지 분할. card_info가 없어도 되는 페이징 검증은
    create_card_direct로 빠르게 11건을 심는다.
    """
    svc, _, _ = await create_service(db, cipher, name="card-htmx-svc")
    for i in range(11):
        await create_card_direct(db, cipher, svc, external_user_id=f"ck-{i:02d}",
                                 billing_key=f"bk-card-{i:02d}",
                                 customer_key=f"cust-{i:02d}")
    await _admin(client, db, redis_client)
    resp = await client.get(f"/admin/services/{svc.id}?kpage=2",
                            headers={"HX-Request": "true",
                                     "HX-Target": "list-svc-cards"})
    body = resp.text
    assert "<!doctype" not in body.lower()            # 전체 페이지 아님(partial만)
    assert 'id="list-svc-cards"' in body
    assert "총 11건" in body
    # 셀 기준으로 카운트(클래스명 'block-head'의 'ck-' 오탐 방지) — 2페이지엔 1행만
    assert body.count(">ck-") == 1                    # 11건 중 마지막 1건만 2페이지에
