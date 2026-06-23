import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError, InputValidationError
from app.core.security import sha256_hex
from app.models import AuditLog, Service, User, UserService
from app.services.registry import (
    delete_service,
    register_service,
    rotate_keys,
    set_service_status,
    set_toss_secret_key,
    update_allowed_ips,
)
from tests.factories import create_plan, create_service, create_subscription, create_user


async def _mgr(db, email_addr=None):
    user, _ = await create_user(db, role="SERVICE_MANAGER", service_id=None,
                                email=email_addr)
    return user


async def test_register_service_creates_keys_and_assigns_managers(db, cipher):
    m1 = await _mgr(db, "mgr1@medisolveai.com")
    m2 = await _mgr(db, "mgr2@medisolveai.com")
    creds = await register_service(
        db, cipher, name="mediness", allowed_ips=["10.0.0.1"],
        manager_user_ids=[m1.id, m2.id], primary_user_id=m1.id)
    assert creds.api_key.startswith("svc_")
    assert len(creds.hmac_secret) >= 48

    svc = await db.scalar(select(Service).where(Service.name == "mediness"))
    assert svc.api_key_hash == sha256_hex(creds.api_key)
    assert cipher.decrypt(svc.hmac_secret_encrypted) == creds.hmac_secret
    # 대표 계정 이메일이 알림 수신처
    assert svc.manager_email == "mgr1@medisolveai.com"
    # 두 계정 모두 주 서비스로 할당(둘 다 무서비스였으므로)
    await db.refresh(m1); await db.refresh(m2)
    assert m1.service_id == svc.id and m2.service_id == svc.id


async def test_register_service_existing_primary_service_uses_junction(db, cipher):
    """이미 주 서비스가 있는 계정은 user_services 추가 행으로 할당."""
    other, _, _ = await create_service(db, cipher)
    m = await _mgr(db)
    m.service_id = other.id
    await db.commit()
    creds = await register_service(
        db, cipher, name="junction-svc", allowed_ips=["10.0.0.1"],
        manager_user_ids=[m.id], primary_user_id=m.id)
    await db.refresh(m)
    assert m.service_id == other.id  # 주 서비스 유지
    link = await db.scalar(select(UserService).where(
        UserService.user_id == m.id,
        UserService.service_id == creds.service.id))
    assert link is not None


async def test_register_service_does_not_create_users(db, cipher):
    """계정 자동 생성 없음 — User 수 불변."""
    m = await _mgr(db)
    before = await db.scalar(select(func.count()).select_from(User))
    await register_service(db, cipher, name="no-new-user", allowed_ips=["10.0.0.1"],
                           manager_user_ids=[m.id], primary_user_id=m.id)
    after = await db.scalar(select(func.count()).select_from(User))
    assert after == before


async def test_register_primary_auto_included(db, cipher):
    """대표 계정이 체크 목록에 없으면 자동 포함."""
    m1 = await _mgr(db, "only-checked@x.com")
    m2 = await _mgr(db, "primary-unchecked@x.com")
    creds = await register_service(
        db, cipher, name="auto-include", allowed_ips=["10.0.0.1"],
        manager_user_ids=[m1.id], primary_user_id=m2.id)
    assert creds.service.manager_email == "primary-unchecked@x.com"
    await db.refresh(m2)
    assert m2.service_id == creds.service.id
    await db.refresh(m1)
    assert m1.service_id == creds.service.id  # 체크 목록 계정도 할당


async def test_register_rejects_empty_managers(db, cipher):
    with pytest.raises(InputValidationError):
        await register_service(db, cipher, name="no-mgr", allowed_ips=["10.0.0.1"],
                               manager_user_ids=[], primary_user_id=None)


async def test_register_rejects_unknown_account(db, cipher):
    import uuid as _uuid
    with pytest.raises(InputValidationError):
        await register_service(db, cipher, name="ghost-mgr", allowed_ips=["10.0.0.1"],
                               manager_user_ids=[_uuid.uuid4()],
                               primary_user_id=_uuid.uuid4())


async def test_register_rejects_deleted_account(db, cipher):
    """소프트 삭제된 계정은 담당자로 선택 불가."""
    m, _ = await create_user(db, role="SERVICE_MANAGER", status="DELETED")
    with pytest.raises(InputValidationError):
        await register_service(db, cipher, name="deleted-mgr", allowed_ips=["10.0.0.1"],
                               manager_user_ids=[m.id], primary_user_id=m.id)


async def test_register_rejects_non_manager_role(db, cipher):
    admin, _ = await create_user(db, role="SYSTEM_ADMIN")
    with pytest.raises(InputValidationError):
        await register_service(db, cipher, name="admin-as-mgr",
                               allowed_ips=["10.0.0.1"],
                               manager_user_ids=[admin.id], primary_user_id=admin.id)


async def test_register_duplicate_name_conflicts(db, cipher):
    m = await _mgr(db)
    await register_service(db, cipher, name="dup", allowed_ips=["10.0.0.1"],
                           manager_user_ids=[m.id], primary_user_id=m.id)
    m2 = await _mgr(db)
    with pytest.raises(ConflictError):
        await register_service(db, cipher, name="dup", allowed_ips=["10.0.0.1"],
                               manager_user_ids=[m2.id], primary_user_id=m2.id)


async def test_register_rejects_invalid_ip(db, cipher):
    m = await _mgr(db)
    with pytest.raises(InputValidationError):
        await register_service(db, cipher, name="bad-ip",
                               allowed_ips=["not-an-ip"],
                               manager_user_ids=[m.id], primary_user_id=m.id)


async def test_rotate_keys_invalidates_old(db, cipher):
    m = await _mgr(db)
    creds = await register_service(db, cipher, name="rot", allowed_ips=["10.0.0.1"],
                                   manager_user_ids=[m.id], primary_user_id=m.id)
    new_api_key, new_secret = await rotate_keys(db, cipher, creds.service.id)
    svc = await db.get(Service, creds.service.id)
    assert svc.api_key_hash == sha256_hex(new_api_key)
    assert svc.api_key_hash != sha256_hex(creds.api_key)
    assert cipher.decrypt(svc.hmac_secret_encrypted) == new_secret


async def test_update_allowed_ips(db, cipher):
    m = await _mgr(db)
    creds = await register_service(db, cipher, name="ips", allowed_ips=["10.0.0.1"],
                                   manager_user_ids=[m.id], primary_user_id=m.id)
    await update_allowed_ips(db, creds.service.id, ["10.0.0.2", "10.0.0.3"])
    svc = await db.get(Service, creds.service.id)
    assert svc.allowed_ips == ["10.0.0.2", "10.0.0.3"]


async def test_delete_service_blocked_when_subscription_exists(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)
    with pytest.raises(ConflictError):
        await delete_service(db, svc.id)


async def test_delete_service_without_subscriptions(db, cipher):
    m = await _mgr(db)
    creds = await register_service(db, cipher, name="deletable",
                                   allowed_ips=["10.0.0.1"],
                                   manager_user_ids=[m.id], primary_user_id=m.id)
    await delete_service(db, creds.service.id)
    assert await db.get(Service, creds.service.id) is None


async def test_set_service_status(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    await set_service_status(db, svc.id, "INACTIVE")
    assert (await db.get(Service, svc.id)).status == "INACTIVE"


async def test_register_allows_empty_ip_list(db, cipher):
    """허용 IP 없이도 등록 가능 — 빈 목록 = IP 제한 없음(모든 IP 허용)."""
    m = await _mgr(db)
    creds = await register_service(db, cipher, name="no-ip", allowed_ips=[],
                                   manager_user_ids=[m.id], primary_user_id=m.id)
    assert creds.service.allowed_ips == []


async def test_update_allowed_ips_can_clear_to_empty(db, cipher):
    """등록 후에도 IP 목록을 비워 'IP 제한 없음'으로 되돌릴 수 있다."""
    m = await _mgr(db)
    creds = await register_service(db, cipher, name="clearable", allowed_ips=["10.0.0.1"],
                                   manager_user_ids=[m.id], primary_user_id=m.id)
    svc = await update_allowed_ips(db, creds.service.id, [])
    assert svc.allowed_ips == []


async def test_register_whitespace_duplicate_name_conflicts(db, cipher):
    """공백만 다른 중복 이름도 409 (500 아님)."""
    m = await _mgr(db)
    await register_service(db, cipher, name="trimmed", allowed_ips=["10.0.0.1"],
                           manager_user_ids=[m.id], primary_user_id=m.id)
    m2 = await _mgr(db)
    with pytest.raises(ConflictError):
        await register_service(db, cipher, name="  trimmed  ",
                               allowed_ips=["10.0.0.1"],
                               manager_user_ids=[m2.id], primary_user_id=m2.id)


async def test_delete_service_cascades_manager_user(db, cipher):
    """주 서비스(FK users.service_id CASCADE)로만 연결된 계정은 서비스 삭제 시 함께 삭제."""
    m = await _mgr(db, "cascade@x.com")
    creds = await register_service(db, cipher, name="cascade-svc",
                                   allowed_ips=["10.0.0.1"],
                                   manager_user_ids=[m.id], primary_user_id=m.id)
    await delete_service(db, creds.service.id)
    db.expire_all()
    user = await db.scalar(select(User).where(User.email == "cascade@x.com"))
    assert user is None


async def test_set_primary_manager_updates_manager_email(db, cipher):
    from app.models import AuditLog
    from app.services.registry import set_primary_manager
    m1 = await _mgr(db, "old-primary@x.com")
    m2 = await _mgr(db, "new-primary@x.com")
    creds = await register_service(db, cipher, name="primary-swap",
                                   allowed_ips=["10.0.0.1"],
                                   manager_user_ids=[m1.id, m2.id],
                                   primary_user_id=m1.id)
    await set_primary_manager(db, creds.service.id, user_id=m2.id)
    svc = await db.get(Service, creds.service.id)
    assert svc.manager_email == "new-primary@x.com"
    log = await db.scalar(select(AuditLog).where(
        AuditLog.action == "service.set_primary_manager"))
    assert log is not None


async def test_set_primary_manager_rejects_non_manager_of_service(db, cipher):
    from app.services.registry import set_primary_manager
    m1 = await _mgr(db)
    outsider = await _mgr(db)  # 이 서비스 담당자가 아님
    creds = await register_service(db, cipher, name="primary-guard",
                                   allowed_ips=["10.0.0.1"],
                                   manager_user_ids=[m1.id], primary_user_id=m1.id)
    with pytest.raises(InputValidationError):
        await set_primary_manager(db, creds.service.id, user_id=outsider.id)


# ── toss_secret_key 테스트 3종 ────────────────────────────────────────────────

async def test_register_with_toss_key_encrypts_and_audits(db, cipher):
    """등록 시 toss_secret_key를 전달하면 암호화 저장되고 감사로그가 기록된다.
    감사로그 어디에도 평문 시크릿이 남지 않아야 한다.
    """
    m = await _mgr(db)
    creds = await register_service(
        db, cipher, name="svc-toss", allowed_ips=[],
        manager_user_ids=[m.id], primary_user_id=m.id,
        toss_secret_key="sk_test_LIVE",
    )
    svc = creds.service
    # 암호화된 값이 저장됨
    assert svc.toss_secret_key_encrypted
    # 복호화하면 원본과 일치해야 함
    assert cipher.decrypt(svc.toss_secret_key_encrypted) == "sk_test_LIVE"
    # 감사로그에 set 액션이 기록되어 있어야 함
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.target_id == str(svc.id))
    )).scalars().all()
    assert any(r.action == "service.toss_secret_key.set" for r in rows)
    # 평문 시크릿이 감사로그 detail에 포함되어서는 안 됨
    assert all("sk_test_LIVE" not in (str(r.detail) or "") for r in rows)


async def test_set_toss_secret_key_set_then_change(db, cipher):
    """최초 설정은 'set', 이후 교체는 'changed' 감사 액션으로 기록된다.
    감사로그 어디에도 평문 시크릿이 남지 않아야 한다.
    """
    m = await _mgr(db)
    creds = await register_service(
        db, cipher, name="svc2-toss", allowed_ips=[],
        manager_user_ids=[m.id], primary_user_id=m.id,
    )
    sid = creds.service.id
    # 최초 설정 → action: set
    await set_toss_secret_key(db, cipher, service_id=sid, toss_secret_key="sk_1")
    # 교체 → action: changed
    await set_toss_secret_key(db, cipher, service_id=sid, toss_secret_key="sk_2")
    svc = await db.get(Service, sid)
    # 최종 키는 sk_2 로 교체되어 있어야 함
    assert cipher.decrypt(svc.toss_secret_key_encrypted) == "sk_2"
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.target_id == str(sid))
    )).scalars().all()
    actions = [r.action for r in rows]
    assert "service.toss_secret_key.set" in actions      # 최초 설정
    assert "service.toss_secret_key.changed" in actions  # 교체
    # 평문 시크릿이 감사로그 detail에 포함되어서는 안 됨
    assert all("sk_1" not in str(r.detail) and "sk_2" not in str(r.detail) for r in rows)


async def test_set_toss_secret_key_rejects_empty(db, cipher):
    """빈 문자열(공백 포함)을 toss_secret_key로 전달하면 InputValidationError를 발생시킨다."""
    m = await _mgr(db)
    creds = await register_service(
        db, cipher, name="svc3-toss", allowed_ips=[],
        manager_user_ids=[m.id], primary_user_id=m.id,
    )
    with pytest.raises(InputValidationError):
        await set_toss_secret_key(db, cipher, service_id=creds.service.id, toss_secret_key="  ")
