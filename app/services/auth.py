"""인증 서비스 — 로그인·세션·비밀번호 설정·시스템 관리자 생성.

세션 구조 (Redis Hash):
  key: "session:{session_id}"
  fields:
    user_id     UUID 문자열
    role        UserRole 값
    service_id  UUID 문자열(없으면 빈 문자열)
    csrf_token  무작위 32바이트 urlsafe 토큰
    created_at  생성 시각(unix epoch 초) — 절대 수명 판정용(보안 L-5)

세션 집합 (Redis Set):
  key: "user_sessions:{user_id}"
  members: session_id 목록(해당 사용자의 활성 세션 전체)
  사용처: destroy_user_sessions — 비밀번호 변경·비활성화 시 전체 파기

로그인 보안:
  - 계정 열거 방지: 존재하지 않는 이메일도 verify_password(_DUMMY_HASH)를 실행해
    응답 시간을 균등화(타이밍 사이드채널 방어)
  - 연속 실패 5회: 15분 잠금(LOCKED + locked_until)
  - 잠금 만료 후 첫 로그인 시 자동 ACTIVE 복구

비밀번호 설정 토큰:
  - 48시간 유효, 1회용(used_at 기록)
  - 같은 사용자의 미사용 토큰 일괄 무효화(used_at = now)
  - redis 전달 시 기존 세션 모두 파기(보안 이벤트)
"""

import secrets
import uuid
from datetime import timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings, default_settings
from app.core.errors import AuthenticationError, ConflictError, InputValidationError
from app.core.security import hash_password, sha256_hex, verify_password
from app.models import PasswordSetupToken, User, UserRole, UserStatus
from app.notifications.email_templates import render_action_email
from app.services.app_settings import get_global_settings
from app.services.audit import record_audit

# 잠금 임계치·잠금 시간·비밀번호 최소 길이는 .env로 조정 가능(config.py 참조).
# 로그인 경로는 주입된 settings를, 그 외(설정 미주입 leaf)는 default_settings()를 쓴다.
SESSION_PREFIX = "session:"
USER_SESSIONS_PREFIX = "user_sessions:"
LOGIN_FAILED_MESSAGE = "이메일 또는 비밀번호가 올바르지 않습니다"

# 존재하지 않는 계정에도 argon2 검증 시간을 소모해 타이밍 기반
# 계정 열거(enumeration)를 방지한다.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


async def _create_session(redis: Redis, settings: Settings, user: User) -> str:
    """Redis에 세션 해시 생성 후 session_id 반환.

    파이프라인 트랜잭션:
    - hset + expire를 하나의 MULTI/EXEC으로 묶어 TTL 없는 불멸 세션을 방지.
    - user_sessions Set에도 session_id를 추가해 destroy_user_sessions가 전체 파기 가능하도록.
    - Set도 같은 TTL로 expire — 사용자가 한동안 로그인하지 않으면 자동 정리.

    service_id는 주 서비스(User.service_id)만 저장한다.
    추가 담당 서비스(UserService)는 세션에 캐시하지 않고 요청마다 DB 조회
    (effective_service_ids) — 권한 변경이 즉시 반영돼야 하므로.
    """
    session_id = secrets.token_urlsafe(32)
    key = SESSION_PREFIX + session_id
    user_set_key = USER_SESSIONS_PREFIX + str(user.id)
    # hset/expire를 파이프라인으로 묶어 TTL 없는 불멸 세션을 방지
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(key, mapping={
            "user_id": str(user.id),
            "role": user.role,
            "service_id": str(user.service_id) if user.service_id else "",
            "csrf_token": secrets.token_urlsafe(32),
            # 절대 수명 판정 기준(감사 Phase 2 — 보안 L-5). get_session이
            # 생성 후 session_absolute_ttl_seconds 초과 세션을 파기한다.
            "created_at": str(int(utcnow().timestamp())),
        })
        pipe.expire(key, settings.session_ttl_seconds)
        pipe.sadd(user_set_key, session_id)
        pipe.expire(user_set_key, settings.session_ttl_seconds)
        await pipe.execute()
    return session_id


async def login(db: AsyncSession, redis: Redis, settings: Settings,
                *, email: str, password: str, ip: str) -> tuple[str, User]:
    """이메일/비밀번호 로그인. 성공 시 (session_id, User) 반환.

    상태별 처리:
    - LOCKED + locked_until 미만: 즉시 거부(잠금 메시지)
    - LOCKED + locked_until 초과: 자동 ACTIVE 복구 후 정상 진행
    - DELETED: 존재하지 않는 것처럼 처리(LOGIN_FAILED_MESSAGE — 계정 열거 방지)
    - DISABLED: 별도 안내 메시지(관리자 문의)
    - PENDING: 비밀번호 설정 안내

    감사 로그:
    - 실패(unknown_email, 비밀번호 불일치): auth.login_failed
    - 성공: auth.login
    모두 DB 커밋 후 세션 생성 — 감사 없는 유효 세션을 방지.
    """
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(password, _DUMMY_HASH)  # 타이밍 균등화
        await record_audit(db, actor_type="USER", action="auth.login_failed",
                           detail={"email": email, "reason": "unknown_email"},
                           ip_address=ip)
        await db.commit()
        raise AuthenticationError(LOGIN_FAILED_MESSAGE)

    # 잠금 정책은 런타임(전체 설정·GlobalSettings)에서 즉시 조정 가능 — 공격 대응 시 즉시 강화.
    # .env(max_failed_logins/account_lock_minutes)는 GlobalSettings 미가용 시 비상 폴백.
    gs = await get_global_settings(db)
    now = utcnow()
    if user.status == UserStatus.LOCKED:
        if user.locked_until is not None and user.locked_until > now:
            raise AuthenticationError("계정이 잠겼습니다. 잠시 후 다시 시도해주세요")
        user.status = UserStatus.ACTIVE
        user.failed_login_count = 0
        user.locked_until = None

    if user.status == UserStatus.DELETED:
        raise AuthenticationError(LOGIN_FAILED_MESSAGE)  # 존재하지 않는 것처럼 처리
    if user.status == UserStatus.DISABLED:
        raise AuthenticationError("비활성화된 계정입니다. 관리자에게 문의하세요")

    if user.status == UserStatus.PENDING:
        raise AuthenticationError("비밀번호 설정이 필요합니다. 안내 메일을 확인해주세요")

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= gs.max_failed_logins:
            user.status = UserStatus.LOCKED
            user.locked_until = now + timedelta(minutes=gs.account_lock_minutes)
        await record_audit(db, actor_type="USER", actor_user_id=user.id,
                           action="auth.login_failed", ip_address=ip)
        await db.commit()
        raise AuthenticationError(LOGIN_FAILED_MESSAGE)

    user.failed_login_count = 0
    user.locked_until = None
    await record_audit(db, actor_type="USER", actor_user_id=user.id,
                       action="auth.login", ip_address=ip)
    # DB 커밋이 성공한 뒤에만 세션을 만든다 — 감사 없는 유효 세션 방지
    await db.commit()
    session_id = await _create_session(redis, settings, user)
    return session_id, user


async def get_session(redis: Redis, settings: Settings, session_id: str) -> dict | None:
    """세션 데이터 조회 + 유휴 TTL 연장. 없거나 빈 session_id면 None.

    매 요청마다 expire를 갱신해 활성 사용자의 세션이 유휴 타임아웃으로 끊기지 않도록 한다.

    절대 수명(감사 Phase 2 — 보안 L-5): 유휴 TTL은 활동 시마다 연장되므로
    탈취된 세션이 계속 쓰이면 영구 유효해진다. 생성 시각(created_at) 기준
    session_absolute_ttl_seconds를 초과한 세션은 활동과 무관하게 즉시 파기한다.
    created_at이 없는 구버전 세션도 안전 측으로 파기(재로그인 1회 유도).
    """
    if not session_id:
        return None
    key = SESSION_PREFIX + session_id
    data = await redis.hgetall(key)
    if not data:
        return None
    created_raw = data.get("created_at", "")
    created_at = int(created_raw) if created_raw.isdigit() else 0
    if int(utcnow().timestamp()) - created_at > settings.session_absolute_ttl_seconds:
        await logout(redis, session_id)  # 절대 수명 초과 — 세션·집합 정리 후 거부
        return None
    await redis.expire(key, settings.session_ttl_seconds)  # 유휴 타임아웃 연장
    return data


async def logout(redis: Redis, session_id: str) -> None:
    """세션 삭제 + user_sessions Set에서 session_id 제거."""
    key = SESSION_PREFIX + session_id
    user_id = await redis.hget(key, "user_id")
    await redis.delete(key)
    if user_id:
        await redis.srem(USER_SESSIONS_PREFIX + user_id, session_id)


async def destroy_user_sessions(redis: Redis, user_id: uuid.UUID) -> None:
    """비밀번호 변경 등 보안 이벤트 시 해당 사용자의 모든 세션 파기."""
    set_key = USER_SESSIONS_PREFIX + str(user_id)
    session_ids = await redis.smembers(set_key)
    if session_ids:
        await redis.delete(*[SESSION_PREFIX + sid for sid in session_ids])
    await redis.delete(set_key)


def _validate_password(password: str) -> None:
    """비밀번호 최소 길이 검증. .env(min_password_length, 기본 10자) 미만이면 거부."""
    min_len = default_settings().min_password_length
    if len(password) < min_len:
        raise InputValidationError(f"비밀번호는 {min_len}자 이상이어야 합니다")


async def setup_password(db: AsyncSession, *, token: str, password: str,
                         redis: Redis | None = None) -> User:
    """초기 비밀번호 설정/재설정. 토큰은 1회용 + 만료 검증.

    redis를 넘기면 비밀번호 변경 시 해당 사용자의 기존 세션을 모두 파기한다.
    같은 사용자의 다른 미사용 토큰도 일괄 무효화한다.

    흐름:
    1. 비밀번호 길이 검증
    2. 토큰 조회(미사용 + 만료 전)
    3. 비밀번호 해시 저장, 상태 ACTIVE, 실패 카운트 초기화
    4. 현재 토큰 used_at 기록 + 같은 사용자 다른 미사용 토큰 일괄 무효화
    5. 감사 로그 → 커밋
    6. redis가 있으면 기존 세션 전체 파기(커밋 후 — 세션 보안 이벤트)
    """
    _validate_password(password)
    row = await db.scalar(select(PasswordSetupToken).where(
        PasswordSetupToken.token_hash == sha256_hex(token),
        PasswordSetupToken.used_at.is_(None)))
    if row is None or row.expires_at < utcnow():
        raise InputValidationError("유효하지 않거나 만료된 토큰입니다")
    user = await db.get(User, row.user_id)
    user.password_hash = hash_password(password)
    user.status = UserStatus.ACTIVE
    user.failed_login_count = 0
    user.locked_until = None
    now = utcnow()
    row.used_at = now
    # 같은 사용자의 다른 미사용 토큰 일괄 무효화
    others = await db.scalars(select(PasswordSetupToken).where(
        PasswordSetupToken.user_id == row.user_id,
        PasswordSetupToken.used_at.is_(None),
        PasswordSetupToken.id != row.id))
    for other in others.all():
        other.used_at = now
    await record_audit(db, actor_type="USER", actor_user_id=user.id,
                       action="auth.password_set")
    await db.commit()
    if redis is not None:
        await destroy_user_sessions(redis, user.id)
    return user


async def get_user(db: AsyncSession, user_id: str) -> User | None:
    """user_id 문자열로 User 조회. UUID 파싱 실패나 미존재 시 None 반환.

    세션 데이터(문자열)로부터 DB User를 조회하는 미들웨어용 헬퍼.
    """
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        return None
    return await db.get(User, uid)


async def create_system_admin(db: AsyncSession, *, email: str, password: str) -> User:
    """CLI에서 호출 — 최초 SYSTEM_ADMIN 계정 생성."""
    _validate_password(password)
    if await db.scalar(select(User).where(User.email == email)):
        raise ConflictError("이미 존재하는 이메일입니다")
    user = User(email=email, password_hash=hash_password(password),
                role=UserRole.SYSTEM_ADMIN, status=UserStatus.ACTIVE)
    db.add(user)
    await record_audit(db, actor_type="SYSTEM", action="user.create_admin",
                       detail={"email": email})
    await db.commit()
    return user


# 재설정 링크 유효시간(.env: password_link_ttl_hours, 기본 48시간).
RESET_TOKEN_TTL = timedelta(hours=default_settings().password_link_ttl_hours)


async def issue_password_reset(db: AsyncSession, email_sender, *, user_id,
                               base_url: str, actor_user_id,
                               redis: Redis | None = None) -> bool:
    """관리자가 담당자 비밀번호 재설정 토큰 발급 + 메일 발송.

    반환: 메일 발송 성공 여부.

    redis를 넘기면 발급 즉시 해당 사용자의 기존 세션을 모두 파기한다 —
    관리자 주도 재설정은 계정 탈취 의심 상황일 수 있으므로 활성 세션 창을 닫는다.

    흐름:
    1. 사용자 조회
    2. PasswordSetupToken 생성(48시간 유효)
    3. 감사 로그 → 커밋
    4. redis가 있으면 기존 세션 파기(커밋 후)
    5. 재설정 메일 발송 → 발송 성공 여부 반환
    """
    from app.core.security import generate_setup_token, sha256_hex

    user = await db.get(User, user_id)
    if user is None:
        from app.core.errors import NotFoundError
        raise NotFoundError("사용자를 찾을 수 없습니다")
    token = generate_setup_token()
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + RESET_TOKEN_TTL))
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="user.password_reset_issued", target_type="user",
                       target_id=str(user.id),
                       detail={"email": user.email,
                               "note": "재설정 메일 발송(48시간 유효), 기존 세션 파기"})
    await db.commit()
    if redis is not None:
        await destroy_user_sessions(redis, user.id)
    # UI/UX 적용 — 평문 대신 CTA 버튼·브랜딩이 있는 HTML 메일(평문 대체 본문 동반).
    reset_url = f"{base_url}/admin/setup-password?token={token}"
    text, html = render_action_email(
        title="비밀번호 재설정 안내",
        intro="관리자 콘솔 계정의 비밀번호 재설정이 요청되었습니다. "
              "아래 버튼을 눌러 새 비밀번호를 설정해 주세요.",
        button_label="비밀번호 재설정하기",
        button_url=reset_url,
        note="이 링크는 발송 후 48시간 동안만 유효합니다.")
    return await email_sender.send(
        user.email, "[결제시스템] 비밀번호 재설정 안내", text, html=html)
