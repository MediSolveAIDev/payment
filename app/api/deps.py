"""API 공통 의존성 주입(Depends) 함수 모음.

앱 기동 시 app.state에 등록된 싱글턴(Settings, DB 세션 팩토리, Redis, Cipher,
TossClient, EmailSender)을 FastAPI Depends 체인으로 주입한다.

인증/인가 흐름:
  authenticate_service  — 외부 서비스 API 키 + IP + HMAC 3중 검증 (6단계).
  payment_rate_limit    — 결제 전용 추가 요청 수 제한.
  get_client_ip         — 리버스 프록시 뒤에서 실제 클라이언트 IP 판별.
"""

import time

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 공통 인프라 의존성은 app/core/deps.py로 이동(감사 Phase 4 — S13).
# 기존 호출부 호환을 위해 재export한다 — 새 코드는 app.core.deps에서 직접 import 권장.
from app.core.config import Settings, default_settings
from app.core.deps import (  # noqa: F401 — 재export
    is_loopback_ip,
    get_cipher,
    get_client_ip,
    get_db,
    get_email_sender,
    get_notifier,
    get_redis,
    get_settings,
)
# T7 컷오버: get_toss 제거 — 전역 토스 클라이언트 불필요. 서비스별 해석은 get_toss_provider 사용.
from app.core.crypto import AesGcmCipher
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitedError
from app.core.security import constant_time_equals, sha256_hex, sign_request
from app.models import Service, ServiceStatus
from app.services.app_settings import ensure_server_enabled  # 킬스위치 게이트 헬퍼

AUTH_FAILED = "인증에 실패했습니다"

# rate limit 윈도우 키 TTL(초) — 1분 윈도우 + 여유(감사 Phase 4 — S14 상수화)
RATE_WINDOW_TTL = 90
# nonce 1회용 키 TTL(초) — 타임스탬프 허용 오차(±300s)보다 길어 재전송 방어 보장.
# .env(hmac_nonce_ttl_seconds)로 조정 가능.
NONCE_TTL_SECONDS = default_settings().hmac_nonce_ttl_seconds


async def authenticate_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    cipher: AesGcmCipher = Depends(get_cipher),
) -> Service:
    """외부 API 3중 인증: API키 + IP 화이트리스트 + HMAC 서명(타임스탬프/nonce)."""
    # 킬스위치(요청 013): 서버 비활성화 상태면 API 키 읽기 전에 즉시 503 차단.
    # redis 전달 → 5초 TTL 캐시로 매 요청 DB 왕복 제거(감사 Phase 3 — 성능 M4)
    await ensure_server_enabled(db, redis)
    api_key = request.headers.get("x-service-key", "")
    timestamp = request.headers.get("x-timestamp", "")
    nonce = request.headers.get("x-nonce", "")
    signature = request.headers.get("x-signature", "")
    if not (api_key and timestamp and nonce and signature):
        raise AuthenticationError(AUTH_FAILED)

    # 1) API 키 (해시 대조)
    service = await db.scalar(select(Service).where(
        Service.api_key_hash == sha256_hex(api_key)))
    if service is None or service.status != ServiceStatus.ACTIVE:
        raise AuthenticationError(AUTH_FAILED)

    # 2) IP 화이트리스트
    #  - allowed_ips가 비어 있으면 "IP 제한 없음(모든 IP 허용)" — HMAC 서명으로만 보호.
    #  - 목록이 있으면 화이트리스트 검사(127.0.0.1/::1 로컬은 목록과 무관하게 항상 허용).
    ip = get_client_ip(request, settings)
    if service.allowed_ips and ip not in service.allowed_ips and not is_loopback_ip(ip):
        raise PermissionDeniedError("허용되지 않은 IP입니다")

    # 3) rate limit — 서명 검증 전에 카운트해 무효 요청도 throttle (DoS 완화)
    window = int(time.time() // 60)
    rl_key = f"rl:{service.id}:{window}"
    count = await redis.incr(rl_key)
    if count == 1:
        await redis.expire(rl_key, RATE_WINDOW_TTL)
    if count > settings.rate_limit_per_minute:
        raise RateLimitedError("요청 한도를 초과했습니다")

    # 4) 타임스탬프 윈도우 (재전송 방어 1차)
    try:
        ts = int(timestamp)
    except ValueError:
        raise AuthenticationError(AUTH_FAILED) from None
    if abs(time.time() - ts) > settings.hmac_timestamp_tolerance_seconds:
        raise AuthenticationError(AUTH_FAILED)

    # 5) HMAC 서명 검증 (본문 무결성 포함)
    #    nonce 소비보다 먼저 검증 — 서명 위조 요청이 Redis nonce 키를
    #    무한정 적재(메모리 DoS)하지 못하게 한다. 유효 요청의 재전송은
    #    어차피 유효 서명을 동반하므로 nonce를 뒤에 둬도 방어력 동일.
    body = await request.body()
    secret = cipher.decrypt(service.hmac_secret_encrypted)
    expected = sign_request(secret, request.method, request.url.path,
                            timestamp, nonce, body)
    if not constant_time_equals(expected, signature):
        raise AuthenticationError(AUTH_FAILED)

    # 6) nonce 1회용 (재전송 방어 2차) — 서명 검증 통과 후에만 소비
    nonce_key = f"nonce:{service.id}:{nonce}"
    if not await redis.set(nonce_key, "1", nx=True, ex=NONCE_TTL_SECONDS):
        raise AuthenticationError(AUTH_FAILED)

    return service


async def payment_rate_limit(
    request: Request,
    service: Service = Depends(authenticate_service),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> Service:
    """결제성 엔드포인트 전용 추가 제한."""
    window = int(time.time() // 60)
    key = f"rlp:{service.id}:{window}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, RATE_WINDOW_TTL)
    if count > settings.rate_limit_payment_per_minute:
        raise RateLimitedError("결제 요청 한도를 초과했습니다")
    return service
