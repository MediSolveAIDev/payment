"""결제(Payment) 레코드 모델.

구독 정기결제(SUBSCRIPTION)와 단건 결제(ONE_OFF) 모두 이 테이블에 기록된다.
구독 결제: subscription_id가 채워지고 payment_type으로 회차(FIRST/RENEWAL/RETRY)를 구분.
단건 결제: subscription_id=NULL, payment_type=ONE_OFF.
토스페이먼츠 API 응답 원문은 raw_response에 보관해 사후 분석에 활용한다.
"""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import PaymentKind, PaymentStatus


def receipt_url_from_raw(raw) -> str | None:
    """토스 응답 원문(raw_response)에서 매출전표(영수증) URL을 안전하게 추출한다.

    토스 Payment 객체의 receipt.url = 카드결제 매출전표 링크. 카드결제(DONE)면 보통
    존재하고, 실패·대기·과거 미보유 건은 None이다. 구조가 다르거나 url이 문자열이
    아닌 경우(방어) 모두 None을 반환한다. 어드민 헬퍼(app/admin)와 Payment.receipt_url
    프로퍼티가 공통으로 사용해 추출 로직을 한 곳에 둔다.
    """
    if not isinstance(raw, dict):
        return None
    receipt = raw.get("receipt")
    if not isinstance(receipt, dict):
        return None
    url = receipt.get("url")
    return url if isinstance(url, str) and url else None


class Payment(TimestampMixin, Base):
    """개별 결제 시도 레코드. 승인 성공·실패 모두 기록되며 삭제하지 않는다."""

    __tablename__ = "payments"
    __table_args__ = (
        # order_id는 서비스(테넌트) 내에서만 고유(감사 Phase 2 — 보안 M-1).
        # 과거 전역 유니크였을 때는 서비스 A가 서비스 B의 주문번호를 선점(스쿼팅)해
        # B의 결제를 차단하거나, 409 응답 차이로 타 서비스 주문번호 존재를 탐지할 수 있었다.
        # 토스에 보내는 전역 고유 ID는 toss_order_id가 별도로 담당한다.
        UniqueConstraint("service_id", "order_id", name="uq_payments_service_order"),
        # 정산 스윕(status=PENDING AND requested_at<=… — 5분마다 실행)과
        # 결제목록 기본 정렬(requested_at desc)용(감사 Phase 3 — 성능 M1)
        Index("ix_payments_status_requested", "status", "requested_at"),
        # 대시보드 매출 집계·월별 정산의 승인시각 범위 조회용(감사 Phase 3 — 성능 M1)
        Index("ix_payments_service_approved", "service_id", "approved_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="RESTRICT"), nullable=True, index=True)  # 구독 결제이면 연결 구독 ID; 단건은 NULL
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), index=True)  # 결제가 속한 서비스(삭제 불가)
    external_user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 결제 대상 외부 사용자 ID=이메일(소문자 정규화, 단건에서도 추적용)
    kind: Mapped[str] = mapped_column(
        String(20), default=PaymentKind.SUBSCRIPTION,
        server_default=PaymentKind.SUBSCRIPTION, index=True)  # PaymentKind: SUBSCRIPTION | ONE_OFF
    order_id: Mapped[str] = mapped_column(String(64))                       # 주문 ID(서비스 내 고유 — __table_args__의 복합 유니크 참조)
    # 토스 API에 전달하는 전역 고유 주문 ID(감사 Phase 2 — 보안 M-1).
    # 시스템 전체가 토스 계정 하나를 공유하므로 토스 측 orderId는 전역 고유여야 한다.
    # 서버 생성 ID(단건 결제) 또는 order_id와 동일(구독 결제 — 서버가 생성해 이미 전역 고유).
    # 기본값은 before_insert 이벤트에서 order_id로 채워진다(아래 _default_toss_order_id).
    toss_order_id: Mapped[str] = mapped_column(String(64), unique=True)
    toss_payment_key: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 토스 승인 후 발급되는 paymentKey(취소·조회 시 사용)
    amount: Mapped[int] = mapped_column(BigInteger)                         # 실제 청구 금액(원 단위, KRW 정수)
    # 결제창·결제정보에 표시되는 상품명(토스 orderName). 단건결제는 클라이언트가 전달한 값,
    # 구독결제는 요금제명(plan.name)을 저장한다. 과거 데이터 호환을 위해 nullable.
    order_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_type: Mapped[str] = mapped_column(String(10))                   # PaymentType: FIRST/RENEWAL/RETRY/ONE_OFF
    status: Mapped[str] = mapped_column(String(10), default=PaymentStatus.PENDING)  # PaymentStatus 현재 상태
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)    # 토스 실패 코드(FAILED일 때 채워짐)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True) # 토스 실패 메시지(사용자 표시용)
    idempotency_key: Mapped[str] = mapped_column(String(300))               # 토스 API 멱등성 키(중복 요청 방지)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))                    # 결제 요청 생성 시각(UTC)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 토스 승인 완료 시각(UTC); 실패 시 NULL
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True) # 토스 API 응답 원문(사후 분석·감사용)
    canceled_amount: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 실제 환불액(금액-수수료); 부분취소 시 amount와 다름
    cancel_fee: Mapped[int | None] = mapped_column(BigInteger, nullable=True)        # 차감 수수료(수수료율 × amount // 100)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 취소 완료 시각(UTC)

    @property
    def receipt_url(self) -> str | None:
        """토스 매출전표(영수증) URL — raw_response.receipt.url에서 안전 추출(없으면 None).

        외부 서비스 결제조회 응답(PaymentResponse)과 어드민 결제목록 링크가 함께 사용한다.
        raw_response 전체는 노출하지 않고 이 영수증 링크만 추출해 노출한다.
        """
        return receipt_url_from_raw(self.raw_response)


@event.listens_for(Payment, "before_insert", propagate=True)
def _default_toss_order_id(mapper, connection, target: Payment) -> None:
    """toss_order_id 미지정 시 order_id로 자동 채움.

    구독 결제(FIRST/RENEWAL/RETRY/manual)는 서버가 order_id를 생성하므로 이미 전역
    고유 — 그대로 토스에 보내면 된다. 별도 값이 필요한 곳(단건 결제 — 클라이언트가
    order_id를 지정)만 명시적으로 설정한다. 이벤트 방식이라 기존 생성 지점·테스트
    픽스처를 수정하지 않아도 NOT NULL 제약을 만족한다.
    """
    if target.toss_order_id is None:
        target.toss_order_id = target.order_id
