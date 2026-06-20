"""관리자 계정(User) 및 비밀번호 설정 토큰(PasswordSetupToken) 모델.

User: htmx 관리 화면에 로그인하는 사내 관리자 계정.
  - SYSTEM_ADMIN은 전체 관리 권한, SERVICE_MANAGER는 담당 서비스만 접근.
  - 외부 서비스 사용자(end-user)가 아님. 구독의 external_user_id와 무관.

PasswordSetupToken: 신규 계정 생성 후 이메일로 발송하는 비밀번호 초기 설정 링크 토큰.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import UserStatus


class User(TimestampMixin, Base):
    """htmx 관리 화면 로그인 계정.

    SYSTEM_ADMIN은 service_id가 NULL, SERVICE_MANAGER는 담당 서비스의 service_id를 가진다.
    비밀번호는 Argon2id 해시로만 저장(평문 불가). 연속 로그인 실패 시 잠금 처리.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True)  # 로그인 ID 겸 연락처(전체 고유)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)  # 연락처(선택)
    password_hash: Mapped[str] = mapped_column(String(512), default="")   # Argon2id 해시; PENDING 상태에서는 빈 문자열
    role: Mapped[str] = mapped_column(String(20))                          # UserRole: SYSTEM_ADMIN | SERVICE_MANAGER
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=True)  # 주 담당 서비스; SYSTEM_ADMIN은 NULL, 서비스 삭제 시 CASCADE
    status: Mapped[str] = mapped_column(String(20), default=UserStatus.PENDING)  # 계정 상태 머신 현재 위치
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)   # 연속 로그인 실패 횟수(임계 초과 시 LOCKED)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 자동 잠금 해제 시각(UTC); NULL이면 잠금 없음


class PasswordSetupToken(Base):
    """비밀번호 초기 설정 링크 토큰.

    신규 계정 생성 시 이메일로 발송하는 일회용 링크에 포함되는 토큰.
    평문은 이메일 링크에만 존재하며, DB에는 SHA-256 해시만 저장.
    사용 후(used_at 채워짐)에는 재사용 불가.
    """

    __tablename__ = "password_setup_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))  # 계정 삭제 시 토큰도 CASCADE 삭제
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 해시(평문 불저장); 전체 고유
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))                   # 링크 유효 기한(UTC)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 최초 사용 시각(UTC); NULL이면 미사용
