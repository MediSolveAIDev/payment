"""admin 정산 라우트.

승인 완료(DONE) 결제 기준으로 서비스별·기간별 정산 요약 및 건별 내역을 제공한다.

두 가지 모드:
  - 전체 모드: service_id 미지정 → 스코프 내 모든 서비스의 요약 테이블
  - 서비스별 모드: service_id 지정 → 해당 서비스의 결제 건별 페이지네이션 목록

SYSTEM_ADMIN은 전체 서비스, SERVICE_MANAGER는 담당 서비스(ctx.service_ids)만 접근한다.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_any
from app.admin.export import xlsx_response
from app.admin.filters import plan_name_options, service_options as build_service_options
from app.admin.pagination import PageParams, date_range, paginate
from app.core.deps import get_db
from app.core.clock import kst_format, utcnow
from app.core.errors import NotFoundError
from app.models import Payment, PaymentKind, PaymentStatus, Plan, Service, Subscription
from app.services.settlement import settlement_summary

router = APIRouter()

# 정렬 가능 컬럼 맵 (서비스별 모드의 건별 테이블에 적용)
_SETTLE_SORT = {"approved_at": Payment.approved_at, "amount": Payment.amount}


def _settlement_payment_query(selected: Service, plan_name: str | None, start, end):
    """서비스별 모드 결제 건별 base 쿼리(정렬 미적용). view/export 공용.

    조건:
        - status IN (DONE, CANCELED) — 승인 완료 + 취소(환불) 결제 포함, 실패·대기는 제외
          (취소 건은 환불액/순매출을 함께 보여주기 위해 정산 목록에 포함)
        - service_id == selected.id 고정
        - plan_name 지정 시: Subscription → Plan INNER JOIN 후 이름 필터
          (단건 결제는 Plan이 없으므로 plan_name 선택 시 구독 결제만 포함됨)
        - approved_at 범위 필터 (반개구간: start <= approved_at < end)
    """
    base = (select(Payment, Subscription)
            .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
            .where(Payment.status.in_((PaymentStatus.DONE, PaymentStatus.CANCELED)),
                   Payment.service_id == selected.id))
    if plan_name:
        base = (base.join(Plan, Subscription.plan_id == Plan.id)
                .where(Plan.name == plan_name))
    if start:
        base = base.where(Payment.approved_at >= start)
    if end:
        base = base.where(Payment.approved_at < end)
    return base


async def _settlement_context(request: Request, pp: "PageParams",
                               ctx: "AdminContext",
                               db: AsyncSession):
    """정산 목록/엑셀 공통: 기간·스코프·선택 서비스 판정. (start, end, scope, selected) 반환.

    기간 기본값:
        from/to 파라미터가 모두 없으면 당월 1일~오늘을 자동으로 설정한다.
        (정산 화면은 기본적으로 당월 현황을 보여주는 것이 UX 상 자연스럽기 때문.)

    스코프:
        ctx.service_ids — SYSTEM_ADMIN이면 None(전체), SERVICE_MANAGER이면 담당 목록.

    선택 서비스 판정:
        service_id 파라미터가 있으면 UUID 파싱 후 스코프 포함 여부를 검사한다.
        SERVICE_MANAGER가 담당하지 않는 서비스 ID를 지정하면 404를 발생시킨다.
        (403 대신 404 — 서비스 존재 여부 미노출.)
    """
    if "from" not in request.query_params and "to" not in request.query_params:
        now = utcnow()
        pp.filters["from"] = now.strftime("%Y-%m-01")
        pp.filters["to"] = now.strftime("%Y-%m-%d")
    start, end = date_range(pp)
    scope = ctx.service_ids
    selected: Service | None = None
    raw_sid = pp.filters.get("service_id", "")
    if raw_sid:
        try:
            sid = uuid.UUID(raw_sid)
        except ValueError:
            pp.filters.pop("service_id", None)
        else:
            if scope is not None and sid not in scope:
                raise NotFoundError("서비스를 찾을 수 없습니다")
            selected = await db.get(Service, sid)
            if selected is None:
                raise NotFoundError("서비스를 찾을 수 없습니다")
    return start, end, scope, selected


@router.get("/settlement")
async def settlement_view(request: Request,
                          ctx: AdminContext = Depends(require_any),
                          db: AsyncSession = Depends(get_db)):
    """정산 요약/건별 페이지.

    전체 모드 (selected is None):
        settlement_summary로 스코프 내 서비스별 요약 rows를 가져오고
        구독 매출(sub_total) / 일반 매출(one_off_total)을 분리 합산한다.
        분리 합계는 요약 테이블 하단 소계 표시에 사용된다.

    서비스별 모드 (selected 있음):
        _settlement_payment_query로 해당 서비스 결제 건별 페이지를 추가로 조회한다.
        pay_page가 None이 아닌 경우 템플릿이 건별 테이블을 렌더한다.

    합계 스코프:
        selected가 있으면 [selected.id]로 단일 서비스 합계만 집계하고,
        없으면 전체 스코프(scope)로 모든 담당 서비스 합계를 집계한다.
    """
    pp = PageParams.from_request(request, sortable=set(_SETTLE_SORT),
                                 default_sort="approved_at",
                                 filter_keys=("from", "to", "service_id", "plan_name"))
    start, end, scope, selected = await _settlement_context(request, pp, ctx, db)
    raw_sid = pp.filters.get("service_id", "")
    plan_name = pp.filters.get("plan_name") or None

    # 합계: 전체 모드=스코프 전체, 서비스별 모드=해당 서비스만
    sum_scope = [selected.id] if selected else scope
    total_count, total_amount, rows = await settlement_summary(
        db, sum_scope, start, end, plan_name=plan_name)

    # 분리 합계 — 구독 매출 / 일반 매출을 각각 집계해 화면 소계 표시에 사용
    sub_total = sum(r.sub_amount for r in rows)
    one_off_total = sum(r.one_off_amount for r in rows)
    sub_count = sum(r.sub_count for r in rows)
    one_off_count = sum(r.one_off_count for r in rows)
    # 환불/순매출 합계 — total_amount(총매출)에서 환불을 빼 순매출 산출(취소내역 반영)
    refund_total = sum(r.refund_amount for r in rows)
    net_total = total_amount - refund_total

    # 서비스별 모드: 결제 건별 페이지
    pay_page = None
    if selected:
        base = _settlement_payment_query(selected, plan_name, start, end)
        items_q = base.order_by(pp.order_by(_SETTLE_SORT))
        pay_page = await paginate(db, items_q, pp)

    # 서비스 select 옵션(스코프 내, '전체 서비스' 항목 없음 — 정산 화면은 서비스 단위 선택)
    service_options = await build_service_options(db, scope, include_all=False)

    # 요금제 select 옵션
    plan_options = await plan_name_options(db, scope, raw_sid)

    return render(request, "settlement/index.html", ctx=ctx,
                  total_count=total_count, total_amount=total_amount, rows=rows,
                  refund_total=refund_total, net_total=net_total,
                  selected=selected, pay_page=pay_page, pp=pp,
                  service_options=service_options,
                  plan_options=plan_options, plan_filter=plan_name or "",
                  sub_total=sub_total, one_off_total=one_off_total,
                  sub_count=sub_count, one_off_count=one_off_count,
                  from_filter=pp.filters.get("from", ""),
                  to_filter=pp.filters.get("to", ""))


@router.get("/settlement/export.xlsx")
async def settlement_export(request: Request, ctx: AdminContext = Depends(require_any),
                            db: AsyncSession = Depends(get_db)):
    """정산 엑셀 다운로드.

    모드에 따라 두 가지 형식으로 출력한다.

    서비스별 모드 (selected 있음):
        결제 건별 상세 — 승인시각·사용자·주문번호·유형·종류·금액 컬럼.
        파일명에 서비스명을 포함한다.

    전체 모드 (selected 없음):
        서비스별 합계 — 서비스·건수·구독매출·일반매출·합계 컬럼.
        settlement_summary를 재호출해 최신 집계를 사용한다.
        페이지네이션을 적용하지 않는다.
    """
    pp = PageParams.from_request(request, sortable=set(_SETTLE_SORT),
                                 default_sort="approved_at",
                                 filter_keys=("from", "to", "service_id", "plan_name"))
    start, end, scope, selected = await _settlement_context(request, pp, ctx, db)
    plan_name = pp.filters.get("plan_name") or None

    if selected:   # 서비스별 모드 — 결제 건별
        base = _settlement_payment_query(selected, plan_name, start, end)
        rows = []
        for p, _sub in (await db.execute(base.order_by(pp.order_by(_SETTLE_SORT)))).all():
            kind_ko = "구독" if p.kind == PaymentKind.SUBSCRIPTION else "일반"
            # 환불액·순매출(원금−환불) 표기 — 전액취소(CANCELED)·부분취소(DONE) 모두 canceled_amount로 판정
            refund = p.canceled_amount or 0
            status_ko = ("취소" if p.status == PaymentStatus.CANCELED
                         else ("부분취소" if refund else "승인"))
            rows.append([kst_format(p.approved_at, "%Y-%m-%d %H:%M"),
                         p.external_user_id or "-", p.order_id, p.payment_type,
                         kind_ko, status_ko, p.amount, refund, p.amount - refund])
        return xlsx_response(f"settlement-{selected.name}",
                             ["승인시각", "사용자", "주문번호", "유형", "종류", "상태",
                              "총매출", "환불", "순매출"],
                             rows, sheet_title="정산")

    # 전체 모드 — 서비스별 합계
    _c, _a, summary = await settlement_summary(db, scope, start, end, plan_name=plan_name)
    rows = [[r.service_name, r.count, r.sub_amount, r.one_off_amount,
             r.amount, r.refund_amount, r.net_amount]
            for r in summary]
    return xlsx_response("settlement",
                         ["서비스", "건수", "구독매출", "일반매출", "총매출", "환불", "순매출"],
                         rows, sheet_title="정산")
