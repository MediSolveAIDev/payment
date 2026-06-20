from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.errors import AuthenticationError, InputValidationError
from app.core.security import sha256_hex, verify_password
from app.models import PasswordSetupToken, User
from app.services import auth
from tests.factories import create_user


async def test_login_success_creates_redis_session(db, redis_client, settings):
    user, password = await create_user(db, role="SYSTEM_ADMIN")
    session_id, logged_in = await auth.login(
        db, redis_client, settings, email=user.email, password=password, ip="127.0.0.1")
    assert logged_in.id == user.id
    data = await auth.get_session(redis_client, settings, session_id)
    assert data["user_id"] == str(user.id)
    assert data["role"] == "SYSTEM_ADMIN"
    assert len(data["csrf_token"]) > 20


async def test_login_wrong_password_generic_error(db, redis_client, settings):
    user, _ = await create_user(db)
    with pytest.raises(AuthenticationError):
        await auth.login(db, redis_client, settings,
                         email=user.email, password="wrong", ip="127.0.0.1")


async def test_login_unknown_email_same_error_shape(db, redis_client, settings):
    with pytest.raises(AuthenticationError):
        await auth.login(db, redis_client, settings,
                         email="ghost@x.com", password="x", ip="127.0.0.1")


async def test_lockout_after_5_failures(db, redis_client, settings):
    user, password = await create_user(db)
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            await auth.login(db, redis_client, settings,
                             email=user.email, password="wrong", ip="127.0.0.1")
    refreshed = await db.get(User, user.id)
    await db.refresh(refreshed)
    assert refreshed.status == "LOCKED"
    # 잠금 중엔 올바른 비밀번호도 거부
    with pytest.raises(AuthenticationError):
        await auth.login(db, redis_client, settings,
                         email=user.email, password=password, ip="127.0.0.1")


async def test_lockout_threshold_is_runtime_configurable(db, redis_client, settings):
    """잠금 임계치(max_failed_logins)가 런타임(GlobalSettings)에서 즉시 조정됨.

    전체 설정에서 임계치를 2회로 낮추면, login이 그 값을 읽어 2회 실패 만에 LOCKED가 된다.
    (.env가 아닌 DB 전역설정이 런타임 권위 — 재배포 없이 적용)
    """
    from app.services.app_settings import update_security_policy
    user, password = await create_user(db)
    await update_security_policy(db, max_failed_logins=2, account_lock_minutes=7,
                                 one_off_max_amount=100_000_000, actor_user_id=user.id)
    for _ in range(2):
        with pytest.raises(AuthenticationError):
            await auth.login(db, redis_client, settings,
                             email=user.email, password="wrong", ip="127.0.0.1")
    refreshed = await db.get(User, user.id)
    await db.refresh(refreshed)
    assert refreshed.status == "LOCKED"  # 5회가 아니라 2회 만에 잠금


async def test_lock_expires_and_allows_login(db, redis_client, settings):
    user, password = await create_user(db, status="LOCKED")
    user.locked_until = utcnow() - timedelta(minutes=1)  # 이미 만료된 잠금
    await db.commit()
    session_id, _ = await auth.login(db, redis_client, settings,
                                     email=user.email, password=password, ip="127.0.0.1")
    assert session_id


async def test_pending_user_cannot_login(db, redis_client, settings):
    user, password = await create_user(db, status="PENDING")
    with pytest.raises(AuthenticationError):
        await auth.login(db, redis_client, settings,
                         email=user.email, password=password, ip="127.0.0.1")


async def test_logout_destroys_session(db, redis_client, settings):
    user, password = await create_user(db)
    session_id, _ = await auth.login(db, redis_client, settings,
                                     email=user.email, password=password, ip="127.0.0.1")
    await auth.logout(redis_client, session_id)
    assert await auth.get_session(redis_client, settings, session_id) is None


async def test_setup_password_with_valid_token(db):
    user, _ = await create_user(db, status="PENDING")
    token = "tok-" + "a" * 30
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()
    await auth.setup_password(db, token=token, password="NewPassword123!")
    await db.refresh(user)
    assert user.status == "ACTIVE"
    assert verify_password("NewPassword123!", user.password_hash)
    # 토큰 재사용 불가
    with pytest.raises(InputValidationError):
        await auth.setup_password(db, token=token, password="Another123!")


async def test_setup_password_rejects_expired_token(db):
    user, _ = await create_user(db, status="PENDING")
    token = "tok-" + "b" * 30
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() - timedelta(hours=1)))
    await db.commit()
    with pytest.raises(InputValidationError):
        await auth.setup_password(db, token=token, password="NewPassword123!")


async def test_setup_password_rejects_weak_password(db):
    user, _ = await create_user(db, status="PENDING")
    token = "tok-" + "c" * 30
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()
    with pytest.raises(InputValidationError):
        await auth.setup_password(db, token=token, password="short")


async def test_session_key_has_ttl(db, redis_client, settings):
    """hset/expire 원자성 — TTL 없는 불멸 세션이 생기지 않아야 한다."""
    user, password = await create_user(db)
    session_id, _ = await auth.login(db, redis_client, settings,
                                     email=user.email, password=password, ip="127.0.0.1")
    ttl = await redis_client.ttl(f"session:{session_id}")
    assert 0 < ttl <= settings.session_ttl_seconds


async def test_unknown_email_attempt_is_audited(db, redis_client, settings):
    from app.models import AuditLog
    with pytest.raises(AuthenticationError):
        await auth.login(db, redis_client, settings,
                         email="probe@nowhere.com", password="x", ip="9.9.9.9")
    row = await db.scalar(select(AuditLog).where(
        AuditLog.action == "auth.login_failed"))
    assert row is not None
    assert row.detail["reason"] == "unknown_email"


async def test_password_change_destroys_sessions_and_other_tokens(db, redis_client, settings):
    """비밀번호 설정 시 기존 세션 전부 파기 + 다른 미사용 토큰 무효화."""
    user, password = await create_user(db, status="ACTIVE")
    session_id, _ = await auth.login(db, redis_client, settings,
                                     email=user.email, password=password, ip="127.0.0.1")
    token_a = "tok-multi-" + "a" * 22
    token_b = "tok-multi-" + "b" * 22
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token_a),
                              expires_at=utcnow() + timedelta(hours=1)))
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token_b),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()

    await auth.setup_password(db, token=token_a, password="BrandNewPassword1",
                              redis=redis_client)
    # 기존 세션 파기
    assert await auth.get_session(redis_client, settings, session_id) is None
    # 남은 토큰도 무효화
    with pytest.raises(InputValidationError):
        await auth.setup_password(db, token=token_b, password="AnotherPassword1")


async def test_login_rejected_for_disabled_account(db, redis_client, settings):
    from app.core.errors import AuthenticationError
    from app.services import auth as auth_service
    from tests.factories import create_user
    user, pw = await create_user(db, status="DISABLED")
    with pytest.raises(AuthenticationError, match="비활성화"):
        await auth_service.login(db, redis_client, settings,
                                 email=user.email, password=pw, ip="1.1.1.1")


async def test_login_rejected_for_deleted_account(db, redis_client, settings):
    from app.core.errors import AuthenticationError
    from app.services import auth as auth_service
    from tests.factories import create_user
    user, pw = await create_user(db, status="DELETED")
    with pytest.raises(AuthenticationError):
        await auth_service.login(db, redis_client, settings,
                                 email=user.email, password=pw, ip="1.1.1.1")
