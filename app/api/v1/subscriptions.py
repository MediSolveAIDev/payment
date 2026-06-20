"""구독 라우터 — 구독 생성·조회·취소·재개·수동결제 엔드포인트.

외부 서비스가 API키+HMAC(authenticate_service)로 인증 후 호출한다.
빌링키 발급·갱신이 발생하는 엔드포인트(create/manual_pay)는
payment_rate_limit을 추가로 통과해야 한다.

Task 10: change-card 엔드포인트 제거 — 카드 교체는 POST /api/v1/cards(재등록)로 통합됨.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticate_service,
    get_cipher,
    get_db,
    get_notifier,
    get_toss,
    payment_rate_limit,
)
from app.api.openapi import (
    AUTH_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    PAYMENT_RESPONSES,
    VALIDATION_RESPONSE,
)
from app.core.crypto import AesGcmCipher
from app.core.errors import NotFoundError
from app.models import Plan, Service, Subscription
from app.schemas.api import (
    SubscriptionCreateRequest,
    SubscriptionResponse,
    UsageDaysRequest,
)
# Task 10: CardChangeRequest 제거 — 카드 교체는 POST /api/v1/cards(재등록)로 통합됨
from app.services import cards as card_service  # 카드 보관함 조회 — 응답에 card_info 포함용
from app.services import subscriptions as subscription_service
from app.toss.client import TossClient

router = APIRouter()


async def _to_response(db: AsyncSession, sub: Subscription) -> SubscriptionResponse:
    """Subscription 모델 + 연결 Plan + 등록 카드를 조회해 SubscriptionResponse로 변환한다.

    Task 7 변경: sub.card_info(제거된 컬럼) 대신 cards 테이블에서 card_info를 조회해
    from_model에 전달한다. 카드가 없으면(미등록·삭제 등) card=None으로 반환된다.
    """
    plan = await db.get(Plan, sub.plan_id)
    if plan is None:
        # FK가 RESTRICT라 정상 경로에선 발생 불가 — 데이터 정합성 깨짐 방어
        raise NotFoundError("구독에 연결된 요금제를 찾을 수 없습니다")
    # cards 테이블에서 마스킹 카드 정보 조회(없으면 None — 응답 card 필드가 null)
    card = await card_service.get_card(
        db, service_id=sub.service_id, external_user_id=sub.external_user_id)
    card_info = card.card_info if card is not None else None
    return SubscriptionResponse.from_model(sub, plan, card_info=card_info)


@router.post(
    "/subscriptions",
    status_code=201,
    response_model=SubscriptionResponse,
    summary="구독 생성",
    responses={201: {"description": "생성된 구독"},
               **PAYMENT_RESPONSES, **NOT_FOUND_RESPONSE,
               **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
)
async def create_subscription(
    payload: SubscriptionCreateRequest,
    service: Service = Depends(payment_rate_limit),
    db: AsyncSession = Depends(get_db),
    toss: TossClient = Depends(get_toss),
    cipher: AesGcmCipher = Depends(get_cipher),
    notifier=Depends(get_notifier),
):
    """신규 구독을 생성한다.

    Task 7 변경: auth_key·customer_key 파라미터 제거. 구독 전에 POST /cards 로
    카드를 먼저 등록해야 한다. 빌링키는 등록된 카드(cards 테이블)에서 서버가 조회한다.
    payment_rate_limit: 첫 결제(토스 API 호출)가 수반되므로 결제 전용 처리율 제한 유지.
    trial=True이면 체험 기간 동안 결제 없이 TRIAL 상태로 시작한다.
    """
    sub = await subscription_service.create_subscription(
        db, toss, cipher, service=service, plan_id=payload.plan_id,
        external_user_id=payload.external_user_id,
        trial=payload.trial, notifier=notifier)
    return await _to_response(db, sub)


@router.post(
    "/subscriptions/{external_user_id}/pay",
    response_model=SubscriptionResponse,
    summary="수동 결제(정지 구독 복구)",
    responses={200: {"description": "결제 후 갱신된 구독"},
               **PAYMENT_RESPONSES, **NOT_FOUND_RESPONSE, **VALIDATION_RESPONSE},
)
async def manual_pay(
    external_user_id: str,
    service: Service = Depends(payment_rate_limit),
    db: AsyncSession = Depends(get_db),
    toss: TossClient = Depends(get_toss),
    cipher: AesGcmCipher = Depends(get_cipher),
    notifier=Depends(get_notifier),
):
    """정지(SUSPENDED) 구독의 수동 결제 — 성공 시 ACTIVE 복귀 + 기준일 리셋.

    payment_rate_limit: 실제 빌링키 청구(토스 API 호출)가 발생하므로
    결제 전용 처리율 제한을 적용한다.
    """
    sub = await subscription_service.manual_charge_subscription(
        db, toss, cipher, service=service, external_user_id=external_user_id,
        notifier=notifier)
    return await _to_response(db, sub)


@router.post(
    "/subscriptions/{external_user_id}/add-days",
    response_model=SubscriptionResponse,
    summary="구독 사용일 추가",
    responses={200: {"description": "사용일 추가 후 구독"},
               **AUTH_RESPONSES, **NOT_FOUND_RESPONSE,
               **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
)
async def add_usage_days(
    external_user_id: str,
    payload: UsageDaysRequest,
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
):
    """외부 서비스가 자기 사용자 구독에 사용일(days)을 추가한다.

    이용 중(ACTIVE/EXTENDED/PAST_DUE) 구독의 만료일·다음 결제일을 days만큼 미루며
    상태는 변경하지 않는다. 토스 결제 호출이 없으므로 일반 HMAC 인증으로 충분.
    대상 상태가 아니면 409(CONFLICT), 구독이 없으면 404를 반환한다.
    """
    sub = await subscription_service.add_usage_days(
        db, service=service, external_user_id=external_user_id, days=payload.days)
    return await _to_response(db, sub)


@router.get(
    "/subscriptions/{external_user_id}",
    response_model=SubscriptionResponse,
    summary="구독 조회",
    responses={200: {"description": "구독 정보"},
               **AUTH_RESPONSES, **NOT_FOUND_RESPONSE},
)
async def get_subscription(
    external_user_id: str,
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
):
    """외부 사용자 ID로 가장 최근 구독을 조회한다.

    authenticate_service: 읽기 전용이므로 일반 HMAC 인증으로 충분.
    구독이 없으면 404를 반환한다.
    """
    sub = await subscription_service.get_latest_subscription(
        db, service_id=service.id, external_user_id=external_user_id)
    if sub is None:
        raise NotFoundError("구독을 찾을 수 없습니다")
    return await _to_response(db, sub)


@router.post(
    "/subscriptions/{external_user_id}/cancel",
    response_model=SubscriptionResponse,
    summary="구독 취소 예약",
    responses={200: {"description": "취소 예약된 구독"},
               **AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **VALIDATION_RESPONSE},
)
async def cancel_subscription(
    external_user_id: str,
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
    notifier=Depends(get_notifier),
):
    """구독을 취소 예약한다 — 만료일이 되면 자동 종료(즉시 삭제 아님).

    authenticate_service: 결제 API 호출 없이 상태만 변경하므로 일반 인증 적용.
    """
    sub = await subscription_service.cancel_subscription(
        db, service=service, external_user_id=external_user_id, notifier=notifier)
    return await _to_response(db, sub)


@router.post(
    "/subscriptions/{external_user_id}/resume",
    response_model=SubscriptionResponse,
    summary="구독 재개(취소 예약 철회)",
    responses={200: {"description": "재개된 구독"},
               **AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **VALIDATION_RESPONSE},
)
async def resume_subscription(
    external_user_id: str,
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
    notifier=Depends(get_notifier),
):
    """취소 예약된 구독을 재개한다 — CANCELED → ACTIVE(또는 원래 상태) 복귀.

    authenticate_service: 결제 API 호출 없이 상태만 변경하므로 일반 인증 적용.
    """
    sub = await subscription_service.resume_subscription(
        db, service=service, external_user_id=external_user_id, notifier=notifier)
    return await _to_response(db, sub)


# Task 10: POST /subscriptions/{external_user_id}/change-card 라우트 제거됨.
# 카드 교체는 POST /api/v1/cards 재등록으로 처리하며, 구독은 card_id FK로 자동 참조.
