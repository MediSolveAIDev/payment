"""감사 로그(AuditLog) 기록 서비스.

record_audit 은 db.add만 수행하고 commit을 호출하지 않는다.
호출자(라우트, 서비스)가 비즈니스 로직과 같은 트랜잭션 안에서 commit을 하므로
"상태 변경 + 감사 기록"이 원자적으로 커밋되거나 함께 롤백된다.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def record_audit(db: AsyncSession, *, actor_type: str, action: str,
                       actor_user_id: uuid.UUID | None = None,
                       actor_service_id: uuid.UUID | None = None,
                       target_type: str | None = None, target_id: str | None = None,
                       detail: dict | None = None, ip_address: str | None = None) -> None:
    """감사 로그 한 건을 세션에 추가한다. commit은 호출자가 묶어서 수행한다.

    이 함수는 db.add만 호출하며 commit을 하지 않는다. 호출자는 비즈니스 로직
    변경(구독 생성, 결제, 사용자 수정 등)과 동일한 트랜잭션 내에서 commit해야
    상태 변경과 감사 기록이 원자적으로 반영된다. 만약 이 함수 단독으로
    commit을 수행하면, 이후 비즈니스 로직이 롤백되더라도 감사 기록만 남게 된다.

    Args:
        db: 현재 요청의 AsyncSession. 호출자가 관리하는 트랜잭션에 속한다.
        actor_type: 행위자 유형 ("USER" | "SERVICE" | "SYSTEM").
        action: 수행된 동작 식별자 (예: "subscription.create", "audit.purge").
        actor_user_id: 행위자가 사용자인 경우의 UUID.
        actor_service_id: 행위자가 외부 서비스인 경우의 UUID.
        target_type: 대상 엔티티 유형 (예: "service", "plan", "subscription").
        target_id: 대상 엔티티 ID 문자열 (UUID 또는 기타).
        detail: 추가 컨텍스트를 담는 자유 형식 JSON 딕셔너리.
        ip_address: 요청 출처 IP.
    """
    db.add(AuditLog(actor_type=actor_type, action=action, actor_user_id=actor_user_id,
                    actor_service_id=actor_service_id,
                    target_type=target_type, target_id=target_id,
                    detail=detail, ip_address=ip_address))
