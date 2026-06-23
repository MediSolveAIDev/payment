"""admin 서비스 관리 라우트.

서비스 목록·엑셀·생성·상세·담당자 관리·API 키 관리·IP 제한·상태 변경·삭제를 제공한다.
모든 엔드포인트는 SYSTEM_ADMIN 전용(require_admin)이다.
"""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render, render_list, saved_redirect
from app.admin.audit_labels import action_label, actor_label, detail_summary
from app.admin.deps import AdminContext, require_admin, validate_csrf
from app.admin.export import EXPORT_MAX_ROWS, xlsx_response
from app.admin.pagination import PageParams, paginate
from app.admin.filters import SUB_SORT, SVC_SORT, services_query, subscription_query
from app.admin.routes.services_managers import service_managers
from app.core.deps import get_cipher, get_db, get_notifier
from app.core.clock import kst_format
from app.core.crypto import AesGcmCipher
from app.core.errors import DomainError, NotFoundError
from app.models import (AuditLog, Card, Payment, PaymentKind, PaymentStatus, Plan, Service,
                        Subscription, User, UserRole, UserService, UserStatus)
from app.services import accounts as account_service
from app.services import registry
from app.services.registry import set_toss_secret_key  # 서비스별 토스 시크릿 키 설정(Task 8)
from app.services.audit import record_audit
from app.services.billing_math import (first_amount_breakdown, plan_first_amount,
                                       plan_recurring_amount,
                                       recurring_amount_breakdown)

router = APIRouter()

# 서비스 상세 — 단건(ONE_OFF) 결제 탭 정렬 가능 컬럼 맵 / 페이지당 건수
ONEOFF_SORT = {"requested_at": Payment.requested_at}
ONEOFF_PER_PAGE = 10


def _parse_cancel_policy(form) -> tuple[bool, int]:
    """취소 정책 폼 공통 파싱 — (허용 여부, 수수료율). 등록·정책변경 2곳이 공유(S9).

    체크박스는 체크 시 "on", 미체크 시 폼에 키 자체가 없다.
    수수료율이 비정수면 ValueError — 호출측이 잡아 화면 오류로 변환한다.
    """
    enabled = form.get("cancellation_enabled") == "on"
    fee_percent = int(form.get("cancellation_fee_percent") or 0)
    return enabled, fee_percent


def _parse_ips(raw: str) -> list[str]:
    """줄바꿈/콤마 구분 IP 목록 파싱(라인단위 입력 + 기존 콤마 호환)."""
    return [ip.strip() for chunk in raw.splitlines()
            for ip in chunk.split(",") if ip.strip()]


@router.get("/services")
async def services_list(request: Request, ctx: AdminContext = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    """서비스 목록 페이지 / htmx partial 공용 라우트.

    render_list가 HX-Request 헤더를 감지해,
    htmx 요청이면 _table.html partial만, 일반 요청이면 list.html 전체를 렌더한다.

    paginate가 반환한 Row 튜플에서 Service 단일 엔티티를 꺼낸다.
    (select(Service)이지만 execute().all()은 (Service,) Row를 반환하므로 [0] 인덱싱.)
    """
    pp = PageParams.from_request(request, sortable=set(SVC_SORT),
                                 default_sort="created_at", filter_keys=("status",))
    base = services_query(pp)
    items_q = base.order_by(pp.order_by(SVC_SORT))
    page = await paginate(db, items_q, pp, flatten=True)  # Row → Service
    return render_list(request, "services/list.html", "services/_table.html",
                      ctx=ctx, page=page, pp=pp,
                      status_filter=pp.filters.get("status", ""))


async def _manager_options(db: AsyncSession) -> list[User]:
    """서비스 등록 폼의 담당자 후보 — 삭제되지 않은 SERVICE_MANAGER 전체."""
    return list((await db.scalars(select(User).where(
        User.role == UserRole.SERVICE_MANAGER,
        User.status != UserStatus.DELETED).order_by(User.email))).all())


@router.get("/services/new")
async def services_new(request: Request, ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    """서비스 등록 폼. 담당자 후보 목록을 함께 렌더한다."""
    return render(request, "services/new.html", ctx=ctx, error=None,
                  manager_options=await _manager_options(db))


@router.post("/services")
async def services_create(request: Request,
                          ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db),
                          cipher: AesGcmCipher = Depends(get_cipher)):
    """서비스 등록 처리.

    성공 시 생성된 API 키·HMAC 시크릿을 일회성으로 화면에 표시한다.
    (키는 암호화 저장되므로 이 화면이 평문을 볼 수 있는 유일한 기회.)
    담당자 UUID 파싱 실패는 DomainError 전에 직접 폼 오류로 처리한다.
    """
    await validate_csrf(request, ctx)
    form = await request.form()

    async def form_error(message: str):
        return render(request, "services/new.html", ctx=ctx, error=message,
                      manager_options=await _manager_options(db))

    try:
        manager_ids = [uuid.UUID(str(v)) for v in form.getlist("manager_ids")]
        primary_raw = str(form.get("primary_user_id", "")).strip()
        primary_id = uuid.UUID(primary_raw) if primary_raw else None
    except ValueError:
        return await form_error("유효하지 않은 담당자 계정입니다")
    try:
        cancellation_enabled, cancellation_fee_percent = _parse_cancel_policy(form)
    except (ValueError, TypeError):
        return await form_error("취소 수수료율은 숫자여야 합니다")
    # 토스 시크릿 키 — 빈 값이면 None(등록 시 미설정); AES 암호화 저장은 서비스 레이어가 처리(Task 8)
    toss_secret_key = str(form.get("toss_secret_key", "")).strip() or None
    try:
        creds = await registry.register_service(
            db, cipher,
            name=str(form.get("name", "")),
            allowed_ips=_parse_ips(str(form.get("allowed_ips", ""))),
            manager_user_ids=manager_ids, primary_user_id=primary_id,
            cancellation_enabled=cancellation_enabled,
            cancellation_fee_percent=cancellation_fee_percent,
            toss_secret_key=toss_secret_key,
            actor_user_id=ctx.user.id)
    except DomainError as exc:
        return await form_error(exc.message)
    return render(request, "services/keys.html", ctx=ctx, service=creds.service,
                  api_key=creds.api_key, hmac_secret=creds.hmac_secret, flash=None)


@router.get("/services/{service_id}/keys-modal")
async def services_keys_modal(service_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_admin),
                              db: AsyncSession = Depends(get_db),
                              cipher: AesGcmCipher = Depends(get_cipher)):
    """키 복사 모달 fragment — 평문 키 노출이므로 감사 로그 필수.

    복호화 실패 시 decrypt_error=True를 템플릿에 전달해 모달 내 오류 안내를 표시한다.
    (복호화 오류가 500으로 새지 않도록 Exception을 넓게 처리.)

    Cache-Control: no-store:
        평문 키가 포함된 응답이 브라우저 캐시에 남지 않도록 강제한다.
        htmx fragment라도 브라우저가 history 복원 등으로 캐시를 사용할 수 있으므로
        명시적으로 no-store 헤더를 설정한다.
    """
    # 복호화+감사+commit은 서비스 레이어가 한 단위로 처리(감사 Phase 4 — S8)
    service, api_key, hmac_secret, decrypt_error = await registry.reveal_keys(
        db, cipher, service_id, actor_user_id=ctx.user.id)
    response = render(request, "services/_keys_modal.html", ctx=ctx, service=service,
                      api_key=api_key, hmac_secret=hmac_secret,
                      decrypt_error=decrypt_error)
    response.headers["Cache-Control"] = "no-store"  # 평문 키 — 브라우저 캐시 금지
    return response


async def _plans_tab(db: AsyncSession, service_id: uuid.UUID):
    """요금제 탭 데이터 — 표시용 금액/툴팁을 각 Plan에 주입해 반환."""
    plans = (await db.scalars(select(Plan).where(Plan.service_id == service_id)
                              .order_by(Plan.created_at))).all()
    for p in plans:
        p.recurring_amount = plan_recurring_amount(p)
        p.first_amount = plan_first_amount(p)
        p.first_tooltip = first_amount_breakdown(p)
        p.recurring_tooltip = recurring_amount_breakdown(p)
    return plans


async def _subs_tab(db: AsyncSession, request: Request, service_id: uuid.UUID):
    """구독 탭 데이터 — 서비스 고정, /admin/subscriptions와 동일 필터/정렬. (sub_page, spp) 반환."""
    spp = PageParams.from_request(request, sortable=set(SUB_SORT),
                                  default_sort="created_at", filter_keys=("status",))
    # 공유 빌더(filters.py) — 구독 목록/엑셀과 동일 필터 보장(감사 Phase 4 — S2).
    # 행은 (Subscription, Plan, Service) 3-튜플 — 템플릿도 3-튜플 언패킹.
    base = subscription_query(spp, service_id=service_id)
    # count 쿼리는 paginate가 내부 생성(감사 Phase 4 — S3)
    page = await paginate(db, base.order_by(spp.order_by(SUB_SORT)), spp)
    return page, spp


async def _oneoff_tab(db: AsyncSession, request: Request, service_id: uuid.UUID):
    """단건결제 탭 데이터 — kind=ONE_OFF 고정. (oneoff_page, opp) 반환(Payment 평탄화).

    page_param='opage'로 구독 탭의 'page'와 분리하고, 페이지당 10건으로 표시한다.
    """
    opp = PageParams.from_request(request, sortable={"requested_at"},
                                  default_sort="requested_at", page_param="opage")
    opp.per_page = ONEOFF_PER_PAGE
    base = select(Payment).where(Payment.service_id == service_id,
                                 Payment.kind == PaymentKind.ONE_OFF)
    # count 쿼리는 paginate가 내부 생성(감사 Phase 4 — S3)
    page = await paginate(db, base.order_by(opp.order_by(ONEOFF_SORT)), opp,
                          flatten=True)  # Row → Payment
    return page, opp


# 서비스 상세 — 등록 카드(결제수단 보관함) 탭 정렬 가능 컬럼 맵 / 페이지당 건수
# 카드는 (service_id, external_user_id)당 1건이므로 사용자 ID와 등록/변경 시각 기준 정렬만 제공한다.
CARDS_SORT = {"external_user_id": Card.external_user_id,
              "created_at": Card.created_at,
              "updated_at": Card.updated_at}
CARDS_PER_PAGE = 10


async def _cards_tab(db: AsyncSession, request: Request, service_id: uuid.UUID):
    """등록 카드 탭 데이터 — 이 서비스의 cards 테이블(결제수단 보관함)을 페이징한다.

    카드는 (service_id, external_user_id) 쌍당 1건이므로 이 서비스에 결제수단을
    등록한 사용자별로 1행씩 나온다. 사용자 ID 검색을 지원하고 page_param='kpage'로
    다른 탭의 'page'와 분리한다(페이지당 10건). (card_page, kpp) 반환.

    표시 정보는 _cards_table.html에서 카드번호(마스킹)·발급사·customerKey·
    등록/변경 시각 등 카드 관련 정보를 모두 노출한다(빌링키 암호문은 제외).
    """
    kpp = PageParams.from_request(request, sortable=set(CARDS_SORT),
                                  default_sort="created_at", page_param="kpage")
    kpp.per_page = CARDS_PER_PAGE
    base = select(Card).where(Card.service_id == service_id)
    # 사용자 ID 부분검색(q) — 구독 탭과 동일한 검색 UX 제공
    if kpp.q:
        base = base.where(Card.external_user_id.ilike(f"%{kpp.q}%"))
    # count 쿼리는 paginate가 내부 생성 — Row → Card 평탄화
    page = await paginate(db, base.order_by(kpp.order_by(CARDS_SORT)), kpp,
                          flatten=True)
    return page, kpp


# 서비스 상세 — 이벤트(감사) 섹션 페이지당 건수
EVENTS_PER_PAGE = 10


async def _events_tab(db: AsyncSession, request: Request, service_id: uuid.UUID):
    """이 서비스와 관련된 감사 이벤트를 10건씩 페이징 — 상세 하단 '이벤트' 섹션용(요청 015).

    포함 범위:
      1) 서비스 자체 동작: target_type='service' AND target_id=service_id
         (등록·상태변경·키재발급·키복사·IP갱신·취소정책·대표지정 등)
      2) 이 서비스의 요금제 CRUD: target_type='plan' AND target_id IN (서비스 요금제들)
      3) 담당자 추가/해제: account.(un)assign_service 중 detail.service_id가 이 서비스
      4) 카드 이벤트: target_type='card' 중 detail.service_id가 이 서비스
         (카드 등록/교체/삭제/활성화/비활성화 — 카드 감사 detail에 service_id를 기록)

    page_param='epage' — 구독/단건결제 탭의 'page'와 분리해 서로 영향 없이 페이징한다.
    (events_page, epp) 반환. events_page.items는 표시용 dict
    {time, action(한글), detail(요약), actor(이메일/유형)} 리스트.
    """
    epp = PageParams.from_request(request, sortable=set(), default_sort="",
                                  page_param="epage")
    epp.per_page = EVENTS_PER_PAGE
    plan_ids = select(cast(Plan.id, String)).where(Plan.service_id == service_id)
    base = (select(AuditLog).where(or_(
                and_(AuditLog.target_type == "service",
                     AuditLog.target_id == str(service_id)),
                and_(AuditLog.target_type == "plan",
                     AuditLog.target_id.in_(plan_ids)),
                and_(AuditLog.action.in_(("account.assign_service",
                                          "account.unassign_service")),
                     AuditLog.detail["service_id"].astext == str(service_id)),
                # 이 서비스의 카드 이벤트(등록·교체·삭제·활성/비활성) — detail.service_id로 스코프
                and_(AuditLog.target_type == "card",
                     AuditLog.detail["service_id"].astext == str(service_id))))
            .order_by(AuditLog.created_at.desc()))
    page = await paginate(db, base, epp, flatten=True)  # Row → AuditLog
    logs = page.items
    # 행위자(USER) 이메일 배치 조회 → 표시용
    actor_ids = {l.actor_user_id for l in logs if l.actor_user_id}
    emails: dict = {}
    if actor_ids:
        for u in (await db.scalars(select(User).where(User.id.in_(actor_ids)))).all():
            emails[u.id] = u.email
    page.items = [{"time": l.created_at, "action": action_label(l.action),
                   "detail": detail_summary(l.detail),
                   "actor": actor_label(l.actor_type, emails.get(l.actor_user_id))}
                  for l in logs]
    return page, epp


@router.get("/services/{service_id}")
async def services_detail(service_id: uuid.UUID, request: Request,
                          ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    """서비스 상세 페이지. 요금제·구독·단건결제 탭을 포함한다.

    탭 데이터는 _plans_tab/_subs_tab/_oneoff_tab로 분리한다.

    htmx 탭 분기:
        HX-Target 헤더 값에 따라 세 가지 partial 중 하나만 반환한다.
        - list-svc-plans   → services/_plans_table.html
        - list-svc-subs    → services/_subs_table.html
        - list-svc-oneoff  → services/_oneoff_table.html
        일반 요청(HX-Request 없음)이면 services/detail.html 전체를 렌더한다.

    구독 탭:
        /admin/subscriptions와 동일 패턴으로 PageParams를 구성하되,
        service_id를 고정해 이 서비스의 구독만 조회한다. (_subs_tab)

    단건결제 탭:
        kind == ONE_OFF 조건 고정. paginate 결과 Row에서 Payment를 평탄화한다. (_oneoff_tab)

    요금제:
        각 Plan 인스턴스에 표시용 금액·툴팁을 동적으로 주입한다. (_plans_tab)
    """
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    plans = await _plans_tab(db, service_id)
    sub_count = await db.scalar(select(func.count()).select_from(Subscription)
                                .where(Subscription.service_id == service_id)) or 0
    # 이 서비스를 담당하는 관리자 + 할당 가능한 담당자 계정
    managers, assignable = await service_managers(db, service_id)
    sub_page, spp = await _subs_tab(db, request, service_id)
    oneoff_page, opp = await _oneoff_tab(db, request, service_id)
    # 등록 카드(결제수단 보관함) 탭 — 이 서비스에 카드를 등록한 사용자별 1건
    card_page, kpp = await _cards_tab(db, request, service_id)

    # htmx 부분 요청이면 대상 영역 partial만 응답 (요청 005)
    hx_target = (request.headers.get("HX-Target", "")
                 if request.headers.get("HX-Request") else "")
    template = {"list-svc-plans": "services/_plans_table.html",
                "list-svc-subs": "services/_subs_table.html",
                "list-svc-cards": "services/_cards_table.html",
                "list-svc-oneoff": "services/_oneoff_table.html",
                "list-svc-events": "services/_events_table.html"}.get(
                    hx_target, "services/detail.html")
    # 이벤트(감사) 섹션은 전체 페이지 또는 이벤트 partial 갱신 시에만 조회
    # (다른 탭 partial 갱신에는 불필요)
    events_page = epp = None
    if not hx_target or hx_target == "list-svc-events":
        events_page, epp = await _events_tab(db, request, service_id)
    return render(request, template, ctx=ctx, service=service,
                  plans=plans, plan_count=len(plans), sub_count=sub_count,
                  managers=managers, assignable_managers=assignable,
                  sub_page=sub_page, spp=spp,
                  sub_status_filter=spp.filters.get("status", ""),
                  card_page=card_page, kpp=kpp,
                  oneoff_page=oneoff_page, opp=opp,
                  events_page=events_page, epp=epp,
                  error=request.query_params.get("error"))


@router.post("/services/{service_id}/rotate-keys")
async def services_rotate(service_id: uuid.UUID, request: Request,
                          ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db),
                          cipher: AesGcmCipher = Depends(get_cipher)):
    """API 키·HMAC 시크릿 재발급.

    재발급 후 새 키를 키 화면(services/keys.html)으로 렌더한다.
    flash 파라미터 주입을 방지하기 위해 flash=None을 명시적으로 전달한다.
    (리다이렉트 없이 직접 렌더하므로 ?flash= 파라미터 오염이 없다.)
    """
    await validate_csrf(request, ctx)
    api_key, hmac_secret = await registry.rotate_keys(db, cipher, service_id,
                                                      actor_user_id=ctx.user.id)
    service = await db.get(Service, service_id)
    # 키 재발급은 메일 발송이 없으므로 flash 없음 (쿼리파람 주입 차단)
    return render(request, "services/keys.html", ctx=ctx, service=service,
                  api_key=api_key, hmac_secret=hmac_secret, flash=None)


@router.post("/services/{service_id}/ips")
async def services_update_ips(service_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_admin),
                              db: AsyncSession = Depends(get_db)):
    """서비스 허용 IP 목록 업데이트. _parse_ips로 줄바꿈/콤마 모두 처리한다."""
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        await registry.update_allowed_ips(
            db, service_id, _parse_ips(str(form.get("allowed_ips", ""))),
            actor_user_id=ctx.user.id)
    except DomainError as exc:
        return RedirectResponse(f"/admin/services/{service_id}?error={exc.message}",
                                status_code=303)
    # 허용 IP 업데이트 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/services/{service_id}", "저장되었습니다")


@router.post("/services/{service_id}/cancel-policy")
async def services_cancel_policy(service_id: uuid.UUID, request: Request,
                                 ctx: AdminContext = Depends(require_admin),
                                 db: AsyncSession = Depends(get_db)):
    """서비스 단건결제 취소 정책 업데이트(허용 여부 + 수수료율).

    폼 필드:
        cancellation_enabled  — 체크박스("on" = True, 미체크 = False)
        cancellation_fee_percent — 정수 0~100(수수료율 %)

    DomainError 시 오류 메시지를 쿼리파람으로 상세 페이지에 전달한다.
    """
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        enabled, fee_percent = _parse_cancel_policy(form)
    except (ValueError, TypeError):
        return RedirectResponse(
            f"/admin/services/{service_id}?error={quote('취소 수수료율은 숫자여야 합니다')}",
            status_code=303)
    try:
        await registry.update_cancel_policy(
            db, service_id, enabled=enabled, fee_percent=fee_percent,
            actor_user_id=ctx.user.id)
    except DomainError as exc:
        return RedirectResponse(f"/admin/services/{service_id}?error={quote(exc.message)}",
                                status_code=303)
    # 취소 정책 저장 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/services/{service_id}", "저장되었습니다")


@router.post("/services/{service_id}/toss-secret-key")
async def services_set_toss_secret_key(service_id: uuid.UUID, request: Request,
                                       ctx: AdminContext = Depends(require_admin),
                                       db: AsyncSession = Depends(get_db),
                                       cipher: AesGcmCipher = Depends(get_cipher)):
    """서비스별 토스 시크릿 키 설정/교체(Task 8).

    폼 필드 toss_secret_key — 빈 값이면 변경 없음(기존 키 유지).
    평문은 화면·로그·감사에 절대 노출하지 않는다; AES 암호화 저장은 서비스 레이어가 처리.
    감사 액션: 최초 설정 → service.toss_secret_key.set, 교체 → service.toss_secret_key.changed.
    """
    await validate_csrf(request, ctx)
    form = await request.form()
    # 빈 값이면 변경 없음 — 폼을 빈 채로 저장해도 기존 키는 유지된다
    new_key = str(form.get("toss_secret_key", "")).strip()
    if new_key:
        await set_toss_secret_key(db, cipher, service_id=service_id,
                                  toss_secret_key=new_key,
                                  actor_user_id=ctx.user.id)
    return saved_redirect(f"/admin/services/{service_id}", "저장되었습니다")


@router.post("/services/{service_id}/notification-url")
async def services_notification_url(service_id: uuid.UUID, request: Request,
                                    ctx: AdminContext = Depends(require_admin),
                                    db: AsyncSession = Depends(get_db)):
    """서비스 알림(아웃고잉 웹훅) 수신 URL 저장(요청 016).

    폼 필드 notification_url — http(s) URL 또는 빈 문자열(빈값이면 알림 끔=NULL).
    형식 오류는 ?error=로 상세 페이지에 표시한다. 감사로그 service.notification_url_updated.
    """
    await validate_csrf(request, ctx)
    form = await request.form()
    raw = str(form.get("notification_url", "")).strip()
    if raw and not (raw.startswith("http://") or raw.startswith("https://")):
        return RedirectResponse(
            f"/admin/services/{service_id}?error={quote('알림 URL은 http:// 또는 https://로 시작해야 합니다')}",
            status_code=303)
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    old_url = service.notification_url
    service.notification_url = raw or None      # 빈값 → NULL(알림 끔)
    await record_audit(db, actor_type="USER", actor_user_id=ctx.user.id,
                       action="service.notification_url_updated", target_type="service",
                       target_id=str(service_id),
                       detail={"old_url": old_url or "", "new_url": service.notification_url or ""})
    await db.commit()
    return saved_redirect(f"/admin/services/{service_id}", "저장되었습니다")


@router.post("/services/{service_id}/notification-test")
async def services_notification_test(service_id: uuid.UUID, request: Request,
                                     ctx: AdminContext = Depends(require_admin),
                                     db: AsyncSession = Depends(get_db),
                                     notifier=Depends(get_notifier)):
    """저장된 알림 URL로 '테스트 알림'을 전송한다(요청 016 — 설정 확인용).

    동기 전송이라 수신 측 응답(성공/실패)을 토스트로 즉시 보여준다.
    알림 URL 미등록이거나 수신 실패면 ?error=로 안내한다.
    """
    await validate_csrf(request, ctx)
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    ok, detail = await notifier.send_test(service)
    if ok:
        return saved_redirect(f"/admin/services/{service_id}",
                              f"테스트 알림을 전송했습니다 ({detail})")
    return RedirectResponse(
        f"/admin/services/{service_id}?error={quote(f'테스트 알림 전송 실패: {detail}')}",
        status_code=303)


@router.post("/services/{service_id}/status")
async def services_set_status(service_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_admin),
                              db: AsyncSession = Depends(get_db)):
    """서비스 상태 변경(ACTIVE / INACTIVE 등)."""
    await validate_csrf(request, ctx)
    form = await request.form()
    await registry.set_service_status(db, service_id, str(form.get("status", "")),
                                      actor_user_id=ctx.user.id)
    # 서비스 상태 변경 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/services/{service_id}", "변경되었습니다")


@router.post("/services/{service_id}/delete")
async def services_delete(service_id: uuid.UUID, request: Request,
                          ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    """서비스 삭제. 구독이 있는 서비스는 서비스 레이어에서 DomainError로 거부된다."""
    await validate_csrf(request, ctx)
    try:
        await registry.delete_service(db, service_id, actor_user_id=ctx.user.id)
    except DomainError as exc:
        # 구독 있는 서비스 삭제 거부 — 에러 경로는 saved 없이 리다이렉트
        return RedirectResponse(f"/admin/services/{service_id}?error={exc.message}",
                                status_code=303)
    # 서비스 삭제 성공 → 완료 모달 트리거
    return saved_redirect("/admin/services", "삭제되었습니다")
