"""토스페이먼트 웹훅 수신 처리 서비스.

처리 흐름(handle_webhook):
  1. transmission-id 헤더 필수 확인 — 없으면 즉시 거부(멱등 식별 불가)
  2. 중복 수신 검사 — 동일 transmission_id가 있으면 기존 이벤트 반환(멱등)
  3. WebhookEvent INSERT(PENDING) → flush
     동시 경쟁 IntegrityError → 롤백 후 기존 행 재조회
  4. 이벤트 타입별 핸들러 실행:
     - BILLING_DELETED      → _handle_billing_deleted  (빌링키 삭제 알림 메일)
     - PAYMENT_STATUS_CHANGED → _handle_payment_status_changed (결제 상태 동기화)
     - 그 외               → IGNORED
  5. 예외 분기:
     - TossError(일시 오류) → 롤백 후 재발생 → 토스가 재전송(200 주면 영구 유실)
     - 일반 Exception(영구 실패) → FAILED 기록 + 200 반환 → 무한 재전송 방지
       (운영 reaper가 FAILED 이벤트 점검)
  6. processed_at 기록 → 커밋

WebhookStatus 정리:
  PROCESSED  정상 처리 완료
  IGNORED    알 수 없는 이벤트 타입 — 기록만 남기고 무시
  FAILED     영구 처리 불가 — 운영자 수동 처리 대상
"""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.errors import InputValidationError
from app.core.security import sha256_hex
from app.models import (
    Card,  # Task 9: billing_key_hash는 cards 테이블로 이동 — Card로 빌링키 조회
    Payment,
    PaymentStatus,
    Service,
    Subscription,
    SubscriptionStatus,
    WebhookEvent,
    WebhookStatus,
)
from app.notifications.email import EmailSender
from app.toss.client import TossClient
from app.toss.errors import TossError

logger = logging.getLogger("payment.webhooks")


def _sanitize(value: str, *, limit: int = 200) -> str:
    """외부 페이로드 문자열을 메일/로그에 넣기 전 개행·제어문자 제거."""
    cleaned = "".join(ch for ch in str(value) if ch == " " or ch.isprintable())
    return cleaned[:limit]


async def handle_webhook(db: AsyncSession, toss: TossClient, email_sender: EmailSender,
                         *, transmission_id: str | None, payload: dict) -> WebhookEvent:
    """토스 웹훅 단일 진입점. 멱등 + 재전송 정책을 여기서 결정한다.

    transmission_id 필수:
    - 합성 ID를 만들면 헤더 없는 위조 재전송이 dedup을 우회해 무한 적재되므로
      transmission_id가 없는 요청은 InputValidationError로 즉시 거부한다.

    재전송 정책:
    - TossError(일시적) → 롤백 후 재발생 → HTTP 4xx/5xx 반환 → 토스 재전송 유도
    - 일반 Exception(영구) → FAILED 기록 + HTTP 200 반환 → 무한 재전송 방지
      (재전송해도 같은 실패가 반복되는 이벤트는 200으로 토스 큐에서 제거)
    """
    event_type = str(payload.get("eventType", "UNKNOWN"))
    # transmission_id가 없으면 멱등 식별이 불가능 — 토스는 항상 보낸다.
    # 합성 ID를 만들면 헤더 없는 위조 재전송이 dedup을 우회해 무한 적재되므로 거부.
    if not transmission_id:
        raise InputValidationError("웹훅 transmission-id 헤더가 필요합니다")

    existing = await db.scalar(select(WebhookEvent).where(
        WebhookEvent.transmission_id == transmission_id))
    if existing is not None:
        return existing  # 중복 수신 — 멱등 처리

    event = WebhookEvent(transmission_id=transmission_id, event_type=event_type,
                         payload=payload)
    db.add(event)
    try:
        await db.flush()
    except IntegrityError:
        # 동시 중복 수신 — 다른 트랜잭션이 먼저 기록함. 멱등하게 기존 행 반환.
        await db.rollback()
        existing = await db.scalar(select(WebhookEvent).where(
            WebhookEvent.transmission_id == transmission_id))
        if existing is not None:
            return existing
        raise

    try:
        if event_type == "BILLING_DELETED":
            await _handle_billing_deleted(db, email_sender, payload)
            event.status = WebhookStatus.PROCESSED
        elif event_type == "PAYMENT_STATUS_CHANGED":
            await _handle_payment_status_changed(db, toss, payload)
            event.status = WebhookStatus.PROCESSED
        else:
            event.status = WebhookStatus.IGNORED
    except TossError:
        # 일시적 오류(토스 재조회 실패 등) — 기록을 롤백하고 재발생시켜
        # 토스가 재전송하게 한다(200을 주면 영구 유실되므로).
        logger.warning("webhook 일시 오류 — 재전송 유도: %s", event_type)
        await db.rollback()
        raise
    except Exception:
        # 처리 불가(영구) — FAILED로 기록하고 200 반환(무한 재전송 방지).
        # 운영 reaper가 FAILED 이벤트를 점검한다.
        logger.exception("webhook 처리 실패(영구): %s", event_type)
        event.status = WebhookStatus.FAILED
    event.processed_at = utcnow()
    await db.commit()
    return event


async def _handle_billing_deleted(db: AsyncSession, email_sender: EmailSender,
                                  payload: dict) -> None:
    """토스 BILLING_DELETED 이벤트 처리 — 서비스 담당자에게 알림 메일 발송.

    빌링키를 해시(SHA-256)로 조회하는 이유:
    - DB에 평문 빌링키를 저장하지 않으므로, 페이로드의 billingKey를 해시로 변환해 조회.

    TRIAL / SUSPENDED / EXPIRED 구독 제외 이유:
    - 조회 조건: Subscription.status.in_((ACTIVE, PAST_DUE, CANCELED)) 만 대상.
    - TRIAL: 체험 구독도 가입 시 빌링키를 발급받지만, 체험 만료 배치가
      직접 결제를 수행하므로 빌링키 삭제 알림 대상에서 제외.
    - SUSPENDED: 다음 자동 갱신이 예약되지 않은 상태 — 알림 불필요.
    - EXPIRED: 이미 완전 종료 — 갱신 결제 없음.
    즉, 향후 정기 갱신 결제가 예정된(ACTIVE·PAST_DUE·CANCELED) 구독에만 알림이 의미 있다.

    빌링키가 없거나 해당 구독을 찾지 못하면 조용히 반환(이벤트는 PROCESSED로 기록).
    """
    data = payload.get("data") or {}
    billing_key = str(data.get("billingKey", ""))
    if not billing_key:
        return
    # Task 9: billing_key_hash는 Subscription에서 Card로 이동됐다.
    # 1) billingKey 해시로 Card를 조회한다.
    # 2) 해당 카드를 참조하는 활성 구독(ACTIVE·PAST_DUE·CANCELED)을 찾는다.
    card = await db.scalar(
        select(Card).where(Card.billing_key_hash == sha256_hex(billing_key)))
    if card is None:
        return  # 우리 DB에 없는 빌링키 — 조용히 무시(PROCESSED로 기록)
    sub = await db.scalar(select(Subscription).where(
        Subscription.card_id == card.id,
        Subscription.status.in_((SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE,
                                 SubscriptionStatus.CANCELED))))
    if sub is None:
        return
    service = await db.get(Service, sub.service_id)
    # service가 DB에 없는 경우(데이터 불일치 등)는 조용히 반환 — 메일 미발송
    if service is None:
        return
    reason = _sanitize(data.get("reason", "알 수 없음"))
    await email_sender.send(
        service.manager_email,
        f"[결제시스템] 빌링키 삭제 감지 — {service.name}",
        f"사용자 {sub.external_user_id}의 빌링키가 토스에서 삭제되었습니다 "
        f"(사유: {reason}).\n"
        f"다음 갱신 결제가 실패할 수 있으니 카드 재등록을 안내해주세요.")


async def _handle_payment_status_changed(db: AsyncSession, toss: TossClient,
                                         payload: dict) -> None:
    """페이로드는 신뢰하지 않는다 — orderId만 취해 토스 API 재조회로 상태 확정.

    페이로드 위조·오염 방지:
    - 페이로드의 status를 직접 사용하지 않고, orderId로 토스 API를 재조회해
      최신 상태를 서버 측에서 확정한다(신뢰 경계 밖 입력 거부 원칙).

    처리 범위:
    - CANCELED 상태만 처리 — 결제 취소 동기화.
    - 그 외 상태 변경(예: PARTIAL_CANCELED)은 현재 미처리.

    우리 DB에 없는 orderId이면 조용히 반환 — 타 서비스 결제이거나 위조.
    """
    data = payload.get("data") or {}
    order_id = str(data.get("orderId", ""))
    if not order_id:
        return
    # 웹훅의 orderId는 토스 측 식별자 → 전역 고유 toss_order_id로 조회한다
    # (order_id는 서비스 내 고유라 다건 매칭될 수 있음 — 감사 Phase 2, 보안 M-1)
    payment = await db.scalar(select(Payment).where(Payment.toss_order_id == order_id))
    if payment is None:
        return  # 우리 주문이 아님
    verified = await toss.get_payment_by_order_id(order_id)
    if verified is None:
        return  # 토스에서 확인 불가 — 위조 의심, 무시
    if verified.status == "CANCELED" and payment.status != PaymentStatus.CANCELED:
        payment.status = PaymentStatus.CANCELED
        payment.canceled_amount = payment.amount   # 외부 전액취소 동기화
        payment.canceled_at = utcnow()
        payment.raw_response = verified.raw
