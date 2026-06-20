"""admin 카드(결제수단 보관함) 라우트.

카드 상세(등록 카드 정보 + 이 카드로 결제한 내역) 조회와 활성/비활성 토글을 제공한다.
서비스 상세와 동일하게 SYSTEM_ADMIN 전용(require_admin)이다.

Payment에는 card_id가 없지만 (service_id, external_user_id)가 Card의 고유키와 같으므로,
"이 카드로 결제한 내역"은 동일 (service_id, external_user_id)의 Payment로 조회한다
(구독 결제·일반결제 모두 포함).
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render, saved_redirect
from app.admin.deps import AdminContext, require_admin, validate_csrf
from app.admin.pagination import PageParams, paginate
from app.core.deps import get_db, get_notifier
from app.core.errors import NotFoundError
from app.models import Card, Payment, Service
from app.services import cards as card_service

router = APIRouter()

# 카드 상세 결제내역 정렬 가능 컬럼 / 페이지당 건수
CARD_PAY_SORT = {"requested_at": Payment.requested_at, "amount": Payment.amount}


@router.post("/cards/{card_id}/toggle")
async def cards_toggle(card_id: uuid.UUID, request: Request,
                       ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db),
                       notifier=Depends(get_notifier)):
    """카드 활성/비활성 토글.

    현재 상태를 반전시킨다(활성↔비활성). 비활성화하면 이 카드로의 모든 결제가 차단된다.

    응답 분기:
        - htmx 요청(서비스 상세 '등록 카드' 리스트에서 호출)이면 갱신된
          services/_cards_table.html partial(list-svc-cards)을 반환한다.
        - 일반 요청(카드 상세 페이지에서 호출)이면 카드 상세로 리다이렉트한다.
    """
    await validate_csrf(request, ctx)
    card = await db.get(Card, card_id)
    if card is None:
        raise NotFoundError("카드를 찾을 수 없습니다")
    # 현재 상태 반전 — set_card_active가 멱등/감사로그까지 처리
    await card_service.set_card_active(
        db, card_id=card.id, is_active=not card.is_active,
        actor_user_id=ctx.user.id, notifier=notifier)

    # htmx(서비스 상세 리스트)면 해당 서비스의 카드 리스트 partial을 다시 렌더
    if request.headers.get("HX-Request"):
        # 순환 import 방지를 위해 함수 내부에서 services 라우트의 탭 빌더를 가져온다
        from app.admin.routes.services import _cards_tab
        service = await db.get(Service, card.service_id)
        card_page, kpp = await _cards_tab(db, request, card.service_id)
        return render(request, "services/_cards_table.html", ctx=ctx,
                      service=service, card_page=card_page, kpp=kpp)
    # 일반 요청 → 카드 상세로 복귀(토스트 안내)
    return saved_redirect(f"/admin/cards/{card.id}", "변경되었습니다")


@router.get("/cards/{card_id}")
async def cards_detail(card_id: uuid.UUID, request: Request,
                       ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    """카드 상세 — 등록 카드 정보 + 활성/비활성 토글 + 이 카드로 결제한 내역 리스트.

    결제내역은 (service_id, external_user_id)가 일치하는 Payment를 requested_at 기준
    역순으로 페이징한다(구독·일반결제 모두 포함).
    """
    card = await db.get(Card, card_id)
    if card is None:
        raise NotFoundError("카드를 찾을 수 없습니다")
    service = await db.get(Service, card.service_id)
    # 이 카드로 결제한 내역(구독+일반) — (service_id, external_user_id) 매칭
    pp = PageParams.from_request(request, sortable=set(CARD_PAY_SORT),
                                 default_sort="requested_at")
    base = select(Payment).where(
        Payment.service_id == card.service_id,
        Payment.external_user_id == card.external_user_id)
    pay_page = await paginate(db, base.order_by(pp.order_by(CARD_PAY_SORT)), pp,
                              flatten=True)  # Row → Payment
    return render(request, "cards/detail.html", ctx=ctx, card=card,
                  service=service, pay_page=pay_page, pp=pp)
