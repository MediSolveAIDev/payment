"""결제 라우터 — 단건(일반) 결제 생성 및 결제 내역 조회 엔드포인트.

Task 9 변경: 단건결제가 카드 보관함 기반으로 전환됨.
- auth_key/customer_key를 요청 본문에서 제거. 사전 등록된 카드(POST /cards)를 사용한다.
- 빌링키는 cards 테이블에서 서버가 자동 조회·복호화하여 결제한다.
금액 보호: HMAC 본문 서명(authenticate_service 내)이 payload 전체를 서명 대상에
포함하므로, 중간자가 amount를 변조하면 서명 검증에서 즉시 거부된다(문서 11 흐름).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticate_service,
    get_cipher,
    get_db,
    get_notifier,
    payment_rate_limit,
)
from app.core.deps import get_toss_provider  # 서비스별 토스 클라이언트 해석기(Task 5)
from app.api.openapi import AUTH_RESPONSES, NOT_FOUND_RESPONSE, PAYMENT_RESPONSES, VALIDATION_RESPONSE
from app.core.crypto import AesGcmCipher
from app.models import Payment, Service
from app.schemas.api import (
    OneOffCancelRequest,
    OneOffPaymentRequest,
    PaymentListResponse,
    PaymentResponse,
)
from app.services import payments as payment_service
from app.toss.provider import TossClientProvider  # 서비스별 토스 클라이언트 해석기 타입(Task 5)

router = APIRouter()


@router.get(
    "/payments/{external_user_id}",
    response_model=PaymentListResponse,
    summary="결제 내역 조회",
    responses={200: {"description": "결제 내역(최신순, 최대 50건)"}, **AUTH_RESPONSES},
)
async def list_payments(
    external_user_id: str,
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
):
    """외부 사용자 ID의 결제 내역을 최신순으로 최대 50건 반환한다.

    Payment.service_id로 서비스 범위를 격리한다(다른 서비스 결제 비노출).
    구독 정기결제와 **단건(ONE_OFF) 결제를 모두 포함**한다 — 취소 가능한 단건 결제도
    조회되어, 응답의 취소 수수료 필드로 '취소 시 수수료/환불액'을 화면에 안내할 수 있다.
    authenticate_service: 읽기 전용이므로 일반 HMAC 인증으로 충분.
    """
    rows = await db.scalars(
        select(Payment)
        .where(Payment.service_id == service.id,
               Payment.external_user_id == external_user_id)
        .order_by(Payment.requested_at.desc())
        .limit(50))
    # from_model: 결제마다 서비스 취소 정책 기준 '취소 시 수수료/환불액'을 함께 반환
    return {"payments": [PaymentResponse.from_model(p, service) for p in rows.all()]}


@router.post(
    "/payments",
    status_code=201,
    response_model=PaymentResponse,
    summary="단건(1회성) 결제 생성",
    responses={201: {"description": "결제 결과"},
               **PAYMENT_RESPONSES, **NOT_FOUND_RESPONSE, **VALIDATION_RESPONSE},
)
async def create_payment(
    payload: OneOffPaymentRequest,
    service: Service = Depends(payment_rate_limit),
    db: AsyncSession = Depends(get_db),
    toss_provider: TossClientProvider = Depends(get_toss_provider),  # Task 5: 서비스별 해석기
    cipher: AesGcmCipher = Depends(get_cipher),
    notifier=Depends(get_notifier),
):
    """단건(일반) 결제를 생성한다 — 구독 없이 1회성으로 등록된 카드(카드 보관함)로 즉시 청구.

    Task 9: auth_key/customer_key 불필요 — 사전 등록된 카드(POST /cards)의 빌링키를
    카드 보관함(cards 테이블)에서 자동 조회해 결제한다. 카드 미등록 시 404 반환.
    payment_rate_limit: 결제 승인(토스 API 호출)이 수반되므로 결제 전용 처리율 제한 적용.
    HMAC 본문 서명이 amount를 포함하므로 중간자 금액 변조는 서명 오류로 차단된다.
    타임아웃(결과 불명) 시 PENDING 유지 — 이중 결제 방지.
    Task 5: get_toss(전역) → get_toss_provider + for_service(service)로 서비스별 클라이언트 사용.
    """
    # Task 5: 서비스에 등록된 toss_secret_key로 클라이언트 해석(키 미설정 시 TossKeyNotConfiguredError)
    toss = toss_provider.for_service(service)
    # Task 9: auth_key/customer_key 제거 — 카드 보관함에서 서버가 자동 조회
    payment = await payment_service.create_one_off_payment(
        db, toss, cipher,
        service=service,
        external_user_id=payload.external_user_id,
        order_id=payload.order_id,
        order_name=payload.order_name,
        amount=payload.amount,
        notifier=notifier,
    )
    return PaymentResponse.from_model(payment, service)


@router.post(
    "/payments/{order_id}/cancel",
    response_model=PaymentResponse,
    summary="단건 결제 취소(환불)",
    responses={200: {"description": "취소 결과"},
               **PAYMENT_RESPONSES, **NOT_FOUND_RESPONSE, **VALIDATION_RESPONSE},
)
async def cancel_payment(
    order_id: str,
    payload: OneOffCancelRequest,
    service: Service = Depends(payment_rate_limit),
    db: AsyncSession = Depends(get_db),
    toss_provider: TossClientProvider = Depends(get_toss_provider),  # Task 5: 서비스별 해석기
    notifier=Depends(get_notifier),
):
    """단건 결제 취소 — 서비스 정책에 따라 환불(수수료 공제). 문서 11 참조.

    payment_rate_limit: 토스 취소(환불) API 호출이 수반된다.
    취소 성공 시 status=CANCELED와 canceled_amount/cancel_fee를 포함한 결과 반환.
    서비스 정책(cancellation_enabled=False) 또는 DONE 아닌 결제는 오류 반환.
    Task 5: get_toss(전역) → get_toss_provider + for_service(service)로 서비스별 클라이언트 사용.
    """
    # Task 5: 서비스에 등록된 toss_secret_key로 클라이언트 해석(키 미설정 시 TossKeyNotConfiguredError)
    toss = toss_provider.for_service(service)
    payment = await payment_service.cancel_one_off_payment(
        db, toss, service=service, order_id=order_id, reason=payload.reason,
        notifier=notifier)
    return PaymentResponse.from_model(payment, service)
