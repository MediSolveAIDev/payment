import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.models import PasswordSetupToken, User, UserService
from app.notifications.email import RecordingEmailSender
from app.services import accounts
from tests.factories import create_service, create_user


@pytest.fixture
def email():
    return RecordingEmailSender()


async def test_create_manager_account_assigns_services_and_emails(db, cipher, email):
    svc1, _, _ = await create_service(db, cipher, name="acc-a")
    svc2, _, _ = await create_service(db, cipher, name="acc-b")
    user, _ = await accounts.create_account(
        db, email, email="mgr@x.com", role="SERVICE_MANAGER",
        service_ids=[svc1.id, svc2.id], base_url="http://x")
    assert user.role == "SERVICE_MANAGER"
    assert user.status == "PENDING"
    # 주 서비스 = 첫 서비스, 나머지는 junction
    assert user.service_id == svc1.id
    links = (await db.scalars(select(UserService).where(UserService.user_id == user.id))).all()
    assert {l.service_id for l in links} == {svc2.id}
    # 유효 스코프 = 합집합
    ids = await accounts.effective_service_ids(db, user)
    assert set(ids) == {svc1.id, svc2.id}
    # 설정 메일 + 토큰
    assert len(email.sent) == 1
    assert await db.scalar(select(PasswordSetupToken).where(
        PasswordSetupToken.user_id == user.id)) is not None


async def test_create_admin_account_no_services(db, email):
    user, _ = await accounts.create_account(
        db, email, email="root2@x.com", role="SYSTEM_ADMIN",
        service_ids=[], base_url="http://x")
    assert user.role == "SYSTEM_ADMIN"
    assert user.service_id is None
    # 시스템 관리자는 스코프 제한 없음(None)
    assert await accounts.effective_service_ids(db, user) is None


async def test_create_manager_without_service_allowed(db, email):
    """서비스 0개 생성 허용 — 서비스는 서비스 등록 시 할당(요청 007)."""
    user, _ = await accounts.create_account(db, email, email="m2@x.com",
                                            role="SERVICE_MANAGER", service_ids=[],
                                            base_url="")
    assert user.role == "SERVICE_MANAGER"
    assert user.service_id is None
    assert await accounts.effective_service_ids(db, user) == []


async def test_create_duplicate_email_conflicts(db, email):
    await accounts.create_account(db, email, email="dup@x.com",
                                  role="SYSTEM_ADMIN", service_ids=[], base_url="")
    with pytest.raises(ConflictError):
        await accounts.create_account(db, email, email="dup@x.com",
                                      role="SYSTEM_ADMIN", service_ids=[], base_url="")


async def test_assign_and_unassign_service(db, cipher, email):
    svc1, _, _ = await create_service(db, cipher, name="asn-a")
    svc2, _, _ = await create_service(db, cipher, name="asn-b")
    user, _ = await create_user(db, role="SERVICE_MANAGER", service_id=svc1.id)
    await accounts.assign_service(db, user_id=user.id, service_id=svc2.id)
    ids = await accounts.effective_service_ids(db, await db.get(User, user.id))
    assert set(ids) == {svc1.id, svc2.id}
    # 중복 할당은 무시(에러 없음)
    await accounts.assign_service(db, user_id=user.id, service_id=svc2.id)
    # 해제
    await accounts.unassign_service(db, user_id=user.id, service_id=svc2.id)
    ids = await accounts.effective_service_ids(db, await db.get(User, user.id))
    assert set(ids) == {svc1.id}


async def test_assign_to_admin_rejected(db, cipher, email):
    svc, _, _ = await create_service(db, cipher)
    admin, _ = await create_user(db, role="SYSTEM_ADMIN")
    with pytest.raises(InputValidationError):
        await accounts.assign_service(db, user_id=admin.id, service_id=svc.id)


async def test_unassign_primary_promotes_or_clears(db, cipher, email):
    """주 서비스를 해제하면 junction의 다른 서비스가 주 서비스로 승격."""
    svc1, _, _ = await create_service(db, cipher, name="pr-a")
    svc2, _, _ = await create_service(db, cipher, name="pr-b")
    user, _ = await create_user(db, role="SERVICE_MANAGER", service_id=svc1.id)
    await accounts.assign_service(db, user_id=user.id, service_id=svc2.id)
    await accounts.unassign_service(db, user_id=user.id, service_id=svc1.id)
    refreshed = await db.get(User, user.id)
    await db.refresh(refreshed)
    assert refreshed.service_id == svc2.id  # 승격
    ids = await accounts.effective_service_ids(db, refreshed)
    assert set(ids) == {svc2.id}


async def test_create_account_with_phone(db, cipher, email):
    svc, _, _ = await create_service(db, cipher, name="ph-a")
    user, _ = await accounts.create_account(
        db, email, email="phone@x.com", role="SERVICE_MANAGER",
        service_ids=[svc.id], base_url="http://x", phone="010-1234-5678")
    assert user.phone == "010-1234-5678"


async def test_create_account_invalid_phone_rejected(db, email):
    with pytest.raises(InputValidationError):
        await accounts.create_account(db, email, email="bp@x.com", role="SYSTEM_ADMIN",
                                      service_ids=[], base_url="", phone="abc!!")


async def test_update_account_email_dup_check(db, email):
    a, _ = await accounts.create_account(db, email, email="a-dup@x.com", role="SYSTEM_ADMIN",
                                         service_ids=[], base_url="")
    await accounts.create_account(db, email, email="b-dup@x.com", role="SYSTEM_ADMIN",
                                  service_ids=[], base_url="")
    # b가 쓰는 이메일로 a를 바꾸면 충돌
    with pytest.raises(ConflictError):
        await accounts.update_account(db, user_id=a.id, email="b-dup@x.com")
    # 같은 이메일(자기 자신) 유지는 허용 + 전화번호 수정
    updated = await accounts.update_account(db, user_id=a.id, email="a-dup@x.com",
                                            phone="010-9999-0000")
    assert updated.email == "a-dup@x.com" and updated.phone == "010-9999-0000"


async def test_disable_and_enable_account(db, email):
    u, _ = await accounts.create_account(db, email, email="dis@x.com", role="SYSTEM_ADMIN",
                                         service_ids=[], base_url="")
    u.password_hash = "x"  # 비밀번호 설정된 상태로 가정
    await db.commit()
    await accounts.set_account_disabled(db, None, user_id=u.id, disabled=True)
    assert (await db.get(User, u.id)).status == "DISABLED"
    await accounts.set_account_disabled(db, None, user_id=u.id, disabled=False)
    assert (await db.get(User, u.id)).status == "ACTIVE"  # 비밀번호 있으면 ACTIVE


async def test_enable_account_without_password_is_pending(db, email):
    u, _ = await accounts.create_account(db, email, email="np@x.com", role="SYSTEM_ADMIN",
                                         service_ids=[], base_url="")  # PENDING, no pw
    await accounts.set_account_disabled(db, None, user_id=u.id, disabled=True)
    await accounts.set_account_disabled(db, None, user_id=u.id, disabled=False)
    assert (await db.get(User, u.id)).status == "PENDING"


async def test_soft_delete_account(db, cipher, email):
    svc, _, _ = await create_service(db, cipher, name="del-a")
    u, _ = await accounts.create_account(db, email, email="del@x.com", role="SERVICE_MANAGER",
                                         service_ids=[svc.id], base_url="")
    await accounts.delete_account(db, None, user_id=u.id)
    refreshed = await db.get(User, u.id)
    assert refreshed.status == "DELETED" and refreshed.service_id is None
    # 담당 서비스 링크 제거
    links = (await db.scalars(select(UserService).where(UserService.user_id == u.id))).all()
    assert links == []
    # 삭제된 계정은 수정 불가(찾을 수 없음)
    with pytest.raises(NotFoundError):
        await accounts.update_account(db, user_id=u.id, phone="010-0000-0000")


async def test_update_account_email_syncs_primary_manager_email(db, cipher, email):
    """대표 담당자의 이메일 변경 시 Service.manager_email 동기 갱신(요청 007)."""
    svc, _, _ = await create_service(db, cipher, manager_email="sync-old@x.com")
    user, _ = await create_user(db, role="SERVICE_MANAGER", email="sync-old@x.com",
                                service_id=svc.id)
    await accounts.update_account(db, user_id=user.id, email="sync-new@x.com")
    await db.refresh(svc)
    assert svc.manager_email == "sync-new@x.com"


async def test_delete_account_blocked_when_primary_manager(db, cipher, email):
    """대표 담당자(Service.manager_email)인 계정은 삭제 불가 — 먼저 대표 변경 필요."""
    svc, _, _ = await create_service(db, cipher, manager_email="primary-del@x.com")
    user, _ = await create_user(db, role="SERVICE_MANAGER", email="primary-del@x.com",
                                service_id=svc.id)
    admin, _ = await create_user(db, role="SYSTEM_ADMIN")
    with pytest.raises(InputValidationError):
        await accounts.delete_account(db, None, user_id=user.id,
                                      actor_user_id=admin.id)
