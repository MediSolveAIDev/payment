"""카드(결제수단) 외부 API 라우터 — POST/GET/DELETE /api/v1/cards.

외부 서비스가 API키+HMAC(authenticate_service)로 인증 후 카드를 등록·조회·삭제한다.
빌링키 발급이 수반되는 등록 엔드포인트(POST /cards)는 payment_rate_limit을,
조회·삭제는 일반 authenticate_service를 사용한다.

보안 원칙:
  - billingKey 등 민감 정보는 응답에 절대 포함하지 않는다.
  - 카드 마스킹 정보(card_info)만 CardResponse에 노출한다.
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
from app.models import Service
from app.schemas.api import CardRegisterRequest, CardResponse
from app.services import cards as card_service
from app.toss.client import TossClient

router = APIRouter()


@router.post(
    "/cards",
    status_code=201,
    response_model=CardResponse,
    summary="카드 등록 또는 교체",
    responses={
        201: {"description": "등록 또는 교체된 카드(마스킹 정보만 반환)"},
        **PAYMENT_RESPONSES,
        **CONFLICT_RESPONSE,
        **VALIDATION_RESPONSE,
    },
)
async def register_card(
    payload: CardRegisterRequest,
    # 빌링키 발급(토스 API 호출) 수반 → 결제 전용 처리율 제한 적용
    service: Service = Depends(payment_rate_limit),
    db: AsyncSession = Depends(get_db),
    toss: TossClient = Depends(get_toss),
    cipher: AesGcmCipher = Depends(get_cipher),
    notifier=Depends(get_notifier),
):
    """카드(빌링키)를 등록하거나 기존 카드를 교체한다.

    (service, external_user_id)당 1건을 유지한다.
    이미 카드가 있으면 기존 행을 교체하고 이전 빌링키를 best-effort 삭제한다.
    응답에는 마스킹된 카드 정보만 포함되며 billingKey는 절대 반환하지 않는다.
    """
    # 카드 등록 또는 교체 서비스 호출 — 등록/교체 시 서비스 알림 발송(notifier)
    card = await card_service.register_or_replace_card(
        db, toss, cipher,
        service=service,
        external_user_id=payload.external_user_id,
        customer_key=payload.customer_key,
        auth_key=payload.auth_key,
        notifier=notifier,
    )
    # 마스킹 정보만 포함한 응답 반환 — billingKey 비포함
    return CardResponse.from_model(card)


@router.get(
    "/cards/{external_user_id}",
    response_model=CardResponse,
    summary="등록 카드 조회",
    responses={
        200: {"description": "등록된 카드 마스킹 정보"},
        **AUTH_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_card(
    external_user_id: str,
    # 읽기 전용 조회 → 일반 HMAC 인증으로 충분
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
):
    """외부 사용자 ID로 등록된 카드 마스킹 정보를 조회한다.

    authenticate_service: 결제 API 호출 없이 조회만 하므로 일반 인증 적용.
    카드가 없으면 404를 반환한다.
    """
    # 서비스+사용자 기준으로 카드 조회
    card = await card_service.get_card(
        db, service_id=service.id, external_user_id=external_user_id
    )
    if card is None:
        # 등록된 카드가 없으면 NotFoundError(404) 반환
        raise NotFoundError("등록된 카드가 없습니다")
    return CardResponse.from_model(card)


@router.delete(
    "/cards/{external_user_id}",
    status_code=204,
    summary="등록 카드 삭제",
    responses={
        204: {"description": "카드 삭제 완료"},
        **AUTH_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **CONFLICT_RESPONSE,
    },
)
async def delete_card(
    external_user_id: str,
    # 읽기+삭제 → 일반 HMAC 인증으로 충분(결제 API 호출 없음)
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
    toss: TossClient = Depends(get_toss),
    cipher: AesGcmCipher = Depends(get_cipher),
    notifier=Depends(get_notifier),
):
    """등록된 카드(빌링키)를 삭제한다.

    authenticate_service: 실제 과금은 없으므로 일반 인증 적용.
    billing-active 상태(TRIAL/ACTIVE/PAST_DUE/SUSPENDED/EXTENDED)의 구독이
    이 카드를 사용 중이면 409(CONFLICT)를 반환한다.
    카드가 없으면 404를 반환한다.
    """
    # 카드 삭제 — cipher 필요(빌링키 복호화 후 토스 best-effort 삭제). 삭제 시 서비스 알림.
    await card_service.delete_card(
        db, toss, cipher,
        service_id=service.id,
        external_user_id=external_user_id,
        notifier=notifier,
    )
    # 204 No Content — 응답 본문 없음
