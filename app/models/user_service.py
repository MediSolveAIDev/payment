"""관리자(User)와 서비스(Service) 간 다대다 연결 테이블 모델."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UserService(Base):
    """관리자↔서비스 다대다 연결. SERVICE_MANAGER 한 명이 여러 서비스를 담당.

    User.service_id(주 서비스)와 별개로 추가 부여를 표현한다.
    유효 담당 서비스 = User.service_id ∪ 이 테이블의 service_id.
    """

    __tablename__ = "user_services"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)    # 관리자 삭제 시 연결 행 CASCADE 삭제
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), primary_key=True)  # 서비스 삭제 시 연결 행 CASCADE 삭제
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())  # 담당 서비스 부여 시각(UTC)
