"""admin 서비스 화면의 엑셀(.xlsx) 다운로드 라우트 모음.

services.py(목록/상세/등록/키 관리)에서 분리(감사 Phase 4 — S6: 580줄 파일에
책임 5개가 동거). URL prefix·템플릿은 그대로 — 라우터 등록만 추가됐다.

GET /services/export.xlsx               — 서비스 목록 (현재 검색/필터 적용)
GET /services/{id}/subs.xlsx            — 서비스 상세 구독 탭
GET /services/{id}/oneoff.xlsx          — 서비스 상세 단건(일반) 결제 탭
GET /services/{id}/plans.xlsx           — 서비스 상세 요금제 탭
"""
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.deps import AdminContext, require_admin
from app.admin.export import EXPORT_MAX_ROWS, xlsx_response
from app.admin.filters import SUB_SORT, SVC_SORT, services_query, subscription_query
from app.admin.pagination import PageParams
from app.core.deps import get_db
from app.core.clock import kst_format
from app.core.errors import NotFoundError
from app.models import Payment, PaymentKind, PaymentStatus, Plan, Service
from app.services.billing_math import plan_first_amount, plan_recurring_amount

router = APIRouter()


@router.get("/services/export.xlsx")
async def services_export(request: Request, ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    """현재 필터/검색이 적용된 서비스 전체를 xlsx로 다운로드."""
    pp = PageParams.from_request(request, sortable=set(SVC_SORT),
                                 default_sort="created_at", filter_keys=("status",))
    items_q = services_query(pp).order_by(pp.order_by(SVC_SORT))
    services = list((await db.scalars(items_q)).all())
    rows = [[s.name, s.manager_email or "-",
             ", ".join(s.allowed_ips or []) or "-", s.status] for s in services]
    return xlsx_response("services", ["서비스명", "담당자 이메일", "허용 IP", "상태"],
                         rows, sheet_title="서비스")


@router.get("/services/{service_id}/subs.xlsx")
async def service_subs_export(service_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_admin),
                              db: AsyncSession = Depends(get_db)):
    """서비스 상세 — 구독 탭 엑셀 다운로드. 현재 탭의 검색/필터가 동일하게 적용된다."""
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    spp = PageParams.from_request(request, sortable=set(SUB_SORT),
                                  default_sort="created_at", filter_keys=("status",))
    # 공유 빌더(filters.py) — 구독 목록/탭과 동일 필터 보장(감사 Phase 4 — S2)
    base = subscription_query(spp, service_id=service_id)
    rows = [[sub.external_user_id, plan.name, sub.status,
             kst_format(sub.current_period_end, "%Y-%m-%d"),
             kst_format(sub.next_billing_at, "%Y-%m-%d %H:%M")]
            for sub, plan, _svc in (await db.execute(  # 행 상한(성능 M2)
                base.order_by(spp.order_by(SUB_SORT)).limit(EXPORT_MAX_ROWS))).all()]
    return xlsx_response(f"{service.name}-subs",
                         ["사용자", "요금제", "상태", "만료일", "다음 결제"],
                         rows, sheet_title="구독")


@router.get("/services/{service_id}/oneoff.xlsx")
async def service_oneoff_export(service_id: uuid.UUID, request: Request,
                                ctx: AdminContext = Depends(require_admin),
                                db: AsyncSession = Depends(get_db)):
    """서비스 상세 — 단건(일반) 결제 탭 엑셀 다운로드."""
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    base = (select(Payment).where(Payment.service_id == service_id,
                                  Payment.kind == PaymentKind.ONE_OFF)
            .order_by(Payment.requested_at.desc())
            .limit(EXPORT_MAX_ROWS))  # 행 상한(성능 M2)
    # 환불액(canceled_amount)·취소 수수료(cancel_fee)를 함께 출력 —
    # 전액취소(CANCELED)·부분취소(DONE이지만 canceled_amount>0) 모두 반영
    rows = [[kst_format(p.approved_at, "%Y-%m-%d %H:%M") if p.approved_at else "-",
             p.external_user_id or "-", p.order_id, p.amount,
             p.canceled_amount or 0, p.cancel_fee or 0,
             ("부분취소" if p.status == PaymentStatus.DONE and p.canceled_amount else p.status)]
            for p in (await db.scalars(base)).all()]
    return xlsx_response(f"{service.name}-oneoff",
                         ["승인시각", "사용자", "주문번호", "금액", "환불", "수수료", "상태"],
                         rows, sheet_title="일반결제")


@router.get("/services/{service_id}/plans.xlsx")
async def service_plans_export(service_id: uuid.UUID, request: Request,
                               ctx: AdminContext = Depends(require_admin),
                               db: AsyncSession = Depends(get_db)):
    """서비스 상세 — 요금제 탭 엑셀 다운로드."""
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    plans = (await db.scalars(select(Plan).where(Plan.service_id == service_id)
                              .order_by(Plan.created_at))).all()
    rows = []
    for plan in plans:
        cycle = plan.billing_cycle + (f" {plan.cycle_days}일" if plan.cycle_days else "")
        rows.append([plan.name, cycle, plan.price, plan_first_amount(plan),
                     plan_recurring_amount(plan), plan.status])
    return xlsx_response(f"{service.name}-plans",
                         ["요금제", "결제주기", "정가", "첫 결제", "정기 결제", "상태"],
                         rows, sheet_title="요금제")
