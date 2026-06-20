"""전역설정(GlobalSettings) 헬퍼 통합 테스트 (요청 013)."""
import pytest
from app.core.errors import AuthenticationError, InputValidationError
from app.services import app_settings
from tests.factories import create_user


async def test_get_global_settings_creates_singleton(db):
    """get_global_settings가 최초 호출 시 기본값 행(id=1)을 생성하고,
    재호출 시 동일 행(id=1)을 반환하는 싱글톤 동작을 검증한다."""
    gs = await app_settings.get_global_settings(db)
    assert gs.id == 1 and gs.retry_limit == 4 and gs.server_disabled is False
    gs2 = await app_settings.get_global_settings(db)
    assert gs2.id == 1   # 같은 단일 행


async def test_update_retry_settings(db):
    """update_retry_settings가 DB의 GlobalSettings 행을 올바르게 갱신함을 검증한다."""
    u, _ = await create_user(db, role="SYSTEM_ADMIN")
    gs = await app_settings.update_retry_settings(
        db, retry_limit=6, retry_interval_hours=6, suspended_grace_days=14,
        actor_user_id=u.id)
    assert gs.retry_limit == 6 and gs.retry_interval_hours == 6
    assert gs.suspended_grace_days == 14


async def test_update_admin_ips_requires_current_ip(db):
    """어드민 IP 변경 시 현재 접속 IP가 목록에 없으면 InputValidationError를 발생시키고,
    목록에 포함하면 정상 저장됨을 검증한다(lockout 방지)."""
    u, _ = await create_user(db, role="SYSTEM_ADMIN")
    with pytest.raises(InputValidationError):
        await app_settings.update_admin_ips(
            db, ips=["10.0.0.1"], current_ip="192.168.0.9", actor_user_id=u.id)
    gs = await app_settings.update_admin_ips(
        db, ips=["10.0.0.1", "192.168.0.9"], current_ip="192.168.0.9", actor_user_id=u.id)
    assert "192.168.0.9" in gs.admin_allowed_ips


async def test_set_server_disabled_password(db):
    """set_server_disabled가 잘못된 비밀번호에 AuthenticationError를 발생시키고,
    올바른 비밀번호로는 킬스위치를 ON/reason을 정상 저장함을 검증한다."""
    u, pw = await create_user(db, role="SYSTEM_ADMIN")
    with pytest.raises(AuthenticationError):
        await app_settings.set_server_disabled(
            db, disabled=True, reason="점검", actor_user=u, password="wrong")
    gs = await app_settings.set_server_disabled(
        db, disabled=True, reason="점검", actor_user=u, password=pw)
    assert gs.server_disabled is True and gs.disabled_reason == "점검"


async def test_set_server_disabled_reason_required(db):
    """disabled=True인데 사유가 없으면 InputValidationError를 발생시킴을 검증한다."""
    u, pw = await create_user(db, role="SYSTEM_ADMIN")
    with pytest.raises(InputValidationError):
        await app_settings.set_server_disabled(
            db, disabled=True, reason=None, actor_user=u, password=pw)
    with pytest.raises(InputValidationError):
        await app_settings.set_server_disabled(
            db, disabled=True, reason="   ", actor_user=u, password=pw)
