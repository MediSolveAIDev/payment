"""웹훅 라우터 — 토스페이먼츠가 서버로 푸시하는 결제 이벤트 수신 엔드포인트.

토스는 이벤트 발생 시 이 엔드포인트로 POST 요청을 보낸다.
중복 방지: transmission_id(헤더)를 handle_webhook이 Redis로 1회용 처리.
IP 검증: settings.webhook_ip_check_enabled=True인 환경에서만 활성화
(로컬 개발·테스트에서는 False로 두어 화이트리스트 없이 동작 가능).
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_client_ip,
    get_db,
    get_email_sender,
    get_settings,
)
from app.core.config import Settings
from app.core.deps import get_toss_provider  # T7: 전역 toss 제거, 서비스별 해석기 사용
from app.core.errors import PermissionDeniedError
from app.notifications.email import EmailSender
from app.schemas.api import WebhookAck
from app.services.webhooks import handle_webhook
from app.toss.provider import TossClientProvider  # 서비스별 토스 클라이언트 해석기 타입(T7)

router = APIRouter()


@router.post(
    "/webhooks/toss",
    response_model=WebhookAck,
    summary="토스 웹훅 수신 (토스 → 서버)",
    responses={200: {"description": "처리 결과 상태"},
               403: {"description": "FORBIDDEN — 허용되지 않은 요청 IP"}},
)
async def toss_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    toss_provider: TossClientProvider = Depends(get_toss_provider),  # T7: 서비스별 해석기
    settings: Settings = Depends(get_settings),
    email_sender: EmailSender = Depends(get_email_sender),
):
    """토스페이먼츠 웹훅 이벤트를 수신해 처리한다.

    IP 검증(조건부): webhook_ip_check_enabled=True일 때만 toss_webhook_allowed_ips
    화이트리스트를 확인한다. 프로덕션에서는 반드시 True로 설정해야 한다.

    중복 방지: tosspayments-webhook-transmission-id 헤더 값을 handle_webhook에
    전달한다. handle_webhook 내부에서 Redis를 이용해 동일 transmission_id의
    재처리를 차단한다(at-least-once 전달 정책으로 동일 이벤트가 재전송될 수 있음).
    T7 컷오버: 전역 toss 제거 — toss_provider를 전달해 웹훅 내부에서 서비스별 해석.
    """
    if settings.webhook_ip_check_enabled:
        ip = get_client_ip(request, settings)
        if ip not in settings.toss_webhook_allowed_ips:
            raise PermissionDeniedError("허용되지 않은 요청입니다")
    payload = await request.json()
    tid = request.headers.get("tosspayments-webhook-transmission-id")
    # T7: toss → toss_provider 전달; handle_webhook 내부에서 payment.service_id 기반 해석
    event = await handle_webhook(db, toss_provider, email_sender,
                                 transmission_id=tid, payload=payload)
    return {"status": event.status}
