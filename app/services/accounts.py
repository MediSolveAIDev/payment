"""관리자 계정 관리 + 관리자↔서비스 다대다.

유효 담당 서비스 = User.service_id(주) ∪ user_services(추가). SYSTEM_ADMIN은
스코프 제한이 없으므로 None.
"""

import re
import uuid
from datetime import timedelta

from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import default_settings
from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.core.security import generate_setup_token, sha256_hex
from app.models import (
    PasswordSetupToken,
    Service,
    User,
    UserRole,
    UserService,
    UserStatus,
)
from app.notifications.email import EmailSender
from app.notifications.email_templates import render_action_email
from app.services.audit import record_audit

# 설정 링크 유효시간(.env: password_link_ttl_hours, 기본 48시간).
SETUP_TOKEN_TTL = timedelta(hours=default_settings().password_link_ttl_hours)


async def effective_service_ids(db: AsyncSession, user: User) -> list[uuid.UUID] | None:
    """담당 서비스 ID 목록. SYSTEM_ADMIN이면 None(전체 접근).

    SERVICE_MANAGER의 유효 스코프:
    - User.service_id(주 서비스) + UserService 테이블의 추가 서비스
    - 두 집합의 합집합을 반환(중복 없는 리스트)
    반환된 목록이 비어 있으면 담당 서비스 없음(조회 권한 없음).
    """
    if user.role == UserRole.SYSTEM_ADMIN:
        return None
    ids: set[uuid.UUID] = set()
    if user.service_id is not None:
        ids.add(user.service_id)
    extra = (await db.scalars(select(UserService.service_id).where(
        UserService.user_id == user.id))).all()
    ids.update(extra)
    return list(ids)


async def _validate_services_exist(db: AsyncSession, service_ids: list[uuid.UUID]) -> None:
    """서비스 목록이 모두 실제 존재하는지 확인. 하나라도 없으면 NotFoundError."""
    for sid in service_ids:
        if await db.get(Service, sid) is None:
            raise NotFoundError("서비스를 찾을 수 없습니다")


_PHONE_RE = re.compile(r"^[0-9+\-() ]{7,30}$")


def _normalize_phone(phone: str | None) -> str | None:
    """전화번호 정규화(선택 항목). 빈값은 None, 형식 위반은 오류."""
    phone = (phone or "").strip()
    if not phone:
        return None
    if not _PHONE_RE.match(phone):
        raise InputValidationError("전화번호 형식이 올바르지 않습니다")
    return phone


async def create_account(db: AsyncSession, email_sender: EmailSender, *,
                         email: str, role: str, service_ids: list[uuid.UUID],
                         base_url: str, phone: str | None = None,
                         actor_user_id: uuid.UUID | None = None,
                         admin_notifier=None) -> tuple[User, bool]:
    """관리자 계정 생성(PENDING) + 비밀번호 설정 메일. 반환: (User, 메일 발송 성공 여부).

    SERVICE_MANAGER 서비스는 선택(0개 허용 — 서비스 등록 시 할당 가능). 첫 서비스=주, 나머지=추가부여.
    SYSTEM_ADMIN은 서비스 없음.

    흐름:
    1. 이메일 정규화(소문자) + 전화번호 정규화
    2. 역할 검증(SYSTEM_ADMIN / SERVICE_MANAGER 만 허용)
    3. SYSTEM_ADMIN이면 service_ids 강제 빈 배열
    4. service_ids 중복 제거(순서 보존)
    5. 서비스 존재 확인
    6. 이메일 중복 확인(SELECT 선조회 + flush IntegrityError 이중 방어)
    7. User 생성(PENDING):
       - service_id = service_ids[0] (주 서비스) 또는 None
    8. service_ids[1:]은 UserService 다대다로 추가
    9. PasswordSetupToken 생성(48시간 유효) — 평문 token은 메일에만 전달
    10. 감사 로그 → 커밋 → 설정 메일 발송(커밋 후 발송 — 실패해도 계정은 유지)
    """
    email = (email or "").strip().lower()
    if not email:
        raise InputValidationError("이메일은 필수입니다")
    phone = _normalize_phone(phone)
    if role not in (UserRole.SYSTEM_ADMIN, UserRole.SERVICE_MANAGER):
        raise InputValidationError(f"유효하지 않은 역할: {role}")
    if role == UserRole.SYSTEM_ADMIN:
        service_ids = []
    # 중복 제거(순서 보존)
    seen: list[uuid.UUID] = []
    for sid in service_ids:
        if sid not in seen:
            seen.append(sid)
    service_ids = seen
    await _validate_services_exist(db, service_ids)
    if await db.scalar(select(User).where(User.email == email)):
        raise ConflictError("이미 존재하는 이메일입니다")

    primary = service_ids[0] if service_ids else None
    user = User(email=email, phone=phone, role=role, service_id=primary,
                status=UserStatus.PENDING)
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ConflictError("이미 존재하는 이메일입니다") from None
    for sid in service_ids[1:]:
        db.add(UserService(user_id=user.id, service_id=sid))

    token = generate_setup_token()
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + SETUP_TOKEN_TTL))
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="account.create", target_type="user",
                       target_id=str(user.id),
                       detail={"email": email, "role": role,
                               "service_count": len(service_ids)})
    await db.commit()
    # UI/UX 적용 — 평문 대신 CTA 버튼·브랜딩이 있는 HTML 메일(평문 대체 본문 동반).
    role_ko = "시스템 관리자" if role == UserRole.SYSTEM_ADMIN else "서비스 담당자"
    setup_url = f"{base_url}/admin/setup-password?token={token}"
    text, html = render_action_email(
        title="관리자 계정 설정 안내",
        intro=f"결제 관리 콘솔 {role_ko} 계정이 생성되었습니다. "
              "아래 버튼을 눌러 비밀번호를 설정하면 로그인할 수 있습니다.",
        button_label="비밀번호 설정하기",
        button_url=setup_url,
        note="이 링크는 발송 후 48시간 동안만 유효합니다.")
    sent = await email_sender.send(
        email, "[결제시스템] 관리자 계정 설정 안내", text, html=html)
    # 시스템 관리자 전원에게 '새 계정 생성' 알림 메일(best-effort, 커밋 후라 실패해도 무해)
    if admin_notifier is not None:
        await admin_notifier.account_created(
            db, user=user, actor_user_id=actor_user_id, service_ids=service_ids)
    return user, sent


async def _get_account(db: AsyncSession, user_id: uuid.UUID) -> User:
    """단일 계정 조회. DELETED 상태는 없는 것으로 취급(소프트 삭제 일관성)."""
    user = await db.get(User, user_id)
    if user is None or user.status == UserStatus.DELETED:
        raise NotFoundError("계정을 찾을 수 없습니다")
    return user


async def update_account(db: AsyncSession, *, user_id: uuid.UUID,
                         email: str | None = None, phone: str | None = None,
                         actor_user_id: uuid.UUID | None = None) -> User:
    """계정 정보 수정. 이메일 변경 시 반드시 중복 체크.

    이메일 변경 시 추가 동기화:
    - 이 계정이 대표(알림 수신처)인 서비스들의 Service.manager_email을 일괄 갱신.
    - IntegrityError는 커밋 시점에도 발생할 수 있으므로(UNIQUE 제약) try-except로 이중 방어.

    phone=None을 전달하면 전화번호 변경 없음(미전달과 동일).
    전화번호를 삭제하려면 빈 문자열("")을 전달한다(_normalize_phone이 None으로 처리).
    """
    user = await _get_account(db, user_id)
    detail: dict = {}
    if email is not None:
        new_email = email.strip().lower()
        if not new_email:
            raise InputValidationError("이메일은 필수입니다")
        if new_email != user.email:
            dup = await db.scalar(select(User).where(
                User.email == new_email, User.id != user.id))
            if dup is not None:
                raise ConflictError("이미 존재하는 이메일입니다")
            # 이 계정이 대표(알림 수신처)인 서비스들의 manager_email 동기화
            await db.execute(update(Service).where(
                Service.manager_email == user.email).values(manager_email=new_email))
            # 변경 전/후 이메일을 모두 기록(감사 전/후 비교)
            detail["old_email"], detail["new_email"] = user.email, new_email
            user.email = new_email
    if phone is not None:
        old_phone = user.phone or "(없음)"
        user.phone = _normalize_phone(phone)
        detail["old_phone"], detail["new_phone"] = old_phone, user.phone or "(삭제)"
    try:
        await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                           action="account.update", target_type="user",
                           target_id=str(user.id), detail=detail)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError("이미 존재하는 이메일입니다") from None
    return user


async def set_account_disabled(db: AsyncSession, redis: Redis | None, *,
                               user_id: uuid.UUID, disabled: bool,
                               actor_user_id: uuid.UUID | None = None) -> User:
    """계정 비활성화/복구. 비활성화 시 기존 세션을 모두 파기한다.

    복구는 비밀번호 설정 여부에 따라 ACTIVE(설정됨) 또는 PENDING(미설정)으로.

    본인 계정 비활성화 금지:
    - 본인을 비활성화하면 현재 세션이 끊기고 복구 불가 상태가 될 수 있으므로 거부.

    세션 파기 순서:
    - DB 커밋 후 Redis 세션 삭제 — 커밋 실패 시 세션을 지우지 않아 불필요한 재로그인 방지.
    """
    from app.services.auth import destroy_user_sessions

    user = await _get_account(db, user_id)
    old_status = user.status   # 변경 전 상태 캡처(감사 전/후)
    if disabled:
        if user.id == actor_user_id:
            raise InputValidationError("본인 계정은 비활성화할 수 없습니다")
        user.status = UserStatus.DISABLED
        action = "account.disable"
    else:
        user.status = UserStatus.ACTIVE if user.password_hash else UserStatus.PENDING
        action = "account.enable"
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action=action, target_type="user", target_id=str(user.id),
                       detail={"email": user.email, "old_status": old_status,
                               "new_status": user.status})
    await db.commit()
    if disabled and redis is not None:
        await destroy_user_sessions(redis, user.id)
    return user


async def delete_account(db: AsyncSession, redis: Redis | None, *, user_id: uuid.UUID,
                         actor_user_id: uuid.UUID | None = None) -> None:
    """소프트 삭제 — 상태 DELETED로 숨김. 기존 세션 파기, 담당 서비스 해제.

    소프트 삭제 이유:
    - 감사 로그에 actor_user_id로 남아 있는 외래 참조를 유지하기 위해 물리 삭제 대신 사용.

    대표 담당자 보호:
    - 이 계정이 어느 서비스의 manager_email(대표)이면 삭제 거부.
    - 먼저 registry.set_primary_manager로 다른 계정을 대표로 지정해야 한다.

    서비스 해제:
    - User.service_id = None (주 서비스 해제)
    - UserService 행 전체 삭제(다대다 해제)
    """
    from app.services.auth import destroy_user_sessions

    user = await _get_account(db, user_id)
    if user.id == actor_user_id:
        raise InputValidationError("본인 계정은 삭제할 수 없습니다")
    primary_of = await db.scalar(select(Service).where(
        Service.manager_email == user.email))
    if primary_of is not None:
        raise InputValidationError(
            f"'{primary_of.name}' 서비스의 대표 담당자입니다. 먼저 다른 계정을 대표로 지정하세요.")
    user.status = UserStatus.DELETED
    user.service_id = None
    await db.execute(delete(UserService).where(UserService.user_id == user.id))
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="account.delete", target_type="user",
                       target_id=str(user.id), detail={"email": user.email})
    await db.commit()
    if redis is not None:
        await destroy_user_sessions(redis, user.id)


async def _get_manager(db: AsyncSession, user_id: uuid.UUID) -> User:
    """서비스 담당자 계정 조회. SERVICE_MANAGER 역할이 아니면 오류."""
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("계정을 찾을 수 없습니다")
    if user.role != UserRole.SERVICE_MANAGER:
        raise InputValidationError("서비스 담당자에게만 서비스를 할당할 수 있습니다")
    return user


async def assign_service(db: AsyncSession, *, user_id: uuid.UUID, service_id: uuid.UUID,
                         actor_user_id: uuid.UUID | None = None) -> None:
    """담당 서비스 추가. 이미 담당이면 무시.

    주 서비스(User.service_id) 우선:
    - user.service_id가 None이면 주 서비스로 직접 설정
    - 주 서비스가 이미 있으면 UserService 다대다로 추가
    중복 확인은 effective_service_ids로 현재 전체 스코프를 기준으로 한다.
    """
    user = await _get_manager(db, user_id)
    if await db.get(Service, service_id) is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    current = await effective_service_ids(db, user)
    if current and service_id in current:
        return  # 이미 담당
    svc = await db.get(Service, service_id)   # 서비스명 표시용(위 None 가드 통과)
    if user.service_id is None:
        user.service_id = service_id  # 주 서비스로
    else:
        db.add(UserService(user_id=user.id, service_id=service_id))
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="account.assign_service", target_type="user",
                       target_id=str(user.id),
                       detail={"email": user.email, "service_id": str(service_id),
                               "service_name": svc.name if svc else None})
    await db.commit()


async def unassign_service(db: AsyncSession, *, user_id: uuid.UUID, service_id: uuid.UUID,
                           actor_user_id: uuid.UUID | None = None) -> None:
    """담당 서비스 해제. 주 서비스를 해제하면 다른 담당이 주로 승격.

    대표 담당자(Service.manager_email과 동일 이메일 계정)는 해제할 수 없다 —
    먼저 다른 계정을 대표로 지정해야 한다(ConflictError). 이 규칙을 서비스
    레이어에서 강제해(감사 Phase 4 — S4) 어드민 화면 외의 진입점(향후 API·CLI)
    에서도 대표 담당자가 빠지는 일이 없도록 한다.

    주 서비스 해제 처리:
    - UserService 행 먼저 삭제(service_id가 일치하는 행)
    - User.service_id == service_id이면 UserService에서 다른 서비스를 꺼내 주로 승격
    - remaining이 None이면 담당 서비스 없는 상태(service_id = None)
    """
    user = await _get_manager(db, user_id)
    svc = await db.get(Service, service_id)   # 서비스명 표시용 + 대표 담당자 검사
    if svc is not None and user.email == svc.manager_email:
        raise ConflictError(
            "대표 담당자는 해제할 수 없습니다. 먼저 다른 계정을 대표로 지정하세요.")
    await db.execute(delete(UserService).where(
        UserService.user_id == user.id, UserService.service_id == service_id))
    if user.service_id == service_id:
        remaining = (await db.scalars(select(UserService.service_id).where(
            UserService.user_id == user.id))).first()
        user.service_id = remaining  # None이면 담당 서비스 없음
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="account.unassign_service", target_type="user",
                       target_id=str(user.id),
                       detail={"email": user.email, "service_id": str(service_id),
                               "service_name": svc.name if svc else None})
    await db.commit()


async def list_managed_services(db: AsyncSession, user: User) -> list[Service]:
    """사용자의 유효 스코프 내 서비스 목록(이름 순). SYSTEM_ADMIN이면 빈 목록 반환.

    SYSTEM_ADMIN은 effective_service_ids가 None을 반환하므로 빈 목록 반환 주의:
    SYSTEM_ADMIN용 전체 조회는 registry.list_services를 사용할 것.
    """
    ids = await effective_service_ids(db, user)
    if not ids:
        return []
    return list((await db.scalars(
        select(Service).where(Service.id.in_(ids)).order_by(Service.name))).all())
