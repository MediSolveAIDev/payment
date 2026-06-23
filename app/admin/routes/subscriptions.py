"""admin 구독 관리 라우트.

목록 조회·엑셀 내보내기·상세 조회·강제 해지 기능을 제공한다.
SYSTEM_ADMIN은 전체 구독에 접근하고, SERVICE_MANAGER는
service_scope(ctx)가 반환하는 담당 서비스의 구독에만 접근한다.
"""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render, render_list, saved_redirect
from app.admin.deps import AdminContext, require_any, service_scope, validate_csrf
from app.admin.export import EXPORT_MAX_ROWS, xlsx_response
from app.admin.filters import (
    SUB_SORT,
    plan_name_options,
    service_options as build_service_options,
    subscription_query,
)
from app.admin.pagination import PageParams, paginate
from app.core.deps import get_cipher, get_db, get_notifier, get_toss_provider  # Task 5: 전역 get_toss → 서비스별 해석기
from app.core.clock import kst_format
from app.core.crypto import AesGcmCipher
from app.toss.provider import TossClientProvider  # Task 5: 서비스별 해석기 타입
from app.core.errors import (
    ConflictError,
    InputValidationError,
    NotFoundError,
    PaymentFailedError,
)
from app.models import AuditLog, Payment, PaymentStatus, Plan, Service, Subscription, User
from app.services.billing_math import plan_recurring_amount
from app.services import cards as card_service  # cards 테이블에서 등록 카드 조회(card-vault 리팩터)
from app.services.subscriptions import (
    admin_retry_payment,
    extend_subscription,
    force_cancel_subscription,
)
# TossClient import 제거 — Task 5에서 TossClientProvider로 교체됨

router = APIRouter()

@router.get("/subscriptions/export.xlsx")
async def subscriptions_export(request: Request, ctx: AdminContext = Depends(require_any),
                               db: AsyncSession = Depends(get_db)):
    """현재 필터/검색이 적용된 구독 전체를 xlsx로 다운로드.

    paginate를 거치지 않고 items_q를 직접 실행해 페이지네이션을 무시한다.
    담당 스코프 및 모든 필터는 공유 빌더 subscription_query(filters.py)가 동일하게 적용한다.
    """
    pp = PageParams.from_request(request, sortable=set(SUB_SORT),
                                 default_sort="created_at",
                                 filter_keys=("status", "service_id", "plan_name", "from", "to"))
    # 행 상한(감사 Phase 3 — 성능 M2): 무제한 적재로 인한 워커 OOM 방지
    # 공유 빌더(app/admin/filters.py) — 목록·서비스 상세 탭과 동일 필터 보장(S2)
    items_q = (subscription_query(pp, scope=service_scope(ctx))
               .order_by(pp.order_by(SUB_SORT)).limit(EXPORT_MAX_ROWS))
    rows = [[svc.name, sub.external_user_id, plan.name, sub.status,
             kst_format(sub.current_period_end, "%Y-%m-%d"),
             kst_format(sub.next_billing_at, "%Y-%m-%d %H:%M")]
            for sub, plan, svc in (await db.execute(items_q)).all()]
    return xlsx_response("subscriptions",
                         ["서비스", "사용자", "요금제", "상태", "만료일", "다음 결제"],
                         rows, sheet_title="구독")


@router.get("/subscriptions")
async def subscriptions_list(request: Request,
                             ctx: AdminContext = Depends(require_any),
                             db: AsyncSession = Depends(get_db)):
    """구독 목록 페이지 / htmx partial 공용 라우트.

    render_list가 HX-Request 헤더를 감지해,
    htmx 요청이면 _table.html partial만, 일반 요청이면 list.html 전체를 렌더한다.

    서비스·요금제 드롭다운 옵션은 스코프 내에서만 생성한다.
    (plan_name_options는 service_filter를 추가 인자로 받아 서비스 선택 시 해당 서비스의
    요금제명만 표시한다.)
    """
    pp = PageParams.from_request(request, sortable=set(SUB_SORT),
                                 default_sort="created_at",
                                 filter_keys=("status", "service_id", "plan_name", "from", "to"))
    # 공유 빌더(app/admin/filters.py) — 엑셀·서비스 상세 탭과 동일 필터 보장(S2)
    base = subscription_query(pp, scope=service_scope(ctx))
    # 서비스 필터(전체/서비스명) — 담당 범위 안에서만 적용
    service_filter = pp.filters.get("service_id", "")
    scope = service_scope(ctx)
    items_q = base.order_by(pp.order_by(SUB_SORT))
    page = await paginate(db, items_q, pp)
    # 필터 드롭다운용 서비스 옵션(범위 내) — (value, label) 튜플
    service_options = await build_service_options(db, scope)
    plan_options = await plan_name_options(db, scope, service_filter)
    return render_list(request, "subscriptions/list.html", "subscriptions/_table.html",
                      ctx=ctx, page=page, pp=pp,
                      status_filter=pp.filters.get("status", ""),
                      service_filter=service_filter, service_options=service_options,
                      plan_filter=pp.filters.get("plan_name", ""), plan_options=plan_options,
                      from_filter=pp.filters.get("from", ""), to_filter=pp.filters.get("to", ""))


@router.get("/subscriptions/{sub_id}")
async def subscription_detail(sub_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_any),
                              db: AsyncSession = Depends(get_db)):
    """구독 상세 페이지.

    스코프 검사: scope가 None이면 전체 접근(SYSTEM_ADMIN),
    목록이면 담당 서비스 여부를 확인해 비담당 구독을 404로 처리한다.
    (403 대신 404를 사용해 구독 존재 여부를 노출하지 않는다.)

    최근 결제 내역은 최대 200건만 가져온다.
    paid_count는 DONE 상태 결제 수를 별도 집계해 부분 조회 가능하게 한다.
    """
    sub = await db.get(Subscription, sub_id)
    scope = service_scope(ctx)
    if sub is None or (scope is not None and sub.service_id not in scope):
        raise NotFoundError("구독을 찾을 수 없습니다")
    plan = await db.get(Plan, sub.plan_id)
    service = await db.get(Service, sub.service_id)
    payments = (await db.scalars(
        select(Payment).where(Payment.subscription_id == sub.id)
        .order_by(Payment.requested_at.desc()).limit(200))).all()
    paid_count = await db.scalar(
        select(func.count()).select_from(Payment).where(
            Payment.subscription_id == sub.id, Payment.status == PaymentStatus.DONE)) or 0
    # 재결제 버튼 안내에 표시할 실제 청구 예정액(상시 할인 적용 후) — 결제 코어와 동일 계산식
    charge_amount = plan_recurring_amount(plan) if plan else None
    # 만료일 연장 이력(요청) — 이 구독의 subscription.extended 감사 이벤트를 최신순으로 표시
    ext_logs = list((await db.scalars(
        select(AuditLog).where(AuditLog.action == "subscription.extended",
                               AuditLog.target_id == str(sub.id))
        .order_by(AuditLog.created_at.desc()).limit(20))).all())
    ext_actor_ids = {l.actor_user_id for l in ext_logs if l.actor_user_id}
    ext_emails: dict = {}
    if ext_actor_ids:
        for u in (await db.scalars(select(User).where(User.id.in_(ext_actor_ids)))).all():
            ext_emails[u.id] = u.email
    extensions = [{"time": l.created_at,
                   "old_end": (l.detail or {}).get("old_period_end"),
                   "new_end": (l.detail or {}).get("new_period_end"),
                   "actor": ext_emails.get(l.actor_user_id, "시스템")}
                  for l in ext_logs]
    # 체험 사용 여부 — 가입 시점 subscription.create 감사로그의 detail.trial로 판정한다
    # (체험 후 ACTIVE로 전환돼도 이력이 유지된다). 감사로그가 없으면 현재 TRIAL 상태로 추정.
    create_log = await db.scalar(
        select(AuditLog).where(AuditLog.action == "subscription.create",
                               AuditLog.target_id == str(sub.id))
        .order_by(AuditLog.created_at.desc()).limit(1))
    trial_used = (bool((create_log.detail or {}).get("trial")) if create_log
                  else sub.status == "TRIAL")
    # card-vault 리팩터: Subscription에는 카드 정보가 없으므로 cards 테이블에서 조회한다.
    # (service_id, external_user_id) 쌍으로 등록된 카드를 가져오며, 미등록이면 None.
    card = await card_service.get_card(
        db, service_id=sub.service_id, external_user_id=sub.external_user_id)
    return render(request, "subscriptions/detail.html", ctx=ctx, sub=sub,
                  plan=plan, service=service, payments=payments, paid_count=paid_count,
                  trial_used=trial_used, card=card,
                  charge_amount=charge_amount, extensions=extensions,
                  error=request.query_params.get("error"))


@router.post("/subscriptions/{sub_id}/force-cancel")
async def subscription_force_cancel(sub_id: uuid.UUID, request: Request,
                                    ctx: AdminContext = Depends(require_any),
                                    db: AsyncSession = Depends(get_db),
                                    notifier=Depends(get_notifier)):
    """구독 강제 해지 처리.

    validate_csrf로 CSRF 토큰을 검증한 뒤 서비스 레이어의
    force_cancel_subscription에 스코프를 그대로 전달한다.
    (스코프 검사·감사 로그 기록은 서비스 레이어가 담당.)
    성공 시 상세 페이지로 303 리다이렉트.
    """
    await validate_csrf(request, ctx)
    await force_cancel_subscription(db, subscription_id=sub_id,
                                    service_scope=service_scope(ctx),
                                    actor_user_id=ctx.user.id, notifier=notifier)
    # 구독 강제 해지 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/subscriptions/{sub_id}", "구독이 해지되었습니다")


@router.post("/subscriptions/{sub_id}/extend")
async def subscription_extend(sub_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_any),
                              db: AsyncSession = Depends(get_db),
                              notifier=Depends(get_notifier)):
    """구독 만료일 연장(요청) — 입력한 날짜로 만료일·다음결제일을 변경하고 상태를 연장처리(EXTENDED)로.

    폼 필드 new_end(YYYY-MM-DD)를 KST 자정이 아닌 UTC 자정 datetime으로 변환해 서비스에 전달한다.
    (감사로그 purge 라우트와 동일한 날짜 파싱 정책.)
    날짜 형식 오류·과거 날짜·상태 불가는 ?error= 로 상세 페이지에 표시하고, 스코프 밖은 404로 전파.
    """
    from datetime import date, datetime, timezone

    await validate_csrf(request, ctx)
    form = await request.form()
    raw = str(form.get("new_end", "")).strip()
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        msg = quote("연장 만료일(날짜)을 올바르게 입력하세요")
        return RedirectResponse(f"/admin/subscriptions/{sub_id}?error={msg}", status_code=303)
    new_end = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    try:
        await extend_subscription(db, subscription_id=sub_id,
                                  service_scope=service_scope(ctx),
                                  new_end=new_end, actor_user_id=ctx.user.id,
                                  notifier=notifier)
    except (InputValidationError, ConflictError) as exc:
        # 과거 날짜·연장 불가 상태는 상세 페이지에 오류로 표시(스코프 밖 NotFoundError는 전파 → 404)
        return RedirectResponse(
            f"/admin/subscriptions/{sub_id}?error={quote(exc.message)}", status_code=303)
    # 연장 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/subscriptions/{sub_id}", "만료일이 연장되었습니다")


@router.post("/subscriptions/{sub_id}/retry-payment")
async def subscription_retry_payment(sub_id: uuid.UUID, request: Request,
                                     ctx: AdminContext = Depends(require_any),
                                     db: AsyncSession = Depends(get_db),
                                     toss_provider: TossClientProvider = Depends(get_toss_provider),  # Task 5: 서비스별 해석기
                                     cipher: AesGcmCipher = Depends(get_cipher),
                                     notifier=Depends(get_notifier)):
    """결제 실패(PAST_DUE)·정지(SUSPENDED) 구독을 담당자가 즉시 재결제.

    CSRF 검증 후 admin_retry_payment에 스코프를 전달한다(스코프 검사·감사 기록은 서비스 레이어).
    성공 → 완료 모달, 실패(결제 거절·상태 불가·결제수단 없음) → ?error= 로 상세 페이지에 메시지 표시.
    Task 5: get_toss(전역) → get_toss_provider + for_service(sub.service)로 서비스별 클라이언트 사용.
    """
    await validate_csrf(request, ctx)
    # Task 5: 구독을 먼저 로드해 service_id로 서비스 조회 후 서비스별 토스 클라이언트 해석.
    # sub이 없으면 admin_retry_payment가 NotFoundError를 발생시키므로 toss 해석은 건너뛴다.
    # admin_retry_payment가 내부에서 sub을 다시 로드하므로 여기서는 service 확보 목적으로만 조회.
    sub = await db.get(Subscription, sub_id)
    if sub is None:
        # 구독이 없으면 admin_retry_payment에서 NotFoundError가 발생한다 — 아무 클라이언트나 전달
        # (도달하기 전에 서비스 레이어가 NotFoundError를 발생시킨다).
        from app.core.errors import NotFoundError as _NFE
        raise _NFE("구독을 찾을 수 없습니다")
    service = await db.get(Service, sub.service_id)
    # 서비스별 토스 클라이언트 해석 — 키 미설정 시 TossKeyNotConfiguredError 발생
    toss = toss_provider.for_service(service)
    try:
        await admin_retry_payment(db, toss, cipher, subscription_id=sub_id,
                                  service_scope=service_scope(ctx),
                                  actor_user_id=ctx.user.id, notifier=notifier)
    except (PaymentFailedError, ConflictError) as exc:
        # 결제 거절·결제수단 없음·상태 불가는 상세 페이지에 오류 메시지로 표시
        # (스코프 밖 NotFoundError는 잡지 않고 전파 → 404 처리)
        return RedirectResponse(
            f"/admin/subscriptions/{sub_id}?error={quote(exc.message)}", status_code=303)
    # 재결제 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/subscriptions/{sub_id}", "결제가 완료되었습니다")
