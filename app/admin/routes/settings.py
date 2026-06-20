"""어드민 전체설정 화면 — 재시도/어드민IP/킬스위치(SYSTEM_ADMIN 전용).

GET  /settings           — 현재 GlobalSettings 값을 렌더
POST /settings/retry     — 자동결제 재시도 설정 저장
POST /settings/admin-ips — 어드민 접속 허용 IP 목록 저장
POST /settings/server-toggle — 결제서버 킬스위치(활성/비활성) 전환
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render, saved_redirect
from app.admin.deps import AdminContext, require_admin, validate_csrf
from app.core.deps import get_client_ip, get_db, get_redis, get_settings
from app.core.config import Settings
from app.core.errors import DomainError
from app.services import app_settings

router = APIRouter()


@router.get("/settings")
async def settings_page(
    request: Request,
    ctx: AdminContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """전역설정 화면 렌더(현재 GlobalSettings 값).

    쿼리 파라미터:
        error — 이전 폼 처리 실패 시 표시할 오류 메시지.
        saved — DB 쓰기 성공 시 완료 모달 트리거(render()에서 공통 처리).
    """
    gs = await app_settings.get_global_settings(db)
    return render(
        request,
        "settings/index.html",
        ctx=ctx,
        gs=gs,
        error=request.query_params.get("error"),
    )


@router.post("/settings/retry")
async def settings_retry(
    request: Request,
    ctx: AdminContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """자동결제 재시도 설정(retry_limit/retry_interval_hours/suspended_grace_days) 저장.

    성공 → /admin/settings?saved=… 리다이렉트(완료 모달).
    실패 → /admin/settings?error=<메시지> 리다이렉트.
    """
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        await app_settings.update_retry_settings(
            db,
            retry_limit=int(form.get("retry_limit", 0)),
            retry_interval_hours=int(form.get("retry_interval_hours", 1)),
            suspended_grace_days=int(form.get("suspended_grace_days", 0)),
            actor_user_id=ctx.user.id,
        )
    except (ValueError, DomainError) as exc:
        # DomainError는 .message 속성으로 한글 메시지, ValueError는 숫자 입력 오류
        msg = exc.message if isinstance(exc, DomainError) else "숫자를 입력하세요"
        return RedirectResponse(f"/admin/settings?error={quote(msg)}", status_code=303)
    # 재시도 설정 저장 성공 → 완료 모달 트리거
    return saved_redirect("/admin/settings", "저장되었습니다")


@router.post("/settings/security-policy")
async def settings_security_policy(
    request: Request,
    ctx: AdminContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """보안/결제 정책(잠금 임계치·잠금 시간·단건결제 상한) 저장 — 즉시(런타임) 적용.

    성공 → /admin/settings?saved=… 리다이렉트(완료 모달).
    실패 → /admin/settings?error=<메시지> 리다이렉트.
    """
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        await app_settings.update_security_policy(
            db,
            max_failed_logins=int(form.get("max_failed_logins", 0)),
            account_lock_minutes=int(form.get("account_lock_minutes", 0)),
            # 천단위 콤마는 클라이언트가 제출 전 제거하지만, JS 미동작 대비 서버에서도 제거
            one_off_max_amount=int(str(form.get("one_off_max_amount", 0)).replace(",", "")),
            actor_user_id=ctx.user.id,
        )
    except (ValueError, DomainError) as exc:
        msg = exc.message if isinstance(exc, DomainError) else "숫자를 입력하세요"
        return RedirectResponse(f"/admin/settings?error={quote(msg)}", status_code=303)
    return saved_redirect("/admin/settings", "저장되었습니다")


@router.post("/settings/admin-ips")
async def settings_admin_ips(
    request: Request,
    ctx: AdminContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """어드민 접속 허용 IP 목록 저장.

    폼 textarea(admin_allowed_ips)의 줄바꿈 구분 IP를 파싱해 저장한다.
    lockout 방지: 현재 접속 IP가 목록에 없으면 InputValidationError.

    성공 → /admin/settings?saved=… 리다이렉트(완료 모달).
    실패 → /admin/settings?error=<메시지> 리다이렉트.
    """
    await validate_csrf(request, ctx)
    form = await request.form()
    # 줄바꿈으로 구분된 IP 목록 파싱(빈 줄 제거)
    ips = [
        ln.strip()
        for ln in str(form.get("admin_allowed_ips", "")).splitlines()
        if ln.strip()
    ]
    try:
        await app_settings.update_admin_ips(
            db,
            ips=ips,
            current_ip=get_client_ip(request, settings),
            actor_user_id=ctx.user.id,
        )
    except DomainError as exc:
        return RedirectResponse(
            f"/admin/settings?error={quote(exc.message)}", status_code=303
        )
    # 허용 IP 저장 성공 → 완료 모달 트리거
    return saved_redirect("/admin/settings", "저장되었습니다")


@router.post("/settings/server-toggle")
async def settings_server_toggle(
    request: Request,
    ctx: AdminContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """결제서버 킬스위치 전환(활성/비활성).

    폼 필드(버튼이 동작을 결정 — hidden disabled에 목표 상태를 담아 전송):
        disabled — on/true/1 이면 비활성화, 나머지(빈 값 포함)는 활성화.
        reason   — 비활성화 사유(disabled=true 시 필수).
        password — 작업자 본인 비밀번호 재확인.

    성공 → /admin/settings?saved=… 리다이렉트(완료 모달).
    실패(비번 불일치·사유 없음) → /admin/settings?error=<메시지> 리다이렉트.
    """
    await validate_csrf(request, ctx)
    form = await request.form()
    # hidden disabled(버튼이 전달): on/true/1 이면 비활성화, 빈 값이면 활성화
    disabled = str(form.get("disabled", "")) in ("on", "true", "1")
    try:
        # redis 전달 — 킬스위치 캐시(성능 M4)를 즉시 무효화해 전파 지연 제거
        await app_settings.set_server_disabled(
            db,
            disabled=disabled,
            reason=str(form.get("reason", "")),
            actor_user=ctx.user,
            password=str(form.get("password", "")),
            redis=redis,
        )
    except DomainError as exc:
        return RedirectResponse(
            f"/admin/settings?error={quote(exc.message)}", status_code=303
        )
    # 킬스위치 전환 성공 → 완료 모달 트리거
    return saved_redirect("/admin/settings", "저장되었습니다")
