"""감사 로그(Audit Log) 모델.

시스템 내에서 발생하는 모든 중요 행위(관리자 로그인, 서비스 생성, 구독 변경 등)를
불변 이력으로 기록한다. 행위자는 USER(관리자), SERVICE(API 키 인증 서비스),
SYSTEM(스케줄러 등 자동화) 세 종류로 구분된다.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    """감사 로그 레코드. 삽입만 허용하며 수정·삭제하지 않는다(불변 이력).

    actor_type에 따라 actor_user_id 또는 actor_service_id 중 하나만 채워진다.
    SYSTEM 행위자(스케줄러 등)인 경우 두 필드 모두 NULL이 될 수 있다.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        # 감사 목록 기본 정렬·대시보드 기간 집계용(감사 Phase 3 — 성능 M1).
        # append-only 테이블이라 쓰기 비용 대비 조회 효과가 크다.
        Index("ix_audit_logs_created_at", "created_at"),
        # 대시보드의 target_id IN (구독 서브쿼리)·서비스 상세 이벤트 조회용
        Index("ix_audit_logs_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)   # USER 행위자일 때 users.id
    actor_service_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # SERVICE 행위자
    actor_type: Mapped[str] = mapped_column(String(10))  # USER | SERVICE | SYSTEM
    action: Mapped[str] = mapped_column(String(100), index=True)  # 행위 식별자(예: subscription.cancel)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)   # 대상 엔티티 종류(예: Subscription)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)     # 대상 엔티티 PK(문자열 직렬화)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)            # 변경 전·후 값 등 부가 정보
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)    # 요청 IP(IPv6 포함 최대 45자)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # 이벤트 발생 시각(UTC)
