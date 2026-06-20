from fastapi import APIRouter

from app.api.v1 import cards, payments, plans, services, subscriptions, webhooks

router = APIRouter()
router.include_router(services.router, tags=["services"])  # 무인증 서비스 목록(id·이름·상태만)
router.include_router(plans.router, tags=["plans"])
router.include_router(subscriptions.router, tags=["subscriptions"])
router.include_router(payments.router, tags=["payments"])
router.include_router(webhooks.router, tags=["webhooks"])
router.include_router(cards.router, tags=["cards"])  # 카드(결제수단) 등록·조회·삭제
