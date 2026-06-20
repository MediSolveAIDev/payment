"""결제서버 킬스위치 게이트 통합 테스트 (요청 013).

ensure_server_enabled 게이트 헬퍼가 GlobalSettings.server_disabled=True 일 때
ServerDisabledError(http_status=503)를 발생시키고, 비활성화 사유를 메시지로
전달하는지 검증한다.
"""
import pytest

from app.core.errors import ServerDisabledError
from app.services import app_settings


async def test_ensure_server_enabled_raises_when_disabled(db):
    """server_disabled=True 로 설정하면 ensure_server_enabled 가 ServerDisabledError 를 발생시킨다.

    - http_status 는 503 이어야 한다.
    - 예외 메시지에 disabled_reason 이 포함되어야 한다.
    """
    gs = await app_settings.get_global_settings(db)
    gs.server_disabled = True
    gs.disabled_reason = "정기 점검"
    await db.commit()

    with pytest.raises(ServerDisabledError) as exc_info:
        await app_settings.ensure_server_enabled(db)

    err = exc_info.value
    assert err.http_status == 503
    assert err.code == "SERVER_DISABLED"
    assert "점검" in str(err)   # disabled_reason 이 메시지에 포함


async def test_ensure_server_enabled_passes_when_active(db):
    """server_disabled=False(기본값)이면 ensure_server_enabled 가 예외 없이 반환한다."""
    # GlobalSettings 기본값은 server_disabled=False
    await app_settings.ensure_server_enabled(db)   # 예외 없음


async def test_ensure_server_enabled_uses_default_message_when_no_reason(db):
    """disabled_reason 이 없으면 기본 안내 문구가 메시지로 사용된다."""
    gs = await app_settings.get_global_settings(db)
    gs.server_disabled = True
    gs.disabled_reason = None   # 사유 없음
    await db.commit()

    with pytest.raises(ServerDisabledError) as exc_info:
        await app_settings.ensure_server_enabled(db)

    assert "점검" in str(exc_info.value)   # 기본 안내 문구("서비스 점검 중입니다")에 포함


async def test_ensure_server_enabled_caches_in_redis(db, redis_client):
    """[감사 Phase 3 — 성능 M4] redis 전달 시 결과를 짧은 TTL로 캐시한다.

    캐시 인코딩: ""(빈 문자열)=활성, 비어있지 않으면 비활성 사유.
    캐시 적중 시 DB를 보지 않는 것을 검증하기 위해 캐시에 사유를 직접 주입한다.
    """
    # 첫 호출 — DB 조회 후 활성("")을 캐시
    await app_settings.ensure_server_enabled(db, redis_client)
    assert await redis_client.get(app_settings.SERVER_DISABLED_CACHE_KEY) == ""
    # 캐시에 비활성 사유 주입 — DB(활성)와 무관하게 캐시 경로로 차단되어야 함
    await redis_client.set(app_settings.SERVER_DISABLED_CACHE_KEY, "캐시 점검", ex=5)
    with pytest.raises(ServerDisabledError) as exc:
        await app_settings.ensure_server_enabled(db, redis_client)
    assert "캐시 점검" in exc.value.message


async def test_set_server_disabled_invalidates_cache(db, redis_client):
    """[감사 Phase 3 — 성능 M4] 킬스위치 전환 시 캐시를 즉시 무효화한다.

    무효화가 없으면 전환 후에도 TTL(5초) 동안 이전 상태가 응답될 수 있다.
    """
    from tests.factories import create_user
    user, pw = await create_user(db)
    # 활성 상태를 캐시에 적재
    await app_settings.ensure_server_enabled(db, redis_client)
    assert await redis_client.get(app_settings.SERVER_DISABLED_CACHE_KEY) == ""
    # 킬스위치 ON(redis 전달) → 캐시 삭제 → 다음 호출이 DB에서 새 상태로 차단
    await app_settings.set_server_disabled(
        db, disabled=True, reason="점검", actor_user=user, password=pw,
        redis=redis_client)
    assert await redis_client.get(app_settings.SERVER_DISABLED_CACHE_KEY) is None
    with pytest.raises(ServerDisabledError):
        await app_settings.ensure_server_enabled(db, redis_client)
