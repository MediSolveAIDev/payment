"""서비스 등록·키 관리·담당자 배정 서비스.

주요 엔티티 관계:
  Service ─── manager_email(대표 담당자 알림 수신처, 1개)
           ├── User.service_id(주 담당자, N명)
           └── UserService(추가 담당 다대다)

allowed_ips:
  IPv4 전용(옥텟 입력 UI — 요청 005). IP 허용 목록 1개 이상 필수.

API 키 / HMAC secret:
  평문은 발급 즉시 반환 후 파기 — DB에는 SHA-256 해시와 AES-GCM 암호문만 보관.
  rotate_keys 호출 시 기존 키는 즉시 무효.

서비스 삭제 규칙:
  구독 이력이 1건이라도 있으면 삭제 불가(스펙) — 비활성화(INACTIVE)를 권장.
  담당자 User 계정은 DB ON DELETE CASCADE로 함께 삭제된다(의도적 설계).

취소 정책(요청 012):
  cancellation_enabled — 단건(ONE_OFF) 결제 취소 허용 여부(기본 True).
  cancellation_fee_percent — 취소 시 차감 수수료율(0~100%, 기본 0).
  update_cancel_policy로 등록 후에도 변경 가능.
"""

import ipaddress
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import AesGcmCipher
from app.core.deps import strip_loopback_ips
from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.core.security import (
    generate_hmac_secret,
    generate_service_api_key,
    sha256_hex,
)
from app.models import (
    Plan,
    Service,
    ServiceStatus,
    Subscription,
    User,
    UserRole,
    UserService,
    UserStatus,
)
from app.services.audit import record_audit


@dataclass
class IssuedCredentials:
    service: Service
    api_key: str       # 평문 — 호출측이 즉시 전달해야 함(DB에는 해시만 저장)
    hmac_secret: str   # 평문 — 동일


def _validate_ips(ips: list[str]) -> list[str]:
    """IPv4 주소 목록 검증.

    빈 목록을 허용한다 — 빈 목록 = "IP 제한 없음(모든 IP 허용)"을 의미하며,
    이 경우 API 호출은 HMAC 서명으로만 보호된다(IP 화이트리스트 미적용, app/api/deps.py).
    값이 있으면 각 항목은 표준 IPv4 형식이어야 한다.

    옥텟 입력 UI(요청 005)와 일치 — IPv6·CIDR은 허용하지 않는다.
    """
    for ip in ips:
        try:
            ipaddress.IPv4Address(ip)  # 옥텟 입력 UI와 일치 — IPv4 전용(요청 005)
        except ValueError as exc:
            raise InputValidationError(f"유효하지 않은 IP: {ip}") from exc
    # 127.0.0.1(같은 서버, 로컬)은 무조건 허용이라 목록에 저장하지 않는다
    return strip_loopback_ips(ips)


async def _validate_managers(db: AsyncSession, manager_user_ids: list[uuid.UUID],
                             primary_user_id: uuid.UUID | None) -> list[User]:
    """담당자 검증: 대표 자동 포함 + 중복 제거, 존재/역할 확인. 대표가 첫번째."""
    if primary_user_id is None:
        raise InputValidationError("담당자를 1명 이상 선택해야 합니다")
    ordered: list[uuid.UUID] = [primary_user_id]
    for uid in manager_user_ids:
        if uid not in ordered:
            ordered.append(uid)
    users: list[User] = []
    for uid in ordered:
        user = await db.get(User, uid)
        if (user is None or user.status == UserStatus.DELETED
                or user.role != UserRole.SERVICE_MANAGER):
            raise InputValidationError("서비스 담당자 계정만 선택할 수 있습니다")
        users.append(user)
    return users


async def register_service(db: AsyncSession, cipher: AesGcmCipher,
                           *, name: str, allowed_ips: list[str],
                           manager_user_ids: list[uuid.UUID],
                           primary_user_id: uuid.UUID | None,
                           cancellation_enabled: bool = True,
                           cancellation_fee_percent: int = 0,
                           toss_secret_key: str | None = None,   # 서비스별 토스 시크릿(선택; AES 암호화 저장)
                           actor_user_id: uuid.UUID | None = None,
                           admin_notifier=None) -> IssuedCredentials:
    """서비스 등록 + 자격증명 발급.

    흐름:
    1. 서비스명 공백 검증
    2. allowed_ips IPv4 유효성 검증(빈 목록 허용 = IP 제한 없음)
    3. cancellation_fee_percent 0~100 범위 검증
    4. 담당자 목록 검증 — primary_user_id를 첫 번째로, 나머지는 중복 제거 후 추가
       (DELETED·비SERVICE_MANAGER 계정 거부)
    5. 서비스명 중복 확인(SELECT 선조회)
    6. API 키 + HMAC secret 생성(평문은 반환 후 파기)
    7. Service 생성:
       - manager_email = managers[0].email (대표 담당자 알림 수신처)
       - api_key_hash = SHA-256 해시(검증용)
       - api_key_encrypted / hmac_secret_encrypted = AES-GCM 암호문(운영자 조회용)
       - cancellation_enabled / cancellation_fee_percent = 단건결제 취소 정책
    8. flush → IntegrityError 경쟁 처리(동시 등록 시 유니크 제약이 최종 심판)
    9. 담당자 UserService 다대다 배정:
       - user.service_id가 None이면 주 서비스로 직접 할당
       - 이미 주 서비스가 있으면 UserService 추가(다대다)
       accounts.assign_service와 동일 규칙이며, 커밋을 여기서 1회만 수행
    10. 감사 로그 → 커밋

    반환: IssuedCredentials(service, api_key 평문, hmac_secret 평문)
    """
    name = (name or "").strip()
    if not name:
        raise InputValidationError("서비스명은 필수입니다")
    # 검증 + 루프백(127.0.0.1/::1) 제거 — 저장·표시 목록에서 빠진다(항상 허용)
    allowed_ips = _validate_ips(allowed_ips)
    # 취소 수수료율 범위 검증 — 0~100%만 허용
    if not 0 <= cancellation_fee_percent <= 100:
        raise InputValidationError("취소 수수료율은 0~100 사이여야 합니다")
    managers = await _validate_managers(db, manager_user_ids, primary_user_id)
    if await db.scalar(select(Service).where(Service.name == name)):
        raise ConflictError("이미 등록된 서비스명입니다")

    api_key = generate_service_api_key()
    hmac_secret = generate_hmac_secret()
    service = Service(name=name, allowed_ips=allowed_ips,
                      manager_email=managers[0].email,  # 대표 계정 = 알림 수신처
                      api_key_hash=sha256_hex(api_key),
                      api_key_encrypted=cipher.encrypt(api_key),
                      hmac_secret_encrypted=cipher.encrypt(hmac_secret),
                      cancellation_enabled=cancellation_enabled,        # 취소 허용 여부
                      cancellation_fee_percent=cancellation_fee_percent,  # 취소 수수료율
                      # toss_secret_key가 전달된 경우 AES 암호화해서 저장; 평문은 보관하지 않음
                      toss_secret_key_encrypted=(cipher.encrypt(toss_secret_key.strip())
                                                 if toss_secret_key and toss_secret_key.strip()
                                                 else None))
    db.add(service)
    try:
        await db.flush()
    except IntegrityError:
        # 동시 등록 경쟁 — 유니크 제약이 최종 심판
        await db.rollback()
        raise ConflictError("이미 등록된 서비스명입니다") from None

    # 선택 계정 할당 — accounts.assign_service와 동일 규칙(주 없으면 주, 있으면 추가).
    # 커밋은 register_service가 묶어서 1회 수행하므로 여기서 직접 처리.
    for user in managers:
        if user.service_id is None:
            user.service_id = service.id
        else:
            db.add(UserService(user_id=user.id, service_id=service.id))

    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.register", target_type="service",
                       target_id=str(service.id),
                       detail={"name": name, "manager_count": len(managers),
                               "manager_emails": [u.email for u in managers],
                               "ip_count": len(allowed_ips),
                               "cancel_enabled": cancellation_enabled,
                               "cancel_fee_percent": cancellation_fee_percent})
    # toss_secret_key가 설정된 경우 별도 감사 기록 — 평문 값은 절대 기록하지 않음
    if toss_secret_key and toss_secret_key.strip():
        await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                           action="service.toss_secret_key.set", target_type="service",
                           target_id=str(service.id),
                           detail={"service_name": service.name})
    await db.commit()
    # 시스템 관리자 전원에게 '새 서비스 등록' 알림 메일(best-effort, 커밋 후라 실패해도 무해)
    if admin_notifier is not None:
        await admin_notifier.service_created(
            db, service=service, manager_emails=[u.email for u in managers],
            actor_user_id=actor_user_id)
    return IssuedCredentials(service=service, api_key=api_key, hmac_secret=hmac_secret)


async def _get_service(db: AsyncSession, service_id: uuid.UUID) -> Service:
    """단일 서비스 조회. 없으면 NotFoundError."""
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    return service


async def reveal_keys(db: AsyncSession, cipher: AesGcmCipher, service_id: uuid.UUID,
                      actor_user_id: uuid.UUID | None = None):
    """평문 키 조회(키 복사 모달용) — 복호화 + 감사 기록 + commit을 한 단위로 처리.

    평문 키 노출은 반드시 감사 로그와 함께여야 하므로 라우트가 아닌 여기서
    묶어서 수행한다(감사 Phase 4 — S8: 라우트 직접 commit 예외 제거).
    복호화 실패는 500으로 새지 않도록 decrypt_error 플래그로 반환한다.

    반환: (service, api_key, hmac_secret, decrypt_error)
    """
    service = await _get_service(db, service_id)
    decrypt_error = False
    api_key = hmac_secret = None
    try:
        api_key = (cipher.decrypt(service.api_key_encrypted)
                   if service.api_key_encrypted else None)
        hmac_secret = cipher.decrypt(service.hmac_secret_encrypted)
    except Exception:  # noqa: BLE001 — 복호화 실패가 500으로 새지 않게 화면 안내로
        decrypt_error = True
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.keys_viewed", target_type="service",
                       target_id=str(service.id),
                       detail={"note": "API 키·HMAC 시크릿 조회(키 복사)"})
    await db.commit()
    return service, api_key, hmac_secret, decrypt_error


async def rotate_keys(db: AsyncSession, cipher: AesGcmCipher, service_id: uuid.UUID,
                      actor_user_id: uuid.UUID | None = None) -> tuple[str, str]:
    """API 키/HMAC secret 재발급. 기존 키는 즉시 무효."""
    service = await _get_service(db, service_id)
    api_key = generate_service_api_key()
    hmac_secret = generate_hmac_secret()
    service.api_key_hash = sha256_hex(api_key)
    service.api_key_encrypted = cipher.encrypt(api_key)
    service.hmac_secret_encrypted = cipher.encrypt(hmac_secret)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.rotate_keys", target_type="service",
                       target_id=str(service.id),
                       detail={"note": "API 키·HMAC 시크릿 재발급(기존 키 무효화)"})
    await db.commit()
    return api_key, hmac_secret


async def set_toss_secret_key(db: AsyncSession, cipher: AesGcmCipher, *,
                              service_id: uuid.UUID, toss_secret_key: str,
                              actor_user_id: uuid.UUID | None = None) -> None:
    """서비스의 토스 시크릿 키를 설정/교체한다(AES 암호화 저장).

    빈 값은 거부한다. 기존에 키가 있었으면 'changed', 없었으면 'set'으로 감사 기록한다.
    감사로그에는 시크릿 값을 절대 남기지 않는다.
    """
    secret = (toss_secret_key or "").strip()
    if not secret:
        raise InputValidationError("토스 시크릿 키는 비어 있을 수 없습니다")
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    had_key = bool(service.toss_secret_key_encrypted)  # 기존 키 존재 여부(set vs changed 구분)
    service.toss_secret_key_encrypted = cipher.encrypt(secret)  # 평문은 저장하지 않음
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action=("service.toss_secret_key.changed" if had_key
                               else "service.toss_secret_key.set"),
                       target_type="service", target_id=str(service_id),
                       detail={"service_name": service.name})   # 시크릿 값 미기록
    await db.commit()


async def clear_toss_secret_key(db: AsyncSession, *, service_id: uuid.UUID,
                                actor_user_id: uuid.UUID | None = None) -> None:
    """서비스의 토스 시크릿 키를 삭제(제거)한다.

    키가 이미 없으면 변화 없이 반환한다(멱등). 삭제는 'deleted' 액션으로 감사 기록하며,
    설정/교체와 마찬가지로 시크릿 값 자체는 절대 남기지 않는다.
    키 삭제 후에는 그 서비스의 결제·구독 첫 결제·자동연장이 거부된다(키 미설정 상태).
    """
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    if not service.toss_secret_key_encrypted:
        return  # 이미 미설정 — 멱등 no-op
    service.toss_secret_key_encrypted = None
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.toss_secret_key.deleted",
                       target_type="service", target_id=str(service_id),
                       detail={"service_name": service.name})   # 시크릿 값 미기록
    await db.commit()


async def update_allowed_ips(db: AsyncSession, service_id: uuid.UUID, ips: list[str],
                             actor_user_id: uuid.UUID | None = None) -> Service:
    """허용 IP 목록 전체 교체. 빈 목록 허용 = IP 제한 없음(모든 IP 허용, HMAC로만 보호)."""
    service = await _get_service(db, service_id)
    old_ips = list(service.allowed_ips or [])   # 변경 전 IP 목록 캡처(감사 전/후 비교)
    service.allowed_ips = _validate_ips(ips)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.update_ips", target_type="service",
                       target_id=str(service.id),
                       detail={"old_ips": old_ips, "new_ips": service.allowed_ips})
    await db.commit()
    return service


async def update_cancel_policy(db: AsyncSession, service_id: uuid.UUID, *,
                               enabled: bool, fee_percent: int,
                               actor_user_id: uuid.UUID | None = None) -> Service:
    """취소 정책(허용 여부·수수료율) 업데이트.

    흐름:
    1. 서비스 조회 — 없으면 NotFoundError
    2. fee_percent 0~100 범위 검증
    3. cancellation_enabled / cancellation_fee_percent 갱신
    4. 감사 로그(service.cancel_policy_updated) → 커밋

    반환: 갱신된 Service
    """
    service = await _get_service(db, service_id)
    if not 0 <= fee_percent <= 100:
        raise InputValidationError("취소 수수료율은 0~100 사이여야 합니다")
    # 변경 전 정책 캡처(감사 전/후 비교)
    detail = {"old_enabled": service.cancellation_enabled, "new_enabled": enabled,
              "old_fee_percent": service.cancellation_fee_percent,
              "new_fee_percent": fee_percent}
    service.cancellation_enabled = enabled
    service.cancellation_fee_percent = fee_percent
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.cancel_policy_updated", target_type="service",
                       target_id=str(service.id), detail=detail)
    await db.commit()
    return service


async def set_service_status(db: AsyncSession, service_id: uuid.UUID, status: str,
                             actor_user_id: uuid.UUID | None = None) -> Service:
    """서비스 활성/비활성 상태 전환.

    ACTIVE ↔ INACTIVE만 허용 — 삭제된 서비스는 이 함수로 복구 불가.
    구독이 있어 delete_service가 불가능할 때 대신 INACTIVE로 전환한다.
    """
    if status not in (ServiceStatus.ACTIVE, ServiceStatus.INACTIVE):
        raise InputValidationError(f"유효하지 않은 상태: {status}")
    service = await _get_service(db, service_id)
    old_status = service.status   # 변경 전 상태 캡처(활성↔비활성 전/후)
    service.status = status
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.set_status", target_type="service",
                       target_id=str(service.id),
                       detail={"old_status": old_status, "new_status": status})
    await db.commit()
    return service


async def delete_service(db: AsyncSession, service_id: uuid.UUID,
                         actor_user_id: uuid.UUID | None = None) -> None:
    """구독 이력이 하나라도 있으면 삭제 불가(스펙 + FK RESTRICT). 비활성화를 권장.

    주의: 이 서비스에 연결된 담당자 User(및 그 setup 토큰)는 DB 레벨
    ON DELETE CASCADE로 함께 삭제된다 — 서비스 없는 담당자 계정은 무의미하므로
    의도된 동작이며, 감사 로그 detail에 기록한다.

    흐름:
    1. 서비스 조회
    2. Subscription 수 확인 → 1 이상이면 ConflictError
    3. 요금제 먼저 하드 삭제(구독 없으므로 안전 — 요금제 FK도 통과)
    4. 서비스 삭제(담당자 User는 CASCADE)
    5. 감사 로그(cascade_deleted_managers 수 포함) → 커밋
    """
    service = await _get_service(db, service_id)
    sub_count = await db.scalar(select(func.count()).select_from(Subscription)
                                .where(Subscription.service_id == service_id))
    if sub_count:
        raise ConflictError("구독 이력이 있는 서비스는 삭제할 수 없습니다. 비활성화를 사용하세요.")
    manager_count = await db.scalar(select(func.count()).select_from(User)
                                    .where(User.service_id == service_id))
    # 요금제 먼저 제거(구독이 없으므로 안전)
    for plan in (await db.scalars(select(Plan).where(Plan.service_id == service_id))).all():
        await db.delete(plan)
    await db.delete(service)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.delete", target_type="service",
                       target_id=str(service_id),
                       detail={"name": service.name,
                               "cascade_deleted_managers": manager_count})
    await db.commit()


async def list_services(db: AsyncSession) -> list[Service]:
    """등록 순으로 전체 서비스 목록 반환. 스코프 제한 없음(SYSTEM_ADMIN용)."""
    return list((await db.scalars(select(Service).order_by(Service.created_at))).all())


async def set_primary_manager(db: AsyncSession, service_id: uuid.UUID, *,
                              user_id: uuid.UUID,
                              actor_user_id: uuid.UUID | None = None) -> Service:
    """대표 담당자 지정 — manager_email(알림 수신처)을 해당 계정 이메일로 갱신.

    조건:
    - 대상 계정이 SERVICE_MANAGER 역할이고 DELETED 상태가 아닐 것
    - 이미 이 서비스의 담당자일 것(User.service_id 또는 UserService 다대다 중 하나)
    """
    service = await _get_service(db, service_id)
    user = await db.get(User, user_id)
    if (user is None or user.status == UserStatus.DELETED
            or user.role != UserRole.SERVICE_MANAGER):
        raise InputValidationError("서비스 담당자 계정만 대표로 지정할 수 있습니다")
    is_manager = user.service_id == service_id or await db.scalar(
        select(UserService).where(UserService.user_id == user_id,
                                  UserService.service_id == service_id)) is not None
    if not is_manager:
        raise InputValidationError("이 서비스의 담당자가 아닙니다")
    old_email = service.manager_email   # 변경 전 대표(알림 수신처) 캡처
    service.manager_email = user.email
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.set_primary_manager", target_type="service",
                       target_id=str(service.id),
                       detail={"old_email": old_email, "new_email": user.email})
    await db.commit()
    return service
