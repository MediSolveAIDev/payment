"""단건(일반) 결제 서비스 — 구독 없이 1회성으로 결제하는 API.

Task 9 변경: 단건결제가 카드 보관함 기반으로 전환됨.
- auth_key/customer_key를 더 이상 받지 않고, 사전 등록된 카드(cards 테이블)를 사용한다.
- 빌링키 삭제 로직 제거 — 카드는 영속(persistent)이므로 단건결제 후에도 삭제하지 않는다.
"""
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import default_settings
from app.core.crypto import AesGcmCipher
from app.core.errors import ConflictError, InputValidationError, NotFoundError, PaymentFailedError
from app.models import Payment, PaymentKind, PaymentStatus, PaymentType, Service
from app.notifications.service_notify import (
    EVENT_PAYMENT_ONE_OFF,
    EVENT_PAYMENT_ONE_OFF_ADMIN_CANCELED,
    EVENT_PAYMENT_ONE_OFF_CANCELED,
)
from app.services.app_settings import get_global_settings
from app.services.audit import record_audit
from app.services.billing_math import compute_cancel_fee
from app.services.cards import get_card  # Task 9: 카드 보관함에서 빌링키 조회
from app.services.payment_utils import (
    PENDING_GRACE_MESSAGE,
    resolve_charge,
)
from app.toss.client import TossClient
from app.toss.errors import TossError, TossTimeoutError

ORDER_ID_RE = re.compile(r"^[A-Za-z0-9\-_=.]{6,64}$")

# 단건 결제 1건당 금액 상한(원). 비정상 고액 요청이 토스까지 전달되는 것을 차단
# (감사 Phase 2 — 보안 L-3). .env(one_off_max_amount)로 조정 가능.
# 주의: 기본값보다 더 높이려면 schemas/api.py의 le= 제약도 함께 올려야 한다
#       (API 경계 Pydantic 검증이 먼저 걸린다). 낮추는 방향은 .env만으로 충분.
ONE_OFF_MAX_AMOUNT = default_settings().one_off_max_amount


async def create_one_off_payment(
    db: AsyncSession,
    toss: TossClient,
    cipher: AesGcmCipher,
    *,
    service: Service,
    external_user_id: str,
    order_id: str,
    order_name: str,
    amount: int,
    notifier=None,
) -> Payment:
    """단건 결제 생성.

    결제 3원칙:
    1. PENDING 선커밋 — 네트워크 장애 전에 기록을 내구성 있게 확보
    2. 타임아웃 = PENDING 유지 — 이중 결제 방지를 위해 절대 FAILED 처리 금지
    3. 멱등 order_id — 같은 서비스+order_id 재시도는 기존 Payment 반환(재결제 없음)

    order_id 스코프(감사 Phase 2 — 보안 M-1):
    클라이언트 order_id는 서비스(테넌트) 내에서만 고유하면 된다 — 타 서비스가 같은
    주문번호를 써도 충돌하지 않는다(스쿼팅·존재 탐지 차단). 토스에는 서버가 생성한
    전역 고유 toss_order_id를 전달한다(전 서비스가 토스 계정 하나를 공유하므로).

    Task 9 변경 — 카드 보관함 기반으로 전환:
    - auth_key/customer_key 파라미터 제거. 사전 등록된 카드(cards 테이블)를 조회해 결제한다.
    - 카드가 없으면 NotFoundError — 먼저 POST /cards 로 카드를 등록해야 한다.
    - 빌링키 삭제 없음 — 카드는 영속(persistent)이므로 단건결제 후에도 카드를 유지한다.

    cipher는 카드 보관함에서 빌링키를 복호화할 때 사용한다.
    """
    if not ORDER_ID_RE.fullmatch(order_id or ""):
        raise InputValidationError("order_id 형식이 올바르지 않습니다")
    if not external_user_id or len(external_user_id) > 255:
        raise InputValidationError("external_user_id가 올바르지 않습니다")
    if amount <= 0:
        raise InputValidationError("금액은 1원 이상이어야 합니다")
    # 단건결제 상한은 런타임(전체 설정·GlobalSettings)에서 즉시 조정 가능 — 사고 시 즉시 조이기.
    # .env(ONE_OFF_MAX_AMOUNT)는 GlobalSettings 미가용 시 비상 폴백.
    max_amount = (await get_global_settings(db)).one_off_max_amount
    if amount > max_amount:
        # 비정상 고액 요청 차단(감사 Phase 2 — 보안 L-3). 스키마(le=)와 이중 방어.
        raise InputValidationError(f"금액은 {max_amount:,}원 이하여야 합니다")

    # Task 9: 카드 보관함 조회 — 등록된 카드가 없으면 결제 불가
    # PENDING 선커밋 전에 확인하여 불필요한 Payment 행 생성을 방지한다.
    card = await get_card(db, service_id=service.id, external_user_id=external_user_id)
    if card is None:
        raise NotFoundError("등록된 카드가 없습니다. 먼저 카드를 등록하세요")
    # 비활성 카드는 결제 불가 — 일반결제(one-off) 요청도 차단(카드 활성화 후 재요청)
    if not card.is_active:
        raise ConflictError("비활성화된 카드입니다. 카드를 활성화한 뒤 다시 시도해주세요.")

    # 멱등성 검사: 같은 (서비스, order_id)가 이미 있으면 재결제 없이 반환.
    # 서비스 스코프 조회라 타 서비스의 동일 주문번호와는 충돌·노출이 없다(보안 M-1).
    existing = await db.scalar(select(Payment).where(
        Payment.service_id == service.id, Payment.order_id == order_id))
    if existing is not None:
        return existing

    # PENDING 선커밋 — 결제 전에 내구성 확보
    now = utcnow()
    # 토스 전달용 전역 고유 ID — 클라이언트 order_id는 서비스 간 중복 가능하므로
    # 서버가 생성한다(보안 M-1). 't' 접두 + uuid4 hex = 33자(토스 6~64자 규칙 충족).
    # 토스 멱등키도 같은 값 사용 — 서비스 간 멱등키 충돌(타 서비스 응답 재생) 방지.
    toss_order_id = f"t{uuid.uuid4().hex}"
    payment = Payment(
        subscription_id=None,
        service_id=service.id,
        external_user_id=external_user_id,
        order_id=order_id,
        toss_order_id=toss_order_id,
        order_name=order_name,   # 결제정보에 표시할 상품명(클라이언트가 전달한 orderName)
        amount=amount,
        payment_type=PaymentType.ONE_OFF,
        kind=PaymentKind.ONE_OFF,
        status=PaymentStatus.PENDING,
        idempotency_key=toss_order_id,
        requested_at=now,
    )
    db.add(payment)
    try:
        await db.flush()
    except IntegrityError:
        # 동시 요청 경쟁 — (service_id, order_id) 복합 유니크가 최종 심판
        await db.rollback()
        again = await db.scalar(select(Payment).where(
            Payment.service_id == service.id, Payment.order_id == order_id))
        if again is not None:
            return again
        raise

    await record_audit(
        db, actor_type="SERVICE", actor_service_id=service.id,
        action="payment.one_off", target_type="payment",
        target_id=str(payment.id),
        detail={"external_user_id": external_user_id, "amount": amount},
    )
    await db.commit()

    # Task 9: 카드 보관함에서 빌링키 복호화 — 영속 카드이므로 결제 후 삭제하지 않는다.
    # 구독 갱신(renewals.py)과 동일한 cipher.decrypt 패턴을 사용한다.
    billing_key = cipher.decrypt(card.billing_key_encrypted)

    # 결제 실행
    try:
        result = await resolve_charge(
            toss,
            billing_key=billing_key,          # 카드 보관함에서 복호화한 빌링키
            customer_key=card.customer_key,   # 카드에 저장된 customerKey
            amount=amount,
            # 토스에는 전역 고유 toss_order_id를 전달 — 클라이언트 order_id는
            # 서비스 간 중복될 수 있어 토스 멱등키로도 쓰면 안 된다(보안 M-1)
            order_id=payment.toss_order_id,
            order_name=order_name,
            idempotency_key=payment.toss_order_id,
        )
    except TossTimeoutError as exc:
        # 결과 불명 — 절대 FAILED 처리하지 않는다(이중 결제 위험).
        # PENDING 유지. 카드는 영속이므로 빌링키를 삭제하지 않는다(Task 9).
        await record_audit(
            db, actor_type="SERVICE", actor_service_id=service.id,
            action="payment.one_off_unresolved", target_type="payment",
            target_id=str(payment.id), detail={"order_id": order_id},
        )
        await db.commit()
        raise PaymentFailedError(
            PENDING_GRACE_MESSAGE, code="PAYMENT_UNRESOLVED", http_status=503
        ) from exc
    except TossError as exc:
        payment.status = PaymentStatus.FAILED
        payment.failure_code = exc.code
        payment.failure_message = exc.message
        await record_audit(
            db, actor_type="SERVICE", actor_service_id=service.id,
            action="payment.one_off_failed", target_type="payment",
            target_id=str(payment.id), detail={"code": exc.code},
        )
        await db.commit()
        # 카드는 영속이므로 빌링키를 삭제하지 않는다(Task 9).
        raise PaymentFailedError(f"결제 실패: {exc.message}", code=exc.code) from exc

    # 결제 확정
    payment.status = PaymentStatus.DONE
    payment.toss_payment_key = result.payment_key
    payment.approved_at = utcnow()
    payment.raw_response = result.raw
    await db.commit()
    # 서비스 알림 — 일반결제 성공. best-effort.
    if notifier is not None:
        await notifier.send(service, event=EVENT_PAYMENT_ONE_OFF, order_id=payment.order_id,
                            status=payment.status, email=external_user_id,
                            desc=f"일반결제 {amount:,}원({order_name})")
    # Task 9: 카드는 영속(persistent) — 성공 후에도 빌링키를 삭제하지 않는다.
    return payment


async def cancel_one_off_payment(db: AsyncSession, toss: TossClient, *,
                                 service: Service, order_id: str, reason: str,
                                 actor_user_id: uuid.UUID | None = None,
                                 notifier=None) -> Payment:
    """단건(ONE_OFF) DONE 결제를 취소(환불)한다.

    서비스 정책(cancellation_enabled)이 꺼져 있으면 취소 불가. 수수료율이 있으면
    환불액 = 금액 − (금액 × 수수료% // 100)으로 부분취소하고 수수료는 서비스가 차감한다.
    성공 시 status=CANCELED + canceled_amount/cancel_fee/canceled_at 기록.
    토스 취소 실패 시 상태는 DONE 유지(멱등 재시도 가능). 결제 3원칙과 달리 타임아웃
    PENDING 보존은 적용하지 않는다(취소는 재호출이 안전).

    actor 분기:
    - actor_user_id 있음(Admin 호출): actor_type="USER", actor_user_id로 기록. 실제 행위자는 관리자.
    - actor_user_id 없음(외부 API 호출): actor_type="SERVICE", actor_service_id=service.id로 기록.
    """
    # (service_id, order_id) 스코프 조회 — 타 서비스 결제 접근 차단.
    # order_id가 서비스 내 고유로 바뀌어(보안 M-1) 전역 조회는 다건 매칭될 수 있다.
    payment = await db.scalar(select(Payment).where(
        Payment.service_id == service.id, Payment.order_id == order_id))
    if payment is None:
        raise NotFoundError("결제를 찾을 수 없습니다")

    # ONE_OFF + DONE 상태만 취소 가능 (구독 결제·이미 취소·미완료 결제 차단)
    if payment.kind != PaymentKind.ONE_OFF or payment.status != PaymentStatus.DONE:
        raise ConflictError("취소할 수 없는 결제입니다")

    # 어드민이 이미 부분취소(canceled_amount>0, status는 DONE 유지)한 결제는
    # 외부(사용자) 전액취소가 이중환불/토스 오류를 일으키므로 차단한다.
    if payment.canceled_amount:
        raise ConflictError("이미 부분 취소된 결제입니다. 관리자에게 문의하세요")

    # 서비스 정책 검사 — 취소 허용 여부 확인
    if not service.cancellation_enabled:
        raise PaymentFailedError("취소가 허용되지 않는 서비스입니다", code="CANCEL_DISABLED")

    # 수수료 계산 — 조회 응답·화면 표시와 동일한 공식(compute_cancel_fee)을 공유한다
    fee, refund = compute_cancel_fee(payment.amount, service.cancellation_fee_percent)

    try:
        if refund > 0:
            # refund == amount이면 수수료 없음 → 전액취소(cancelAmount 생략)
            # refund < amount이면 수수료 공제 → 부분취소(cancelAmount=환불액)
            await toss.cancel_payment(
                payment.toss_payment_key, reason,
                cancel_amount=(None if refund == payment.amount else refund))
    except TossError as exc:
        # 토스 취소 실패 — payment 상태는 DONE 유지, 감사 기록 후 예외 재발생
        # actor_user_id가 있으면 Admin(USER) 행위자, 없으면 외부 서비스(SERVICE) 행위자
        if actor_user_id is not None:
            await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                               action="payment.cancel_failed", target_type="payment",
                               target_id=str(payment.id),
                               detail={"external_user_id": payment.external_user_id,
                                       "order_id": payment.order_id,
                                       "code": exc.code, "reason": reason})
        else:
            await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                               action="payment.cancel_failed", target_type="payment",
                               target_id=str(payment.id),
                               detail={"external_user_id": payment.external_user_id,
                                       "order_id": payment.order_id,
                                       "code": exc.code, "reason": reason})
        await db.commit()
        raise PaymentFailedError(f"취소 실패: {exc.message}", code=exc.code) from exc

    # 취소 확정 — 환불액/수수료/취소시각 기록
    payment.status = PaymentStatus.CANCELED
    payment.canceled_amount = refund
    payment.cancel_fee = fee
    payment.canceled_at = utcnow()
    # actor_user_id가 있으면 Admin(USER) 행위자, 없으면 외부 서비스(SERVICE) 행위자
    if actor_user_id is not None:
        await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                           action="payment.canceled", target_type="payment",
                           target_id=str(payment.id),
                           detail={"external_user_id": payment.external_user_id,
                                   "order_id": payment.order_id,
                                   "amount": payment.amount, "refund": refund,
                                   "fee": fee, "reason": reason})
    else:
        await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                           action="payment.canceled", target_type="payment",
                           target_id=str(payment.id),
                           detail={"external_user_id": payment.external_user_id,
                                   "order_id": payment.order_id,
                                   "amount": payment.amount, "refund": refund,
                                   "fee": fee, "reason": reason})
    await db.commit()
    # 서비스 알림 — 사용자 일반결제 취소. best-effort.
    if notifier is not None:
        await notifier.send(service, event=EVENT_PAYMENT_ONE_OFF_CANCELED,
                            order_id=payment.order_id, status=payment.status,
                            email=payment.external_user_id,
                            desc=f"일반결제 취소 환불 {refund:,}원(수수료 {fee:,}원)")
    return payment


async def admin_cancel_one_off_payment(
    db: AsyncSession, toss: TossClient, *,
    payment: Payment, cancel_amount: int | None, reason: str,
    actor_user_id: uuid.UUID, notifier=None,
) -> Payment:
    """어드민(관리자) 전용 단건결제 취소 — 수수료 없이 전액/부분 취소(누적).

    외부 서비스(사용자) 취소(cancel_one_off_payment)와 달리:
    - **취소 수수료 미적용**: 관리자가 지정한 금액 그대로 환불한다.
    - **취소 허용 게이트(cancellation_enabled) 무시**: 관리자는 항상 취소 가능.
    - **부분취소 누적**: canceled_amount에 환불액을 누적한다. 잔여 환불가능액이
      남으면 status=DONE을 유지하고(추가 취소 가능), 0이 되면 CANCELED로 전환한다.

    Args:
        payment: 취소 대상 Payment(라우트에서 스코프 확인 후 전달).
        cancel_amount: 이번에 환불할 금액. None이면 잔여 전액 환불.
        reason: 취소 사유(토스 cancelReason + 감사로그).
        actor_user_id: 취소를 수행한 관리자 UUID.

    Returns:
        갱신된 Payment.

    Raises:
        ConflictError: ONE_OFF가 아니거나 취소 완료(잔여 0) 상태.
        InputValidationError: cancel_amount가 1~잔여 범위를 벗어남.
        PaymentFailedError: 토스 취소 실패(상태·금액 보존).
    """
    # ONE_OFF만, 그리고 아직 취소 종료되지 않은 결제만 취소 가능.
    # 부분취소 후에도 status는 DONE을 유지하므로 DONE이면 잔여로 판단한다.
    if payment.kind != PaymentKind.ONE_OFF or payment.status != PaymentStatus.DONE:
        raise ConflictError("취소할 수 없는 결제입니다")

    already = payment.canceled_amount or 0          # 기존 누적 환불액
    remaining = payment.amount - already            # 잔여 환불가능액
    if remaining <= 0:
        raise ConflictError("이미 전액 취소된 결제입니다")

    # cancel_amount=None → 잔여 전액. 지정 시 1~잔여 범위 검증.
    refund = remaining if cancel_amount is None else cancel_amount
    if refund <= 0 or refund > remaining:
        raise InputValidationError(
            f"취소 금액은 1원 이상 잔여 환불가능액({remaining:,}원) 이하여야 합니다")

    # 토스 부분취소 — 최초 전액취소(누적 0 + 전액)만 cancelAmount 생략(전액취소),
    # 그 외(부분이거나 이미 일부 취소됨)는 환불액을 명시한다.
    cancel_arg = None if (already == 0 and refund == payment.amount) else refund
    try:
        await toss.cancel_payment(payment.toss_payment_key, reason,
                                  cancel_amount=cancel_arg)
    except TossError as exc:
        # 토스 취소 실패 — 상태·누적액 보존, 감사 기록 후 예외 재발생(재시도 안전)
        await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                           action="payment.cancel_failed", target_type="payment",
                           target_id=str(payment.id),
                           detail={"external_user_id": payment.external_user_id,
                                   "order_id": payment.order_id, "refund": refund,
                                   "code": exc.code, "reason": reason})
        await db.commit()
        raise PaymentFailedError(f"취소 실패: {exc.message}", code=exc.code) from exc

    # 취소 확정 — 누적 환불액 갱신. 잔여 0이면 CANCELED, 아니면 DONE 유지(추가 취소 가능).
    new_total = already + refund
    payment.canceled_amount = new_total
    payment.canceled_at = utcnow()
    if new_total >= payment.amount:
        payment.status = PaymentStatus.CANCELED       # 전액 환불 도달 → 취소 종료
    # cancel_fee는 어드민 무수수료라 건드리지 않는다(0/None 유지).
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="payment.canceled", target_type="payment",
                       target_id=str(payment.id),
                       detail={"external_user_id": payment.external_user_id,
                               "order_id": payment.order_id, "amount": payment.amount,
                               "refund": refund, "canceled_total": new_total,
                               "remaining": payment.amount - new_total,
                               "partial": new_total < payment.amount, "reason": reason})
    await db.commit()
    # 서비스 알림 — 관리자 일반결제 취소(전액/부분). best-effort.
    if notifier is not None:
        service = await db.get(Service, payment.service_id)
        kind_ko = "전액취소" if new_total >= payment.amount else "부분취소"
        await notifier.send(service, event=EVENT_PAYMENT_ONE_OFF_ADMIN_CANCELED,
                            order_id=payment.order_id, status=payment.status,
                            email=payment.external_user_id,
                            desc=f"관리자 {kind_ko} 환불 {refund:,}원(누적 {new_total:,}원)")
    return payment
