"""admin 결제 관리 라우트.

결제 목록 조회·엑셀 내보내기·상세 조회·단건결제 취소를 제공한다.
구독 결제(SUBSCRIPTION)와 단건(일반) 결제(ONE_OFF) 모두 이 라우트에서 관리한다.
SYSTEM_ADMIN은 전체, SERVICE_MANAGER는 service_scope(ctx)의 담당 서비스 결제만 접근한다.
"""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render, saved_redirect
from app.admin.deps import AdminContext, require_any, service_scope, validate_csrf
from app.admin.export import EXPORT_MAX_ROWS, xlsx_response
from app.admin.filters import plan_name_options, service_options as build_service_options
from app.admin.pagination import PageParams, date_range, paginate
from app.core.deps import get_db, get_notifier, get_toss_provider  # Task 5: 전역 get_toss → 서비스별 해석기
from app.core.clock import kst_format
from app.core.errors import InputValidationError, NotFoundError, TossKeyNotConfiguredError  # 키 미설정 오류
from app.models import Payment, PaymentKind, PaymentStatus, Plan, Service, Subscription
from app.services import cards as card_service  # 결제 카드 표시용 cards 테이블 조회
from app.services import payments as payment_service
from app.toss.provider import TossClientProvider  # Task 5: 서비스별 해석기 타입

router = APIRouter()

# 정렬 가능 컬럼 맵
_PAY_SORT = {
    "order_id": Payment.order_id, "amount": Payment.amount,
    "status": Payment.status, "requested_at": Payment.requested_at,
}


def _build_payments_query(pp: PageParams, ctx):
    """목록·엑셀이 공유하는 결제 검색/필터 쿼리.

    JOIN 전략:
        Subscription을 OUTER JOIN하는 이유는 단건(ONE_OFF) 결제에는
        subscription_id가 없기 때문이다. INNER JOIN이면 단건 결제가 누락된다.
        Plan도 Subscription을 통해 OUTER JOIN하므로 단건 결제는 Plan이 없다.
        Service는 INNER JOIN — 모든 결제는 반드시 서비스에 속한다.

    plan_name 필터:
        Plan을 기준으로 필터하므로, plan_name 선택 시 구독 결제만 결과에 포함된다.
        (단건 결제는 Plan이 없으므로 자동 제외.)

    스코프 적용:
        service_scope(ctx)가 None(SYSTEM_ADMIN)이면 전체 결제 조회.
        UUID 목록이면 Payment.service_id 기준 담당 서비스로 제한.

    기타 필터:
        q         — order_id 또는 external_user_id 부분 일치
        status    — 정확 일치
        kind      — SUBSCRIPTION / ONE_OFF
        service_id — UUID 파싱 실패 시 pp.filters에서 제거
        from/to   — requested_at 범위
    """
    base = (select(Payment, Subscription, Service)
            .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
            .outerjoin(Plan, Subscription.plan_id == Plan.id)
            .join(Service, Payment.service_id == Service.id))
    scope = service_scope(ctx)
    if scope is not None:
        base = base.where(Payment.service_id.in_(scope))
    if pp.q:
        base = base.where(Payment.order_id.ilike(f"%{pp.q}%")
                          | Payment.external_user_id.ilike(f"%{pp.q}%"))
    if pp.filters.get("status"):
        base = base.where(Payment.status == pp.filters["status"])
    if pp.filters.get("kind"):
        base = base.where(Payment.kind == pp.filters["kind"])
    if pp.filters.get("plan_name"):
        base = base.where(Plan.name == pp.filters["plan_name"])
    sid = pp.filters.get("service_id", "")
    if sid:
        try:
            base = base.where(Payment.service_id == uuid.UUID(sid))
        except ValueError:
            pp.filters.pop("service_id", None)
    start, end = date_range(pp)
    if start:
        base = base.where(Payment.requested_at >= start)
    if end:
        base = base.where(Payment.requested_at < end)
    return base


@router.get("/payments/export.xlsx")
async def payments_export(request: Request, ctx: AdminContext = Depends(require_any),
                          db: AsyncSession = Depends(get_db)):
    """현재 필터/검색이 적용된 결제 전체를 xlsx로 다운로드.

    paginate를 거치지 않고 쿼리를 직접 실행해 페이지네이션을 무시한다.
    담당 스코프 및 모든 필터는 _build_payments_query에서 동일하게 적용된다.
    """
    pp = PageParams.from_request(request, sortable=set(_PAY_SORT),
                                 default_sort="requested_at",
                                 filter_keys=("status", "from", "to", "kind", "service_id", "plan_name"))
    # 행 상한(감사 Phase 3 — 성능 M2): 무제한 적재로 인한 워커 OOM 방지
    items_q = (_build_payments_query(pp, ctx)
               .order_by(pp.order_by(_PAY_SORT)).limit(EXPORT_MAX_ROWS))
    rows = []
    for p, _sub, svc in (await db.execute(items_q)).all():
        kind_ko = "구독" if p.kind == PaymentKind.SUBSCRIPTION else "일반"
        rows.append([p.order_id, svc.name, kind_ko, p.external_user_id or "-",
                     p.payment_type, p.amount, p.status, p.failure_code or "-",
                     kst_format(p.requested_at, "%Y-%m-%d %H:%M")])
    return xlsx_response("payments",
                         ["주문번호", "서비스", "종류", "사용자", "유형", "금액",
                          "상태", "실패코드", "요청시각"], rows, sheet_title="결제")


@router.post("/payments/{payment_id}/cancel")
async def payment_cancel(payment_id: uuid.UUID, request: Request,
                         ctx: AdminContext = Depends(require_any),
                         db: AsyncSession = Depends(get_db),
                         toss_provider: TossClientProvider = Depends(get_toss_provider),  # Task 5: 서비스별 해석기
                         notifier=Depends(get_notifier)):
    """Admin에서 단건 결제 취소(운영자) — 수수료 없이 전액/부분(누적) 취소.

    CSRF·스코프 검증 후 admin_cancel_one_off_payment 호출.
    폼 필드 cancel_amount: 비어 있으면 전액 취소(None), 숫자면 부분 취소 금액(원).
    취소 허용 게이트(cancellation_enabled)는 어드민 취소에서 무시된다(항상 허용).
    상태(DONE)·잔여 한도 검증은 도메인 레이어에서 수행한다.
    취소 성공 시 결제 상세로 303 리다이렉트.
    Task 5: get_toss(전역) → get_toss_provider + for_service(service)로 서비스별 클라이언트 사용.
    """
    await validate_csrf(request, ctx)
    payment = await db.get(Payment, payment_id)
    scope = service_scope(ctx)
    if payment is None or (scope is not None and payment.service_id not in scope):
        raise NotFoundError("결제를 찾을 수 없습니다")
    # Task 5: payment.service_id로 서비스를 로드해 서비스별 토스 클라이언트 해석
    service = await db.get(Service, payment.service_id)
    # 키 미설정 시 raw JSON 422가 아닌 결제 상세 ?error= 리다이렉트로 처리(final-review F1)
    try:
        toss = toss_provider.for_service(service)
    except TossKeyNotConfiguredError as exc:
        return RedirectResponse(
            f"/admin/payments/{payment_id}?error={quote(exc.message)}", status_code=303)
    form = await request.form()
    # 부분취소 금액 — 빈값/미입력이면 전액 취소(None). 숫자가 아니면 입력 오류.
    raw = (form.get("cancel_amount") or "").strip()
    cancel_amount: int | None = None
    if raw:
        try:
            cancel_amount = int(raw)
        except ValueError:
            raise InputValidationError("취소 금액이 올바르지 않습니다")
    # Admin 취소는 실제 행위자(관리자 USER)를 감사 로그에 기록하기 위해 actor_user_id 전달
    await payment_service.admin_cancel_one_off_payment(
        db, toss, payment=payment, cancel_amount=cancel_amount,
        reason="관리자 취소", actor_user_id=ctx.user.id, notifier=notifier)
    # 결제 취소 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/payments/{payment_id}", "결제가 취소되었습니다")


@router.get("/payments/{payment_id}")
async def payment_detail(payment_id: uuid.UUID, request: Request,
                         ctx: AdminContext = Depends(require_any),
                         db: AsyncSession = Depends(get_db)):
    """결제 상세 페이지.

    스코프 검사: scope가 None이면 전체 접근(SYSTEM_ADMIN),
    목록이면 담당 서비스 여부 확인 후 비담당 결제를 404로 처리한다.
    (403 대신 404 — 결제 존재 여부 미노출.)

    구독 결제(subscription_id 존재)인 경우에만 Subscription을 추가 조회한다.
    단건 결제는 sub가 None으로 전달된다.
    """
    payment = await db.get(Payment, payment_id)
    scope = service_scope(ctx)
    if payment is None or (scope is not None and payment.service_id not in scope):
        raise NotFoundError("결제를 찾을 수 없습니다")
    service = await db.get(Service, payment.service_id)
    sub = (await db.get(Subscription, payment.subscription_id)
           if payment.subscription_id else None)
    # 결제에 사용된 카드 — Payment에는 card_id가 없으므로 (service_id, external_user_id)로
    # 현재 보관함 카드를 조회한다. 정확한 충전 카드는 템플릿에서 raw_response.card를 우선 사용한다.
    card = (await card_service.get_card(db, service_id=payment.service_id,
                                        external_user_id=payment.external_user_id)
            if payment.external_user_id else None)
    # 어드민 취소(수수료 없음, 전액/부분 누적)용 — 누적 환불액·잔여 환불가능액.
    # 어드민 취소는 항상 허용이므로 cancellation_enabled와 무관하게 잔여가 있으면 취소 가능.
    refunded = (payment.canceled_amount or 0) if payment.kind == PaymentKind.ONE_OFF else 0
    cancel_remaining = (payment.amount - refunded
                        if payment.kind == PaymentKind.ONE_OFF else 0)
    return render(request, "payments/detail.html", ctx=ctx,
                  payment=payment, service=service, sub=sub, card=card,
                  refunded=refunded, cancel_remaining=cancel_remaining)


@router.get("/payments")
async def payments_list(request: Request,
                        ctx: AdminContext = Depends(require_any),
                        db: AsyncSession = Depends(get_db)):
    """결제 목록 페이지.

    render_list 대신 render를 사용한다.
    결제 목록은 htmx 부분 갱신 대상이 아니므로 전체 페이지만 렌더한다.
    (subscriptions_list 등과 달리 partial 템플릿이 별도로 없다.)

    서비스·요금제 드롭다운 옵션은 스코프 내에서만 생성한다.
    """
    pp = PageParams.from_request(request, sortable=set(_PAY_SORT),
                                 default_sort="requested_at",
                                 filter_keys=("status", "from", "to", "kind", "service_id", "plan_name"))
    base = _build_payments_query(pp, ctx)
    # 서비스 필터 — 유효 UUID면 적용, 아니면 무시
    service_filter = pp.filters.get("service_id", "")
    scope = service_scope(ctx)
    items_q = base.order_by(pp.order_by(_PAY_SORT))
    page = await paginate(db, items_q, pp)
    # 서비스 옵션 (스코프 내)
    service_options = await build_service_options(db, scope)
    plan_options = await plan_name_options(db, scope, service_filter)
    return render(request, "payments/list.html", ctx=ctx, page=page, pp=pp,
                  status_filter=pp.filters.get("status", ""),
                  kind_filter=pp.filters.get("kind", ""),
                  service_filter=service_filter,
                  service_options=service_options,
                  plan_filter=pp.filters.get("plan_name", ""), plan_options=plan_options,
                  from_filter=pp.filters.get("from", ""), to_filter=pp.filters.get("to", ""))
