"""전역 운영 설정(단일 행) — 자동결제 재시도·어드민 IP·결제서버 킬스위치.

DB에 id=1 단일 행만 존재하며, get_or_create 패턴으로 항상 해당 행에 접근한다.
런타임 변경이 즉시 배치·어드민 IP 검사·외부 API 게이트에 반영된다.
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class GlobalSettings(TimestampMixin, Base):
    """런타임 변경 가능한 전역 설정. 항상 id=1 단일 행만 존재(get_or_create)."""

    __tablename__ = "global_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)  # 싱글톤 행(항상 1)
    retry_limit: Mapped[int] = mapped_column(Integer, default=4, server_default="4")            # 자동결제 실패 재시도 횟수
    retry_interval_hours: Mapped[int] = mapped_column(Integer, default=12, server_default="12")  # 재시도 간격(시간)
    suspended_grace_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")  # SUSPENDED 만료 유예(일)
    # 어드민 로그인 보안 정책(런타임 조정). .env(max_failed_logins/account_lock_minutes)는 비상 폴백.
    max_failed_logins: Mapped[int] = mapped_column(Integer, default=5, server_default="5")        # 연속 실패 잠금 임계치
    account_lock_minutes: Mapped[int] = mapped_column(Integer, default=15, server_default="15")   # 잠금 지속(분)
    # 단건 결제 1회 최대 금액(원, 런타임 조정). .env(one_off_max_amount)는 비상 폴백.
    one_off_max_amount: Mapped[int] = mapped_column(BigInteger, default=100_000_000, server_default="100000000")
    admin_allowed_ips: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")    # 어드민 접속 허용 IP(빈=제한없음)
    server_disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # 결제서버 킬스위치
    disabled_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)              # 비활성화 사유(서비스 API 반환)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 비활성화 시각(UTC)
    disabled_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)                          # 비활성화한 관리자 user id
