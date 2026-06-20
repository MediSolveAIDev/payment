"""토스페이먼츠 웹훅 이벤트 수신 기록 모델.

토스가 서버로 보내는 비동기 이벤트(결제 완료·취소 등)를 먼저 저장한 뒤
처리 결과(PROCESSED/IGNORED/FAILED)를 갱신한다.
transmission_id로 중복 수신을 방지해 멱등성을 보장한다.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import WebhookStatus


class WebhookEvent(Base):
    """토스페이먼츠가 전송한 웹훅 이벤트 1건."""

    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    transmission_id: Mapped[str] = mapped_column(String(100), unique=True)  # 토스가 부여한 전송 고유 ID(중복 수신 방지에 사용)
    event_type: Mapped[str] = mapped_column(String(100))                     # 이벤트 종류(예: PAYMENT_STATUS_CHANGED)
    payload: Mapped[dict] = mapped_column(JSONB)                             # 토스 웹훅 원문 페이로드(JSONB)
    status: Mapped[str] = mapped_column(String(20), default=WebhookStatus.RECEIVED)  # 처리 상태(RECEIVED → PROCESSED/IGNORED/FAILED)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())  # 웹훅 수신 시각(UTC)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)      # 처리 완료 시각(UTC); 미처리 시 NULL
