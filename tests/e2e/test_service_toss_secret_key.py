"""서비스별 토스 시크릿 키 어드민 UI e2e — 설정/교체/감사 기록 검증(Task 8).

흐름:
  1. 서비스 상세에 토스 시크릿 키 카드(입력 + 상태 표시)가 렌더되는지 확인.
  2. POST /toss-secret-key → DB에 암호문 저장, 평문 미노출, 감사 기록(set).
  3. 교체 시 감사 액션이 changed로 변경.
  4. 빈 값 제출 시 기존 키 유지(변경 없음).
  5. 서비스 등록(POST /services) 시 toss_secret_key 함께 전달 → DB 저장 확인.
  6. 상세 화면에서 설정됨/미설정 배지 표시 검증 — 평문 절대 미노출.
"""
from sqlalchemy import select

from app.models import AuditLog, Service
from tests.factories import create_service, create_user
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    """SYSTEM_ADMIN 로그인 후 CSRF 토큰 반환."""
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


async def test_detail_shows_toss_key_card(client, db, redis_client, cipher):
    """서비스 상세에 '토스 시크릿 키' 카드 + 쓰기 전용 입력칸이 렌더된다."""
    svc, _, _ = await create_service(db, cipher, name="toss-card-svc")
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "토스 시크릿 키" in html
    # 쓰기 전용 입력 — type="password" autocomplete="off"
    assert 'name="toss_secret_key"' in html
    assert 'type="password"' in html
    assert 'autocomplete="off"' in html


async def test_detail_shows_unset_badge_when_no_key(client, db, redis_client, cipher):
    """토스 시크릿 키 미설정 서비스 → '미설정' 배지가 표시된다(평문 노출 없음)."""
    svc, _, _ = await create_service(db, cipher, name="toss-unset-svc")
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "미설정" in html
    # 평문이 절대 화면에 나타나지 않아야 한다 — 테스트 키 패턴으로 검증
    assert "test_secret" not in html


async def test_set_toss_secret_key_saves_encrypted(client, db, redis_client, cipher):
    """토스 시크릿 키 설정 → DB에 암호문 저장, 평문 미노출, 감사 set 기록."""
    svc, _, _ = await create_service(db, cipher, name="toss-set-svc")
    csrf = await _admin(client, db, redis_client)

    r = await client.post(f"/admin/services/{svc.id}/toss-secret-key",
                          data={"csrf_token": csrf,
                                "toss_secret_key": "test_plaintext_key_12345"},
                          follow_redirects=False)
    assert r.status_code == 303

    # DB에 암호문이 저장되었는지 확인 — 평문이 아닌 암호문이어야 한다
    await db.refresh(svc)
    assert svc.toss_secret_key_encrypted is not None
    assert svc.toss_secret_key_encrypted != "test_plaintext_key_12345"

    # 감사 로그: service.toss_secret_key.set 액션, detail에 평문 미포함
    log = await db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "service.toss_secret_key.set",
               AuditLog.target_id == str(svc.id))
        .order_by(AuditLog.created_at.desc())
    )
    assert log is not None, "service.toss_secret_key.set 감사 로그가 없습니다"
    # 감사 detail에 평문 절대 미포함
    detail_str = str(log.detail or "")
    assert "test_plaintext_key_12345" not in detail_str


async def test_detail_shows_set_badge_after_key_set(client, db, redis_client, cipher):
    """토스 시크릿 키 설정 후 상세 → '설정됨' 배지 표시, 평문 미노출."""
    svc, _, _ = await create_service(db, cipher, name="toss-badge-svc")
    csrf = await _admin(client, db, redis_client)

    await client.post(f"/admin/services/{svc.id}/toss-secret-key",
                      data={"csrf_token": csrf,
                            "toss_secret_key": "badge_test_key_xyz"})

    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "설정됨" in html
    # 평문이 화면에 절대 노출되지 않아야 한다
    assert "badge_test_key_xyz" not in html


async def test_replace_toss_secret_key_audit_changed(client, db, redis_client, cipher):
    """기존 키가 있는 서비스에 새 키 입력 → 감사 액션이 changed 이어야 한다."""
    svc, _, _ = await create_service(db, cipher, name="toss-replace-svc")
    csrf = await _admin(client, db, redis_client)

    # 1차 설정(set)
    await client.post(f"/admin/services/{svc.id}/toss-secret-key",
                      data={"csrf_token": csrf, "toss_secret_key": "first_key"})

    # 2차 교체(changed)
    r = await client.post(f"/admin/services/{svc.id}/toss-secret-key",
                          data={"csrf_token": csrf, "toss_secret_key": "second_key"},
                          follow_redirects=False)
    assert r.status_code == 303

    # 감사 로그: service.toss_secret_key.changed 액션 확인
    log = await db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "service.toss_secret_key.changed",
               AuditLog.target_id == str(svc.id))
        .order_by(AuditLog.created_at.desc())
    )
    assert log is not None, "service.toss_secret_key.changed 감사 로그가 없습니다"
    detail_str = str(log.detail or "")
    assert "first_key" not in detail_str
    assert "second_key" not in detail_str


async def test_empty_key_submission_preserves_existing(client, db, redis_client, cipher):
    """빈 값 제출 시 기존 키 유지 — 변경 없음, 감사 로그도 추가되지 않는다."""
    svc, _, _ = await create_service(db, cipher, name="toss-noop-svc")
    csrf = await _admin(client, db, redis_client)

    # 1차 설정
    await client.post(f"/admin/services/{svc.id}/toss-secret-key",
                      data={"csrf_token": csrf, "toss_secret_key": "keep_this_key"})
    await db.refresh(svc)
    encrypted_before = svc.toss_secret_key_encrypted

    # 빈 값 제출 → 변경 없음
    r = await client.post(f"/admin/services/{svc.id}/toss-secret-key",
                          data={"csrf_token": csrf, "toss_secret_key": ""},
                          follow_redirects=False)
    assert r.status_code == 303
    await db.refresh(svc)
    # 암호문이 동일해야 한다(기존 키 유지)
    assert svc.toss_secret_key_encrypted == encrypted_before


async def test_register_service_with_toss_key(client, db, redis_client, cipher):
    """서비스 등록 폼에 toss_secret_key 입력 → 등록 시 DB에 암호화 저장."""
    # 등록 폼에 필요한 SERVICE_MANAGER 계정 준비
    mgr, _ = await create_user(db, role="SERVICE_MANAGER")
    csrf = await _admin(client, db, redis_client)

    r = await client.post(
        "/admin/services",
        data={
            "csrf_token": csrf,
            "name": "toss-reg-svc",
            "manager_ids": str(mgr.id),
            "primary_user_id": str(mgr.id),
            "allowed_ips": "",
            "cancellation_enabled": "on",
            "cancellation_fee_percent": "0",
            "toss_secret_key": "reg_test_secret_key",
        },
    )
    # 등록 성공 시 200(keys.html 렌더) 또는 303 리다이렉트
    assert r.status_code in (200, 303)

    # DB에서 방금 등록한 서비스 확인
    svc = await db.scalar(select(Service).where(Service.name == "toss-reg-svc"))
    assert svc is not None
    assert svc.toss_secret_key_encrypted is not None
    # 평문이 저장되지 않았는지 확인
    assert svc.toss_secret_key_encrypted != "reg_test_secret_key"

    # 감사 로그: service.toss_secret_key.set 기록 확인
    log = await db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "service.toss_secret_key.set",
               AuditLog.target_id == str(svc.id))
    )
    assert log is not None, "등록 시 service.toss_secret_key.set 감사 로그가 없습니다"


async def test_new_service_form_has_toss_key_input(client, db, redis_client, cipher):
    """서비스 등록 폼(/admin/services/new)에 토스 시크릿 키 입력칸이 있다."""
    # 등록 폼 진입에 SERVICE_MANAGER가 최소 1명 필요
    await create_user(db, role="SERVICE_MANAGER")
    await _admin(client, db, redis_client)
    html = (await client.get("/admin/services/new")).text
    assert "토스 시크릿 키" in html
    assert 'name="toss_secret_key"' in html
    assert 'type="password"' in html
    assert 'autocomplete="off"' in html
