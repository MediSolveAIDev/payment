"""전역설정(GlobalSettings) 단일 행 접근·갱신 — 재시도/어드민IP/킬스위치.

모든 함수는 id=1 단일 행에만 접근한다. 행이 없으면 get_or_create로 기본값 생성.
record_audit은 commit을 하지 않으므로 각 헬퍼가 직접 commit한다.
"""
import ipaddress
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.deps import LOOPBACK_IPS, strip_loopback_ips
from app.core.errors import AuthenticationError, InputValidationError, ServerDisabledError
from app.core.security import verify_password
from app.models import GlobalSettings, User
from app.services.audit import record_audit


async def get_global_settings(db: AsyncSession) -> GlobalSettings:
    """전역설정 단일 행(id=1)을 반환. 없으면 기본값으로 생성(get_or_create).

    retry_limit=4, retry_interval_hours=12, suspended_grace_days=30,
    admin_allowed_ips=[], server_disabled=False 기본값.
    """
    gs = await db.get(GlobalSettings, 1)
    if gs is None:
        gs = GlobalSettings(id=1)   # 모델 기본값(retry 4/12/30, 제한 없음, 활성)
        db.add(gs)
        await db.commit()
        await db.refresh(gs)
    return gs


async def update_retry_settings(db: AsyncSession, *, retry_limit: int,
                                retry_interval_hours: int,
                                suspended_grace_days: int,
                                actor_user_id: uuid.UUID) -> GlobalSettings:
    """자동결제 재시도 설정 변경. 다음 갱신 배치부터 적용.

    Args:
        retry_limit: 재시도 최대 횟수(0 이상).
        retry_interval_hours: 재시도 간격(시간, 1 이상).
        suspended_grace_days: SUSPENDED → 만료 유예 일수(0 이상).
        actor_user_id: 변경을 수행한 관리자 UUID(감사 로그용).
    """
    if retry_limit < 0 or retry_interval_hours < 1 or suspended_grace_days < 0:
        raise InputValidationError("재시도 설정 값이 올바르지 않습니다")
    gs = await get_global_settings(db)
    # 변경 전 값을 먼저 캡처 — 감사로그에 "어떤 값 → 어떤 값"으로 남기기 위함
    detail = {"old_retry_limit": gs.retry_limit, "new_retry_limit": retry_limit,
              "old_interval_hours": gs.retry_interval_hours,
              "new_interval_hours": retry_interval_hours,
              "old_grace_days": gs.suspended_grace_days,
              "new_grace_days": suspended_grace_days}
    gs.retry_limit, gs.retry_interval_hours = retry_limit, retry_interval_hours
    gs.suspended_grace_days = suspended_grace_days
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="settings.retry_updated", target_type="global_settings",
                       target_id="1", detail=detail)
    await db.commit()
    return gs


async def update_security_policy(db: AsyncSession, *, max_failed_logins: int,
                                 account_lock_minutes: int, one_off_max_amount: int,
                                 actor_user_id: uuid.UUID) -> GlobalSettings:
    """보안/결제 정책(잠금 임계치·잠금 시간·단건결제 상한) 런타임 변경.

    재배포 없이 즉시 적용된다(login·단건결제가 매 호출 시 이 값을 읽음).

    Args:
        max_failed_logins: 연속 로그인 실패 잠금 임계치(1 이상).
        account_lock_minutes: 잠금 지속 시간(분, 1 이상).
        one_off_max_amount: 단건 결제 1회 최대 금액(원, 1 이상).
        actor_user_id: 변경을 수행한 관리자 UUID(감사 로그용).
    """
    if max_failed_logins < 1 or account_lock_minutes < 1 or one_off_max_amount < 1:
        raise InputValidationError("보안 정책 값이 올바르지 않습니다(모두 1 이상)")
    gs = await get_global_settings(db)
    detail = {"old_max_failed_logins": gs.max_failed_logins,
              "new_max_failed_logins": max_failed_logins,
              "old_account_lock_minutes": gs.account_lock_minutes,
              "new_account_lock_minutes": account_lock_minutes,
              "old_one_off_max_amount": gs.one_off_max_amount,
              "new_one_off_max_amount": one_off_max_amount}
    gs.max_failed_logins = max_failed_logins
    gs.account_lock_minutes = account_lock_minutes
    gs.one_off_max_amount = one_off_max_amount
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="settings.security_policy_updated",
                       target_type="global_settings", target_id="1", detail=detail)
    await db.commit()
    return gs


async def update_admin_ips(db: AsyncSession, *, ips: list[str], current_ip: str,
                           actor_user_id: uuid.UUID) -> GlobalSettings:
    """어드민 접속 허용 IP 목록 변경. 빈 목록=제한 없음.

    lockout 방지: 비어있지 않은데 현재 접속 IP가 빠지면 InputValidationError.
    IP 형식 검증(IPv4/IPv6)도 수행하며 빈 줄은 무시한다.

    Args:
        ips: 허용할 IP 문자열 목록.
        current_ip: 요청자의 현재 접속 IP(lockout 방지 검사용).
        actor_user_id: 변경을 수행한 관리자 UUID(감사 로그용).
    """
    cleaned = []
    for ip in ips:
        ip = ip.strip()
        if not ip:
            continue
        try:
            ipaddress.ip_address(ip)   # IPv4/IPv6 형식 검증
        except ValueError as exc:
            raise InputValidationError(f"유효하지 않은 IP: {ip}") from exc
        cleaned.append(ip)
    # 127.0.0.1/::1(같은 서버, 로컬)은 무조건 허용이라 목록에 저장하지 않는다
    cleaned = strip_loopback_ips(cleaned)
    # lockout 방지: 목록이 비어있지 않은데 현재 IP가 포함되지 않으면 거부.
    # 단, 현재 IP가 루프백이면 항상 허용되므로 검사하지 않는다.
    if cleaned and current_ip not in cleaned and current_ip not in LOOPBACK_IPS:
        raise InputValidationError("현재 접속 IP를 포함해야 잠금을 피할 수 있습니다")
    gs = await get_global_settings(db)
    # 변경 전 IP 목록을 먼저 캡처 — 감사로그에 "기존 목록 → 새 목록"으로 남긴다
    old_ips = list(gs.admin_allowed_ips or [])
    gs.admin_allowed_ips = cleaned
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="settings.admin_ips_updated", target_type="global_settings",
                       target_id="1", detail={"old_ips": old_ips, "new_ips": cleaned})
    await db.commit()
    return gs


# 킬스위치 Redis 캐시(감사 Phase 3 — 성능 M4).
# ensure_server_enabled는 모든 외부 API 요청에서 실행되므로, 매 요청 DB 왕복 대신
# 짧은 TTL 캐시를 둔다. 값 인코딩: ""(빈 문자열)=활성, 비어있지 않으면 비활성 사유.
# set_server_disabled가 전환 시 즉시 무효화하고, TTL은 무효화 누락(타 인스턴스 등)
# 시의 최대 전파 지연 상한이다.
SERVER_DISABLED_CACHE_KEY = "cache:global:server_disabled"
SERVER_DISABLED_CACHE_TTL = 5  # 초

_DEFAULT_DISABLED_MESSAGE = "서비스 점검 중입니다"


async def ensure_server_enabled(db: AsyncSession, redis=None) -> None:
    """결제서버가 비활성화(킬스위치 ON)면 ServerDisabledError(503, 사유)를 발생시킨다.

    외부 API 진입 직후 호출하는 게이트 헬퍼.
    어드민 라우트에는 사용하지 않는다(어드민은 킬스위치 영향 없음).

    redis를 넘기면 결과를 짧은 TTL로 캐시해 매 요청 DB 왕복을 제거한다
    (감사 Phase 3 — 성능 M4). redis=None이면(배치·테스트 등) 항상 DB 조회.
    """
    if redis is not None:
        cached = await redis.get(SERVER_DISABLED_CACHE_KEY)
        if cached is not None:
            if cached:  # 비어있지 않음 = 비활성 사유
                raise ServerDisabledError(cached)
            return      # "" = 활성
    gs = await get_global_settings(db)
    reason = (gs.disabled_reason or _DEFAULT_DISABLED_MESSAGE) if gs.server_disabled else ""
    if redis is not None:
        await redis.set(SERVER_DISABLED_CACHE_KEY, reason, ex=SERVER_DISABLED_CACHE_TTL)
    if gs.server_disabled:
        # disabled_reason 이 없으면 기본 안내 문구 사용
        raise ServerDisabledError(reason)


async def set_server_disabled(db: AsyncSession, *, disabled: bool,
                              reason: str | None, actor_user: User,
                              password: str, redis=None) -> GlobalSettings:
    """결제서버 킬스위치 전환. 본인 비밀번호 재확인 + (비활성화 시) 사유 필수.

    비밀번호 불일치 → AuthenticationError.
    disabled=True인데 reason 없음 → InputValidationError.

    Args:
        disabled: True=서버 비활성화, False=활성화.
        reason: 비활성화 사유(disabled=True 시 필수).
        actor_user: 작업 수행 User 객체(password_hash 및 id 사용).
        password: 확인용 평문 비밀번호.
        redis: 전달 시 킬스위치 캐시를 즉시 무효화한다(성능 M4 캐시 전파 지연 제거).
    """
    # 본인 비밀번호 재확인 (verify_password: 첫 인자=평문, 두 번째=해시)
    if not verify_password(password, actor_user.password_hash):
        raise AuthenticationError("비밀번호가 일치하지 않습니다")
    if disabled and not (reason or "").strip():
        raise InputValidationError("비활성화 사유를 입력해야 합니다")
    gs = await get_global_settings(db)
    # 변경 전 상태를 먼저 캡처 — 감사로그에 다른 설정과 동일하게 old→new 상세를 남긴다
    new_reason = reason.strip() if disabled and reason else None
    detail = {"old_server_disabled": gs.server_disabled, "new_server_disabled": disabled,
              "old_reason": gs.disabled_reason, "new_reason": new_reason}
    gs.server_disabled = disabled
    gs.disabled_reason = new_reason
    gs.disabled_at = utcnow() if disabled else None
    gs.disabled_by = actor_user.id if disabled else None
    await record_audit(db, actor_type="USER", actor_user_id=actor_user.id,
                       action="server.disabled" if disabled else "server.enabled",
                       target_type="global_settings", target_id="1",
                       detail=detail)
    await db.commit()
    if redis is not None:
        # 캐시 즉시 무효화 — 다음 요청이 DB에서 새 상태를 읽어 재캐시한다
        await redis.delete(SERVER_DISABLED_CACHE_KEY)
    return gs
