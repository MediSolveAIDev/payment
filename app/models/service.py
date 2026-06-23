"""사내 외부 서비스(구독·결제를 이용하는 클라이언트) 모델."""
import uuid

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import ServiceStatus


class Service(TimestampMixin, Base):
    """구독/결제 API를 이용하는 사내 서비스 등록 정보.

    서비스가 등록되면 API 키가 발급되고, 외부 서비스는 이 키로 구독·결제 API를 호출한다.
    보안 민감 정보는 평문을 저장하지 않는다(해시 또는 AES 암호화).
    """

    __tablename__ = "services"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)          # 서비스명(전체 고유)
    allowed_ips: Mapped[list] = mapped_column(JSONB, default=list)       # API 호출 허용 IP 목록(JSONB 배열)
    manager_email: Mapped[str] = mapped_column(String(255))              # 서비스 담당자 이메일(초기 계정 생성에 사용)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # SHA-256 해시(인증 검증용, 평문 저장 안 함)
    hmac_secret_encrypted: Mapped[str] = mapped_column(String(512))     # 웹훅 서명 검증용 HMAC 시크릿(AES 암호화 보관)
    # 표시용 평문 API 키(AES 암호화). 인증은 api_key_hash로만 — 요청 005 키 복사
    api_key_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 관리 화면 키 표시용(AES 암호화)
    status: Mapped[str] = mapped_column(String(20), default=ServiceStatus.ACTIVE)       # 서비스 활성 상태
    cancellation_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true")           # 단건결제 취소 허용 여부
    cancellation_fee_percent: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0")                 # 취소 수수료율(0~100, %)
    # 서비스 알림(아웃고잉 웹훅) 수신 URL — 구독·결제·카드·요금제 이벤트를 이 URL로 POST한다.
    # 비어 있으면(NULL) 알림을 보내지 않는다. 서명은 서비스의 hmac_secret_encrypted를 재사용한다.
    notification_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 알림 수신 URL(없으면 미발송)
    # 서비스별 토스 시크릿 키(AES-GCM 암호화 보관). 미설정(NULL)이면 결제·승인·갱신이 TOSS_KEY_NOT_CONFIGURED로 거부된다.
    # 평문은 저장·응답·감사로그 어디에도 남기지 않는다(api_key/hmac과 동일 정책).
    toss_secret_key_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
