"""Redis 분산 락 헬퍼 + 갱신·정합성 공유 상수.

renewals.py와 reconciliation.py 양쪽에서 사용하므로 별도 모듈로 추출.
순환 import 없이 단방향 의존 구조를 유지한다.
"""
import logging
import uuid
from datetime import timedelta

from app.models import SubscriptionStatus

logger = logging.getLogger("payment.locks")

# 락 TTL(초)
RENEW_LOCK_TTL = 300

# PENDING 정합성 유예 시간 — 결과 불명 결제를 재조회하기 전 대기 시간

PENDING_RECONCILE_GRACE = timedelta(minutes=10)

# 갱신/체험 만료가 배치에서 처리되는 '결제 시도 대상' 상태.
# EXTENDED(연장처리)도 포함 — 연장된 새 만료일(=next_billing_at) 도래 시 자동결제 갱신(성공→ACTIVE).
DUE_STATUSES = (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE,
                 SubscriptionStatus.PAST_DUE, SubscriptionStatus.EXTENDED)

_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


async def acquire_lock(redis, key: str) -> str | None:
    token = uuid.uuid4().hex
    if await redis.set(key, token, nx=True, ex=RENEW_LOCK_TTL):
        return token
    return None


async def release_lock(redis, key: str, token: str) -> None:
    try:
        await redis.eval(_RELEASE_LOCK_LUA, 1, key, token)
    except Exception:  # noqa: BLE001 — 해제 실패는 TTL로 자연 해소
        logger.warning("락 해제 실패(TTL로 만료 예정): %s", key)
