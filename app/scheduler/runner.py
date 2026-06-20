"""구독 갱신 배치 스케줄러 모듈.

APScheduler(AsyncIOScheduler)를 사용해 일정 주기(기본 5분)마다
``process_due``를 호출해 만료된 구독을 자동 갱신·결제한다.

다중 인스턴스(수평 확장) 환경에서 중복 실행을 막기 위해 Redis SET NX 락을
전역으로 사용하며, 구독 단위 락과 토스 멱등키가 2차 방어선 역할을 한다.
"""

import asyncio
import contextlib
import logging
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.core.config import default_settings
from app.services.renewals import process_due

logger = logging.getLogger("payment.scheduler")

# Redis 전역 락 키 — 배치가 실행 중임을 나타낸다.
GLOBAL_LOCK_KEY = "lock:scheduler:renewals"
# 락 TTL(초). 배치 진행 중에는 아래 heartbeat가 주기적으로 TTL을 연장하므로
# (감사 Phase 1 — 성능 H2) 배치가 TTL보다 오래 걸려도 락이 만료돼 다른
# 인스턴스와 중첩 실행되지 않는다. TTL은 "heartbeat가 멈춘 뒤(프로세스 사망 등)
# 락이 자연 해소되기까지의 시간"으로만 작동한다 — 데드맨 스위치.
# .env(scheduler_lock_ttl_seconds)로 조정 가능.
GLOBAL_LOCK_TTL = default_settings().scheduler_lock_ttl_seconds
# heartbeat 주기(초) — TTL의 1/3로 잡아 네트워크 지연·이벤트 루프 정체가 있어도
# 만료 전에 최소 2회의 연장 기회를 확보한다.
HEARTBEAT_INTERVAL = GLOBAL_LOCK_TTL // 3

# 토큰이 일치할 때만 TTL 연장/삭제 — 만에 하나 락이 만료돼 다른 인스턴스가
# 획득한 경우, 죽은 주인이 남의 락을 연장·삭제하지 못하게 한다.
_EXTEND_IF_OWNER_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""
_RELEASE_IF_OWNER_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


async def _heartbeat(redis, token: str) -> None:
    """배치 실행 동안 전역 락 TTL을 주기적으로 연장한다(감사 Phase 1 — 성능 H2).

    토큰 비교 Lua로 자기 소유 락만 연장한다. 연장 실패(락 소실)는 경고만 남기고
    계속한다 — 이때의 중첩 실행은 구독별 Redis 락 + 토스 멱등키가 방어한다.
    run_renewals의 finally에서 task.cancel()로 종료된다.
    """
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            extended = await redis.eval(_EXTEND_IF_OWNER_LUA, 1, GLOBAL_LOCK_KEY,
                                        token, str(GLOBAL_LOCK_TTL))
            if not extended:
                logger.warning("전역 락 연장 실패 — 락이 만료/탈취됨. "
                               "구독별 락·멱등키가 중첩 실행을 방어합니다.")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 일시적 Redis 오류로 배치를 죽이지 않는다
            logger.warning("전역 락 heartbeat 오류(다음 주기에 재시도)", exc_info=True)


async def run_renewals(app: FastAPI) -> dict | None:
    """갱신 배치 1회를 실행한다. 전역 Redis 락으로 다중 인스턴스 중복 실행 방지.

    락 획득에 실패하면(다른 인스턴스가 실행 중) 즉시 None을 반환한다.
    락 값은 무작위 토큰 — heartbeat 연장과 해제 모두 토큰 비교로 자기 소유
    락만 건드린다(감사 Phase 1 — 성능 H2). 배치 완료 또는 예외 발생 시
    finally 블록에서 heartbeat 중단 + 락 해제를 보장한다.
    """
    redis = app.state.redis
    token = uuid.uuid4().hex
    if not await redis.set(GLOBAL_LOCK_KEY, token, nx=True, ex=GLOBAL_LOCK_TTL):
        logger.info("renewal batch skipped — 다른 인스턴스가 실행 중")
        return None
    heartbeat = asyncio.create_task(_heartbeat(redis, token))
    try:
        # settings= 인자 제거 — 재시도 설정은 이제 GlobalSettings(DB)에서 로드 (요청 013)
        stats = await process_due(app.state.session_factory, redis, app.state.toss,
                                  app.state.cipher, app.state.email_sender,
                                  notifier=app.state.notifier)
        logger.info("renewal batch done: %s", stats)
        return stats
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        try:
            await redis.eval(_RELEASE_IF_OWNER_LUA, 1, GLOBAL_LOCK_KEY, token)
        except Exception:  # noqa: BLE001 — 해제 실패는 TTL로 자연 해소
            logger.warning("전역 락 해제 실패(TTL로 만료 예정)")


def start_scheduler(app: FastAPI) -> AsyncIOScheduler:
    """APScheduler를 시작하고 갱신 배치 잡을 등록한다.

    ``max_instances=1``로 동일 프로세스 내 중첩 실행을 차단하고,
    ``coalesce=True``로 지연된 실행이 쌓일 경우 한 번만 실행한다.
    스케줄러는 ``app.state.settings.scheduler_interval_minutes`` 주기로 동작하며,
    ``scheduler_enabled=False``일 때는 호출 자체가 생략된다(main.py에서 분기).
    """
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(run_renewals, "interval",
                      minutes=app.state.settings.scheduler_interval_minutes,
                      args=[app], max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler
