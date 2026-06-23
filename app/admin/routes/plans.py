"""admin 요금제 관리 라우트.

요금제 목록·엑셀·생성·수정·보관·삭제를 제공한다.
생성/수정/삭제는 SERVICE_MANAGER 이상 권한이 필요하며,
SYSTEM_ADMIN은 모든 서비스 요금제를, SERVICE_MANAGER는 담당 서비스 요금제만 관리한다.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render, render_list, saved_redirect
from app.admin.deps import AdminContext, require_any, require_role, validate_csrf
from app.admin.filters import plan_name_options, service_options as build_service_options
from app.admin.export import xlsx_response
from app.admin.pagination import PageParams, paginate
from app.core.config import Settings
from app.core.deps import get_db, get_notifier, get_settings
from app.core.errors import DomainError, InputValidationError, NotFoundError, PermissionDeniedError
from app.models import Plan, Service, UserRole
from app.services import plans as plan_service
from app.services.billing_math import (first_amount_breakdown, plan_first_amount,
                                       plan_recurring_amount,
                                       recurring_amount_breakdown)

router = APIRouter()
# SERVICE_MANAGER 이상: 기존 /plans 생성 플로우(담당자 본인 주 서비스에 요금제 추가)
require_manager = require_role(UserRole.SERVICE_MANAGER)

# 정렬 가능 컬럼 맵
_PLAN_SORT = {"name": Plan.name, "price": Plan.price, "status": Plan.status,
              "created_at": Plan.created_at}


def _can_manage(ctx: AdminContext, service_id) -> bool:
    """SYSTEM_ADMIN은 전체, 담당자는 담당 서비스만."""
    return ctx.service_ids is None or service_id in ctx.service_ids


def _safe_next(value: str | None, fallback: str) -> str:
    """open redirect 방어: next URL이 /admin/ 로 시작해야만 허용.

    그 외 값(외부 URL, 빈 값)은 모두 fallback으로 대체한다.
    """
    return value if value and value.startswith("/admin/") else fallback


async def _authorize_plan(db, ctx, plan_id) -> Plan:
    """요금제를 조회하고 현재 사용자의 관리 권한을 검사한다.

    요금제가 없거나 담당 서비스가 아닌 경우 모두 404를 발생시킨다.
    403 대신 404를 사용해 요금제 존재 여부를 외부에 노출하지 않는다.
    """
    plan = await db.get(Plan, plan_id)
    if plan is None or not _can_manage(ctx, plan.service_id):
        raise NotFoundError("요금제를 찾을 수 없습니다")
    return plan


def _collect_extra_info(form) -> dict:
    """요금제 폼의 추가정보 입력 행(키/값 한 쌍씩)을 dict로 수집한다 (요청 013).

    폼은 `extra_key`/`extra_value`를 같은 개수의 병렬 목록으로 전송한다(행마다 한 쌍).
    규칙:
    - 키·값이 모두 빈 행은 무시한다(빈 행 추가/삭제 UI 허용).
    - 값은 있는데 키가 비면 InputValidationError로 거부한다.
    - 키가 중복되면 마지막 값이 우선한다(입력 순서 유지).
    구분자 파싱이 없으므로 키·값에 ':'/'=' 등이 포함돼도 안전하다.
    """
    keys = form.getlist("extra_key")
    values = form.getlist("extra_value")
    result: dict[str, str] = {}
    # zip은 길이가 다르면 짧은 쪽에 맞추지만, 폼은 항상 행 단위로 쌍을 보내 길이가 같다.
    for raw_key, raw_value in zip(keys, values):
        key, value = raw_key.strip(), raw_value.strip()
        if not key and not value:
            continue  # 빈 행 무시
        if not key:
            raise InputValidationError(f"추가정보 키를 입력하세요(값: {value})")
        result[key] = value
    return result


def _form_plan_fields(form) -> dict:
    """요금제 폼 데이터를 서비스 레이어 인자 dict로 변환한다.

    체크박스 처리:
        HTML 체크박스는 체크 시 "on", 미체크 시 필드 자체가 전송되지 않는다.
        API·테스트 환경 호환을 위해 "on" / "true" / "1" 세 값을 모두 truthy로 허용한다.

    recurring_discount_value 무효화:
        recurring_discount_type이 "NONE"이면 값 입력 필드가 화면에 숨겨지므로
        서버 측에서도 None으로 강제해 DB 데이터 오염을 방지한다.

    trial_days 무효화:
        trial_enabled가 False이면 trial_days를 None으로 강제한다.

    auto_renew(요청 013):
        체크박스 미체크 시 False. True이면 자동결제 안함(주기 종료 시 만료).

    extra_info(요청 013):
        키/값 입력 행(extra_key/extra_value 병렬 목록)을 수집. 키 없는 값은
        InputValidationError가 호출측의 form_error 흐름에서 처리됨.
    """
    def opt_int(key: str) -> int | None:
        raw = str(form.get(key, "")).strip()
        return int(raw) if raw else None

    trial_enabled = str(form.get("trial_enabled", "")) in ("on", "true", "1")
    rec_type = str(form.get("recurring_discount_type", "NONE"))
    # 자동결제 안함 체크박스: 체크 시 auto_renew=False (요청 013)
    auto_renew = str(form.get("auto_renew_disabled", "")) not in ("on", "true", "1")
    # extra_info 키/값 행 수집 — 키 없는 값은 호출측에서 DomainError로 처리(요청 013)
    extra_info = _collect_extra_info(form)
    return {
        "name": str(form.get("name", "")),
        "price": opt_int("price") or 0,
        "first_payment_type": str(form.get("first_payment_type", "NONE")),
        "first_payment_value": opt_int("first_payment_value"),
        "recurring_discount_type": rec_type,
        "recurring_discount_value": (opt_int("recurring_discount_value")
                                     if rec_type != "NONE" else None),
        "trial_enabled": trial_enabled,
        "trial_days": opt_int("trial_days") if trial_enabled else None,
        "auto_renew": auto_renew,       # 자동결제 여부(요청 013)
        "extra_info": extra_info,       # 추가정보(요청 013)
    }


def _build_plans_query(pp: PageParams, ctx):
    """목록·엑셀이 공유하는 요금제 검색/필터 쿼리.

    ctx.service_ids가 None(SYSTEM_ADMIN)이면 전체 요금제를 조회하고,
    목록이 있으면(SERVICE_MANAGER) 담당 서비스 요금제만 조회한다.
    Service는 INNER JOIN — 모든 요금제는 반드시 서비스에 속한다.

    필터:
        q             — Plan.name 부분 일치
        status        — 정확 일치
        billing_cycle — 정확 일치
        plan_name     — 정확 일치 (distinct 드롭다운 선택)
        service_id    — UUID 파싱 실패 시 pp.filters에서 제거
    """
    base = select(Plan, Service).join(Service, Plan.service_id == Service.id)
    if ctx.service_ids is not None:
        base = base.where(Plan.service_id.in_(ctx.service_ids))
    if pp.q:
        base = base.where(Plan.name.ilike(f"%{pp.q}%"))
    if pp.filters.get("status"):
        base = base.where(Plan.status == pp.filters["status"])
    if pp.filters.get("billing_cycle"):
        base = base.where(Plan.billing_cycle == pp.filters["billing_cycle"])
    if pp.filters.get("plan_name"):
        base = base.where(Plan.name == pp.filters["plan_name"])
    sid = pp.filters.get("service_id", "")
    if sid:
        try:
            base = base.where(Plan.service_id == uuid.UUID(sid))
        except ValueError:
            pp.filters.pop("service_id", None)
    return base


@router.get("/plans/export.xlsx")
async def plans_export(request: Request, ctx: AdminContext = Depends(require_any),
                       db: AsyncSession = Depends(get_db)):
    """현재 필터/검색이 적용된 요금제 전체를 xlsx로 다운로드.

    paginate를 거치지 않고 쿼리를 직접 실행해 페이지네이션을 무시한다.
    """
    pp = PageParams.from_request(request, sortable=set(_PLAN_SORT),
                                 default_sort="created_at",
                                 filter_keys=("status", "service_id", "billing_cycle",
                                              "plan_name"))
    items_q = _build_plans_query(pp, ctx).order_by(pp.order_by(_PLAN_SORT))
    rows = []
    for plan, svc in (await db.execute(items_q)).all():
        # cycle_days 우선, 없으면 cycle_minutes(분) 표시 — MINUTE 주기 비운영 전용(Task 6)
        cycle = plan.billing_cycle
        if plan.cycle_days:
            cycle += f" {plan.cycle_days}일"
        elif plan.cycle_minutes:
            cycle += f" {plan.cycle_minutes}분"
        rows.append([svc.name, plan.name, cycle, plan.price,
                     plan_first_amount(plan), plan_recurring_amount(plan), plan.status])
    return xlsx_response("plans", ["서비스", "요금제", "결제주기", "정가",
                                   "첫 결제", "정기 결제", "상태"], rows, sheet_title="요금제")


@router.get("/plans")
async def plans_list(request: Request, ctx: AdminContext = Depends(require_any),
                     db: AsyncSession = Depends(get_db)):
    """요금제 목록 페이지 / htmx partial 공용 라우트.

    render_list가 HX-Request 헤더를 감지해,
    htmx 요청이면 _table.html partial만, 일반 요청이면 list.html 전체를 렌더한다.

    페이지 렌더 전에 각 Plan 인스턴스에 표시용 금액(first_amount, recurring_amount)과
    툴팁 내역(first_tooltip, recurring_tooltip)을 동적으로 주입한다.
    """
    pp = PageParams.from_request(request, sortable=set(_PLAN_SORT),
                                 default_sort="created_at",
                                 filter_keys=("status", "service_id", "billing_cycle",
                                              "plan_name"))
    base = _build_plans_query(pp, ctx)
    # 서비스 필터 현재 값 재읽기(옵션 표시용)
    service_filter = pp.filters.get("service_id", "")
    items_q = base.order_by(pp.order_by(_PLAN_SORT))
    page = await paginate(db, items_q, pp)
    for plan, _svc in page.items:  # 표시용 금액 + 계산내역 툴팁
        plan.recurring_amount = plan_recurring_amount(plan)
        plan.first_amount = plan_first_amount(plan)
        plan.first_tooltip = first_amount_breakdown(plan)
        plan.recurring_tooltip = recurring_amount_breakdown(plan)
    # 필터 드롭다운용 서비스 옵션(범위 내)
    service_options = await build_service_options(db, ctx.service_ids)
    # 요금제명 드롭다운 옵션(범위 내 distinct) — 서비스 선택 시 그 서비스의 요금제만
    plan_options = await plan_name_options(db, ctx.service_ids, service_filter)
    return render_list(request, "plans/list.html", "plans/_table.html",
                      ctx=ctx, page=page, pp=pp,
                      status_filter=pp.filters.get("status", ""),
                      service_filter=service_filter, service_options=service_options,
                      cycle_filter=pp.filters.get("billing_cycle", ""),
                      plan_filter=pp.filters.get("plan_name", ""), plan_options=plan_options,
                      error=request.query_params.get("error"))


@router.get("/plans/new")
async def plans_new(request: Request, ctx: AdminContext = Depends(require_manager),
                    settings: Settings = Depends(get_settings)):
    """요금제 생성 폼 (SERVICE_MANAGER 전용 진입점).

    SERVICE_MANAGER가 자신의 주 서비스(ctx.user.service_id)에 요금제를 추가할 때 사용한다.
    service는 템플릿에 전달되지 않으므로 form action은 /admin/plans 고정.
    is_prod: 운영(prod) 환경 여부 — 비운영에서만 MINUTE(분) 주기 옵션 노출(Task 6).
    """
    return render(request, "plans/form.html", ctx=ctx, plan=None, error=None,
                  action="/admin/plans", next_url="/admin/plans",
                  is_prod=(settings.environment == "prod"))


@router.post("/plans")
async def plans_create(request: Request, ctx: AdminContext = Depends(require_manager),
                       db: AsyncSession = Depends(get_db),
                       settings: Settings = Depends(get_settings)):
    """담당자가 본인의 주 서비스에 요금제 생성(기존 플로우)."""
    await validate_csrf(request, ctx)
    form = await request.form()
    cycle_days_raw = str(form.get("cycle_days", "")).strip()
    # MINUTE 주기 지원: 폼에서 cycle_minutes 파싱(Task 6)
    cycle_minutes_raw = str(form.get("cycle_minutes", "")).strip()
    try:
        # _form_plan_fields 를 try 안으로 이동: _collect_extra_info가 InputValidationError
        # (DomainError 서브클래스)를 던질 수 있으므로 except DomainError에서 form_error로 처리해야 함
        fields = _form_plan_fields(form)
        await plan_service.create_plan(
            db, service_id=ctx.user.service_id,
            billing_cycle=str(form.get("billing_cycle", "")),
            cycle_days=int(cycle_days_raw) if cycle_days_raw else None,
            cycle_minutes=int(cycle_minutes_raw) if cycle_minutes_raw else None,
            environment=settings.environment,  # 비운영 가드 전달(Task 6)
            actor_user_id=ctx.user.id, **fields)
    except DomainError as exc:
        # 에러 재렌더 시 is_prod 유지 — 분 옵션이 사라지지 않게(Task 6)
        return render(request, "plans/form.html", ctx=ctx, plan=None, error=exc.message,
                      action="/admin/plans", next_url="/admin/plans",
                      is_prod=(settings.environment == "prod"))
    # 요금제 생성 성공 → 완료 모달 트리거
    return saved_redirect("/admin/plans", "저장되었습니다")


@router.get("/services/{service_id}/plans/new")
async def service_plan_new(service_id: uuid.UUID, request: Request,
                           ctx: AdminContext = Depends(require_any),
                           db: AsyncSession = Depends(get_db),
                           settings: Settings = Depends(get_settings)):
    """서비스 상세 화면에서 특정 서비스에 요금제 생성 폼 진입.

    require_any(SYSTEM_ADMIN or SERVICE_MANAGER)로 접근하되,
    _can_manage로 담당 서비스인지 추가 검사한다.
    SYSTEM_ADMIN은 모든 서비스에 진입 가능하다.
    is_prod: 운영(prod) 환경 여부 — 비운영에서만 MINUTE(분) 주기 옵션 노출(Task 6).
    """
    if not _can_manage(ctx, service_id):
        raise PermissionDeniedError("권한이 없습니다")
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    return render(request, "plans/form.html", ctx=ctx, plan=None, error=None,
                  service=service, action=f"/admin/services/{service_id}/plans",
                  next_url=f"/admin/services/{service_id}",
                  is_prod=(settings.environment == "prod"))


@router.post("/services/{service_id}/plans")
async def service_plan_create(service_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_any),
                              db: AsyncSession = Depends(get_db),
                              settings: Settings = Depends(get_settings)):
    """서비스 상세에서 요금제 생성(관리자 또는 해당 서비스 담당자)."""
    if not _can_manage(ctx, service_id):
        raise PermissionDeniedError("권한이 없습니다")
    await validate_csrf(request, ctx)
    form = await request.form()
    cycle_days_raw = str(form.get("cycle_days", "")).strip()
    # MINUTE 주기 지원: 폼에서 cycle_minutes 파싱(Task 6)
    cycle_minutes_raw = str(form.get("cycle_minutes", "")).strip()
    try:
        # _form_plan_fields 를 try 안으로 이동: _collect_extra_info가 InputValidationError
        # (DomainError 서브클래스)를 던질 수 있으므로 except DomainError에서 form_error로 처리해야 함
        fields = _form_plan_fields(form)
        await plan_service.create_plan(
            db, service_id=service_id,
            billing_cycle=str(form.get("billing_cycle", "")),
            cycle_days=int(cycle_days_raw) if cycle_days_raw else None,
            cycle_minutes=int(cycle_minutes_raw) if cycle_minutes_raw else None,
            environment=settings.environment,  # 비운영 가드 전달(Task 6)
            actor_user_id=ctx.user.id, **fields)
    except DomainError as exc:
        service = await db.get(Service, service_id)
        # 에러 재렌더 시 is_prod 유지 — 분 옵션이 사라지지 않게(Task 6)
        return render(request, "plans/form.html", ctx=ctx, plan=None, error=exc.message,
                      service=service, action=f"/admin/services/{service_id}/plans",
                      next_url=f"/admin/services/{service_id}",
                      is_prod=(settings.environment == "prod"))
    # 서비스 상세에서 요금제 생성 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/services/{service_id}", "저장되었습니다")


@router.get("/plans/{plan_id}/edit")
async def plans_edit(plan_id: uuid.UUID, request: Request,
                     ctx: AdminContext = Depends(require_any),
                     db: AsyncSession = Depends(get_db)):
    """요금제 수정 폼. next 파라미터로 저장 후 이동 URL을 전달받는다.

    _safe_next로 open redirect를 방어한다.
    """
    plan = await _authorize_plan(db, ctx, plan_id)
    next_url = _safe_next(request.query_params.get("next"), "/admin/plans")
    return render(request, "plans/form.html", ctx=ctx, plan=plan, error=None,
                  action=f"/admin/plans/{plan_id}", next_url=next_url)


@router.post("/plans/{plan_id}")
async def plans_update(plan_id: uuid.UUID, request: Request,
                       ctx: AdminContext = Depends(require_any),
                       db: AsyncSession = Depends(get_db)):
    """요금제 수정 처리.

    DomainError.http_status == 404이면 서비스 레이어에서 요금제 삭제/미존재 판정이므로
    예외를 그대로 상위로 전파해 404 핸들러가 처리하게 한다.
    그 외 DomainError는 폼으로 오류 메시지를 렌더한다.
    """
    plan = await _authorize_plan(db, ctx, plan_id)
    await validate_csrf(request, ctx)
    form = await request.form()
    next_url = _safe_next(str(form.get("next", "")), "/admin/plans")
    # 결제 주기(billing_cycle/cycle_days)는 수정 불가(요청) — 폼에서도 보내지 않으며,
    # 서버 update_plan도 더 이상 인자를 받지 않아 기존 주기가 그대로 유지된다.
    try:
        # _form_plan_fields 를 try 안으로 이동: _collect_extra_info가 InputValidationError
        # (DomainError 서브클래스)를 던질 수 있으므로 except DomainError에서 form_error로 처리해야 함
        fields = _form_plan_fields(form)
        await plan_service.update_plan(db, plan_id=plan_id, service_id=plan.service_id,
                                       actor_user_id=ctx.user.id, **fields)
    except DomainError as exc:
        if exc.http_status == 404:
            raise
        return render(request, "plans/form.html", ctx=ctx, plan=plan, error=exc.message,
                      action=f"/admin/plans/{plan_id}", next_url=next_url)
    # 요금제 수정 성공 → 완료 모달 트리거
    return saved_redirect(next_url, "저장되었습니다")


@router.post("/plans/{plan_id}/archive")
async def plans_archive(plan_id: uuid.UUID, request: Request,
                        ctx: AdminContext = Depends(require_any),
                        db: AsyncSession = Depends(get_db),
                        notifier=Depends(get_notifier)):
    """요금제 보관(비활성화) 처리. 새 구독 불가 상태로 전환하며 기존 구독은 유지된다."""
    plan = await _authorize_plan(db, ctx, plan_id)
    await validate_csrf(request, ctx)
    form = await request.form()
    next_url = _safe_next(str(form.get("next", "")), "/admin/plans")
    await plan_service.archive_plan(db, plan_id=plan_id, service_id=plan.service_id,
                                    actor_user_id=ctx.user.id, notifier=notifier)
    # 요금제 보관 성공 → 완료 모달 트리거
    return saved_redirect(next_url, "보관되었습니다")


@router.post("/plans/{plan_id}/activate")
async def plans_activate(plan_id: uuid.UUID, request: Request,
                         ctx: AdminContext = Depends(require_any),
                         db: AsyncSession = Depends(get_db),
                         notifier=Depends(get_notifier)):
    """보관된 요금제를 다시 활성화(ARCHIVED → ACTIVE). 신규 구독을 다시 받는다."""
    plan = await _authorize_plan(db, ctx, plan_id)
    await validate_csrf(request, ctx)
    form = await request.form()
    next_url = _safe_next(str(form.get("next", "")), "/admin/plans")
    await plan_service.activate_plan(db, plan_id=plan_id, service_id=plan.service_id,
                                     actor_user_id=ctx.user.id, notifier=notifier)
    # 요금제 활성화 성공 → 완료 모달 트리거
    return saved_redirect(next_url, "활성화되었습니다")


@router.post("/plans/{plan_id}/delete")
async def plans_delete(plan_id: uuid.UUID, request: Request,
                       ctx: AdminContext = Depends(require_any),
                       db: AsyncSession = Depends(get_db),
                       notifier=Depends(get_notifier)):
    """요금제 삭제 처리.

    구독이 있는 요금제는 서비스 레이어에서 DomainError를 발생시킨다.
    이 경우 next_url에 ?error=메시지를 붙여 리다이렉트한다.
    (이미 next_url에 ?가 포함된 경우를 위해 sep를 동적으로 결정한다.)
    DomainError.http_status == 404이면 재조회 중 미존재이므로 예외 전파.
    """
    plan = await _authorize_plan(db, ctx, plan_id)
    await validate_csrf(request, ctx)
    form = await request.form()
    next_url = _safe_next(str(form.get("next", "")), "/admin/plans")
    sep = "&" if "?" in next_url else "?"
    try:
        await plan_service.delete_plan(db, plan_id=plan_id, service_id=plan.service_id,
                                       actor_user_id=ctx.user.id, notifier=notifier)
    except DomainError as exc:
        if exc.http_status == 404:
            raise
        # 구독 있는 요금제 삭제 거부 — 에러 경로는 saved 없이 리다이렉트
        return RedirectResponse(f"{next_url}{sep}error={exc.message}", status_code=303)
    # 요금제 삭제 성공 → 완료 모달 트리거
    return saved_redirect(next_url, "삭제되었습니다")


@router.post("/plans/{plan_id}/bonus-days")
async def plans_bonus_days(plan_id: uuid.UUID, request: Request,
                           ctx: AdminContext = Depends(require_any),
                           db: AsyncSession = Depends(get_db),
                           notifier=Depends(get_notifier)):
    """요금제 보너스 사용일 추가(요청) — 이 요금제의 모든 열린 구독 만료일·다음결제를 +N일.

    폼 days(정수) 파싱 → add_bonus_days. 성공 시 적용 구독 수를 완료 메시지에 포함.
    숫자 오류·범위 오류는 ?error= 로 목록에 표시(plans_delete와 동일 패턴).
    """
    plan = await _authorize_plan(db, ctx, plan_id)
    await validate_csrf(request, ctx)
    form = await request.form()
    next_url = _safe_next(str(form.get("next", "")), "/admin/plans")
    sep = "&" if "?" in next_url else "?"
    try:
        days = int(str(form.get("days", "")).strip())
    except ValueError:
        return RedirectResponse(f"{next_url}{sep}error=추가 일수를 숫자로 입력하세요",
                                status_code=303)
    try:
        affected = await plan_service.add_bonus_days(
            db, plan_id=plan_id, service_id=plan.service_id, days=days,
            actor_user_id=ctx.user.id, notifier=notifier)
    except DomainError as exc:
        if exc.http_status == 404:
            raise
        return RedirectResponse(f"{next_url}{sep}error={exc.message}", status_code=303)
    # 사용일 추가 성공 → 완료 모달(적용 구독 수 안내)
    return saved_redirect(next_url, f"{affected}개 구독에 {days}일이 추가되었습니다")
