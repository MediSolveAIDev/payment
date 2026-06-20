"""admin 관리자 계정 관리 라우트.

계정 목록·엑셀·생성·상세·수정·비활성화/활성화·삭제·비밀번호 재설정을 제공한다.
모든 엔드포인트는 SYSTEM_ADMIN 전용(require_admin)이다.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render, render_list, saved_redirect
from app.admin.deps import AdminContext, require_admin, validate_csrf
from app.admin.export import xlsx_response
from app.admin.flash import email_flash_qs
from app.admin.pagination import PageParams, paginate
from app.core.deps import get_db, get_email_sender, get_redis, get_settings
from app.core.errors import DomainError, NotFoundError
from app.models import Service, User, UserStatus
from app.services import accounts as account_service
from app.services import registry
from app.services.auth import issue_password_reset

router = APIRouter()

# 정렬 가능 컬럼 맵
_USER_SORT = {"email": User.email, "role": User.role, "status": User.status,
              "created_at": User.created_at}


def _build_users_query(pp: PageParams):
    """목록·엑셀이 공유하는 계정 검색/필터 쿼리.

    DELETED 계정 기본 제외:
        삭제된 계정은 논리 삭제(status=DELETED)이므로 DB에 잔류하지만,
        목록/엑셀에서는 기본적으로 노출하지 않는다.
        status 필터로 'DELETED'를 명시 선택해도 이 조건이 먼저 적용되므로
        삭제된 계정은 어떤 필터를 사용해도 목록에 나타나지 않는다.

    Service LEFT OUTER JOIN:
        담당 서비스(User.service_id)가 없는 계정(SYSTEM_ADMIN 등)도
        누락 없이 조회하기 위해 INNER JOIN 대신 OUTER JOIN을 사용한다.

    필터:
        q      — User.email 부분 일치
        role   — 정확 일치
        status — 정확 일치 (단, DELETED는 기본 제외 조건으로 이미 필터됨)
    """
    base = (select(User, Service).outerjoin(Service, User.service_id == Service.id)
            .where(User.status != UserStatus.DELETED))
    if pp.q:
        base = base.where(User.email.ilike(f"%{pp.q}%"))
    if pp.filters.get("role"):
        base = base.where(User.role == pp.filters["role"])
    if pp.filters.get("status"):
        base = base.where(User.status == pp.filters["status"])
    return base


def _parse_service_ids(form) -> list[uuid.UUID]:
    """폼의 service_ids 멀티셀렉트 값을 UUID 목록으로 변환한다.

    파싱 실패 값은 조용히 건너뛴다(사용자 입력 오염 방어).
    """
    out: list[uuid.UUID] = []
    for raw in form.getlist("service_ids"):
        raw = str(raw).strip()
        if raw:
            try:
                out.append(uuid.UUID(raw))
            except ValueError:
                continue
    return out


@router.get("/users")
async def users_list(request: Request, ctx: AdminContext = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    """계정 목록 페이지 / htmx partial 공용 라우트.

    render_list가 HX-Request 헤더를 감지해,
    htmx 요청이면 _table.html partial만, 일반 요청이면 list.html 전체를 렌더한다.
    """
    pp = PageParams.from_request(request, sortable=set(_USER_SORT),
                                 default_sort="created_at",
                                 filter_keys=("role", "status"))
    base = _build_users_query(pp)
    items_q = base.order_by(pp.order_by(_USER_SORT))
    page = await paginate(db, items_q, pp)
    return render_list(request, "users/list.html", "users/_table.html",
                      ctx=ctx, page=page, pp=pp,
                      role_filter=pp.filters.get("role", ""),
                      status_filter=pp.filters.get("status", ""))


@router.get("/users/export.xlsx")
async def users_export(request: Request, ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    """현재 필터/검색이 적용된 계정 전체를 xlsx로 다운로드.

    paginate를 거치지 않고 쿼리를 직접 실행해 페이지네이션을 무시한다.
    """
    pp = PageParams.from_request(request, sortable=set(_USER_SORT),
                                 default_sort="created_at", filter_keys=("role", "status"))
    items_q = _build_users_query(pp).order_by(pp.order_by(_USER_SORT))
    rows = [[u.email, u.role, (svc.name if svc else "-"), u.status]
            for u, svc in (await db.execute(items_q)).all()]
    return xlsx_response("users", ["이메일", "역할", "주 서비스", "상태"],
                         rows, sheet_title="관리자")


@router.get("/users/new")
async def users_new(request: Request, ctx: AdminContext = Depends(require_admin),
                    db: AsyncSession = Depends(get_db)):
    """계정 생성 폼. 서비스 목록은 SERVICE_MANAGER 역할 선택 시 담당 서비스 지정에 사용된다."""
    services = await registry.list_services(db)
    return render(request, "users/new.html", ctx=ctx, services=services, error=None)


@router.post("/users")
async def users_create(request: Request, ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db),
                       email_sender=Depends(get_email_sender),
                       settings=Depends(get_settings)):
    """계정 생성 처리.

    생성 성공 시 계정 설정 메일 발송 여부를 flash 메시지로 전달한다.
    메일 발송 실패 여부에 따라 다른 flash 메시지가 표시된다(email_flash_qs 참조).
    """
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        _, sent = await account_service.create_account(
            db, email_sender, email=str(form.get("email", "")),
            role=str(form.get("role", "")), service_ids=_parse_service_ids(form),
            phone=str(form.get("phone", "")),
            base_url=settings.base_url, actor_user_id=ctx.user.id)
    except DomainError as exc:
        services = await registry.list_services(db)
        return render(request, "users/new.html", ctx=ctx, services=services,
                      error=exc.message)
    qs = email_flash_qs(sent, "계정 설정 메일을 발송했습니다")
    # 계정 생성 성공 → 완료 모달 + 이메일 발송 결과 토스트를 함께 전달
    return saved_redirect(f"/admin/users?{qs}", "저장되었습니다")


@router.get("/users/{user_id}")
async def users_detail(user_id: uuid.UUID, request: Request,
                       ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    """계정 상세 페이지.

    managed: 현재 담당 서비스 목록.
    assignable: 아직 담당하지 않는 서비스 목록(추가 할당 드롭다운용).
    """
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("계정을 찾을 수 없습니다")
    managed = await account_service.list_managed_services(db, user)
    managed_ids = {s.id for s in managed}
    all_services = await registry.list_services(db)
    assignable = [s for s in all_services if s.id not in managed_ids]
    return render(request, "users/detail.html", ctx=ctx, account=user,
                  managed=managed, assignable=assignable,
                  error=request.query_params.get("error"))


@router.post("/users/{user_id}/services")
async def users_assign_service(user_id: uuid.UUID, request: Request,
                               ctx: AdminContext = Depends(require_admin),
                               db: AsyncSession = Depends(get_db)):
    """계정에 서비스 담당 할당. 실패 시 상세 페이지에 ?error= 붙여 리다이렉트."""
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        await account_service.assign_service(
            db, user_id=user_id, service_id=uuid.UUID(str(form.get("service_id"))),
            actor_user_id=ctx.user.id)
    except (DomainError, ValueError) as exc:
        msg = exc.message if isinstance(exc, DomainError) else "유효하지 않은 서비스"
        return RedirectResponse(f"/admin/users/{user_id}?error={msg}", status_code=303)
    # 서비스 담당 할당 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/users/{user_id}", "저장되었습니다")


@router.post("/users/{user_id}/services/{service_id}/remove")
async def users_unassign_service(user_id: uuid.UUID, service_id: uuid.UUID,
                                 request: Request,
                                 ctx: AdminContext = Depends(require_admin),
                                 db: AsyncSession = Depends(get_db)):
    """계정의 서비스 담당 해제."""
    await validate_csrf(request, ctx)
    await account_service.unassign_service(db, user_id=user_id, service_id=service_id,
                                           actor_user_id=ctx.user.id)
    # 서비스 담당 해제 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/users/{user_id}", "해제되었습니다")


@router.get("/users/{user_id}/edit")
async def users_edit(user_id: uuid.UUID, request: Request,
                     ctx: AdminContext = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    """계정 수정 폼. DELETED 상태 계정은 수정 불가 — 404 처리."""
    user = await db.get(User, user_id)
    if user is None or user.status == UserStatus.DELETED:
        raise NotFoundError("계정을 찾을 수 없습니다")
    return render(request, "users/edit.html", ctx=ctx, account=user, error=None)


@router.post("/users/{user_id}/edit")
async def users_update(user_id: uuid.UUID, request: Request,
                       ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    """계정 수정 처리(이메일·전화번호). 실패 시 수정 폼을 재렌더한다."""
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        await account_service.update_account(
            db, user_id=user_id, email=str(form.get("email", "")),
            phone=str(form.get("phone", "")), actor_user_id=ctx.user.id)
    except DomainError as exc:
        user = await db.get(User, user_id)
        return render(request, "users/edit.html", ctx=ctx, account=user,
                      error=exc.message)
    # 계정 수정 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/users/{user_id}", "저장되었습니다")


@router.post("/users/{user_id}/disable")
async def users_disable(user_id: uuid.UUID, request: Request,
                        ctx: AdminContext = Depends(require_admin),
                        db: AsyncSession = Depends(get_db),
                        redis: Redis = Depends(get_redis)):
    """계정 비활성화/활성화 토글.

    폼 값 처리:
        체크박스 방식이 아니라 hidden input으로 "true"/"false" 문자열을 전달한다.
        값이 정확히 "false"일 때만 disabled=False(활성화)로 처리하고,
        그 외 모든 값("true", 누락, 기타)은 disabled=True(비활성화)로 처리한다.
        이렇게 하면 HTML 체크박스의 미전송 문제 없이 의도를 명확히 표현할 수 있다.

    redis 전달:
        비활성화 시 기존 세션을 즉시 무효화하기 위해 Redis를 서비스 레이어에 전달한다.
    """
    await validate_csrf(request, ctx)
    disabled = str((await request.form()).get("disabled", "true")) != "false"
    try:
        await account_service.set_account_disabled(
            db, redis, user_id=user_id, disabled=disabled, actor_user_id=ctx.user.id)
    except DomainError as exc:
        return RedirectResponse(f"/admin/users/{user_id}?error={exc.message}",
                                status_code=303)
    # 계정 비활성화/활성화 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/users/{user_id}", "변경되었습니다")


@router.post("/users/{user_id}/delete")
async def users_delete(user_id: uuid.UUID, request: Request,
                       ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db),
                       redis: Redis = Depends(get_redis)):
    """계정 논리 삭제. 성공 시 세션을 즉시 무효화하고 목록으로 리다이렉트한다."""
    await validate_csrf(request, ctx)
    try:
        await account_service.delete_account(
            db, redis, user_id=user_id, actor_user_id=ctx.user.id)
    except DomainError as exc:
        return RedirectResponse(f"/admin/users/{user_id}?error={exc.message}",
                                status_code=303)
    # 계정 삭제 성공 → 완료 모달 트리거
    return saved_redirect("/admin/users", "삭제되었습니다")


@router.post("/users/{user_id}/reset-password")
async def users_reset_password(user_id: uuid.UUID, request: Request,
                               ctx: AdminContext = Depends(require_admin),
                               db: AsyncSession = Depends(get_db),
                               redis: Redis = Depends(get_redis),
                               email_sender=Depends(get_email_sender),
                               settings=Depends(get_settings)):
    """비밀번호 재설정 메일 발송.

    발송 성공/실패 여부를 email_flash_qs로 인코딩해 상세 페이지에 flash 토스트로 표시한다.
    """
    await validate_csrf(request, ctx)
    sent = await issue_password_reset(db, email_sender, user_id=user_id,
                                      base_url=settings.base_url, actor_user_id=ctx.user.id,
                                      redis=redis)
    qs = email_flash_qs(sent, "비밀번호 재설정 메일을 발송했습니다")
    return RedirectResponse(f"/admin/users/{user_id}?{qs}", status_code=303)
