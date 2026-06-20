"""admin 서비스 담당자 관리 라우트 + 담당자 목록 헬퍼.

services.py(목록/상세/등록/키 관리)에서 분리(감사 Phase 4 — S6).
URL prefix·템플릿은 그대로 — 라우터 등록만 추가됐다.
service_managers 헬퍼는 서비스 상세 화면(services.py)도 사용한다.

POST /services/{id}/assign-manager            — 담당자 추가
POST /services/{id}/primary-manager           — 대표 담당자 변경
POST /services/{id}/managers/{user_id}/remove — 담당자 해제(대표는 도메인이 거부)
"""
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import saved_redirect
from app.admin.deps import AdminContext, require_admin, validate_csrf
from app.core.deps import get_db
from app.core.errors import DomainError
from app.models import User, UserRole, UserService
from app.services import accounts as account_service
from app.services import registry

router = APIRouter()


async def service_managers(db: AsyncSession, service_id: uuid.UUID):
    """(담당 관리자 목록, 할당 가능한 SERVICE_MANAGER 계정 목록).

    담당 관리자:
        User.service_id == service_id (주 서비스 지정 계정) +
        UserService 테이블에 등록된 추가 담당 계정을 합산한다.
        dict로 중복을 제거한다.

    할당 가능:
        SERVICE_MANAGER 전체에서 현재 담당자를 제외한 목록.
        담당자 추가 드롭다운에 사용된다.
    """
    primary = (await db.scalars(select(User).where(
        User.service_id == service_id))).all()
    extra_ids = (await db.scalars(select(UserService.user_id).where(
        UserService.service_id == service_id))).all()
    extra = []
    if extra_ids:
        extra = (await db.scalars(select(User).where(User.id.in_(extra_ids)))).all()
    managers = {u.id: u for u in [*primary, *extra]}
    all_mgr = (await db.scalars(select(User).where(
        User.role == UserRole.SERVICE_MANAGER).order_by(User.email))).all()
    assignable = [u for u in all_mgr if u.id not in managers]
    return list(managers.values()), assignable


@router.post("/services/{service_id}/assign-manager")
async def services_assign_manager(service_id: uuid.UUID, request: Request,
                                  ctx: AdminContext = Depends(require_admin),
                                  db: AsyncSession = Depends(get_db)):
    """서비스에 담당자 추가 할당. UUID 파싱 실패도 오류로 처리한다."""
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        await account_service.assign_service(
            db, user_id=uuid.UUID(str(form.get("user_id"))), service_id=service_id,
            actor_user_id=ctx.user.id)
    except (DomainError, ValueError) as exc:
        msg = exc.message if isinstance(exc, DomainError) else "유효하지 않은 계정"
        return RedirectResponse(f"/admin/services/{service_id}?error={msg}",
                                status_code=303)
    # 담당자 할당 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/services/{service_id}", "저장되었습니다")


@router.post("/services/{service_id}/primary-manager")
async def services_set_primary_manager(service_id: uuid.UUID, request: Request,
                                       ctx: AdminContext = Depends(require_admin),
                                       db: AsyncSession = Depends(get_db)):
    """서비스의 대표 담당자 변경. 대표 담당자는 service.manager_email로 등록된다."""
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        await registry.set_primary_manager(
            db, service_id, user_id=uuid.UUID(str(form.get("user_id"))),
            actor_user_id=ctx.user.id)
    except (DomainError, ValueError) as exc:
        msg = exc.message if isinstance(exc, DomainError) else "유효하지 않은 계정"
        return RedirectResponse(f"/admin/services/{service_id}?error={msg}",
                                status_code=303)
    # 대표 담당자 변경 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/services/{service_id}", "변경되었습니다")


@router.post("/services/{service_id}/managers/{user_id}/remove")
async def services_remove_manager(service_id: uuid.UUID, user_id: uuid.UUID,
                                  request: Request,
                                  ctx: AdminContext = Depends(require_admin),
                                  db: AsyncSession = Depends(get_db)):
    """서비스 담당자 해제.

    대표 담당자(service.manager_email과 동일한 계정)는 해제할 수 없다 —
    이 규칙은 서비스 레이어(accounts.unassign_service)가 ConflictError로 강제한다
    (감사 Phase 4 — S4: 라우트에 있던 검사를 도메인으로 내려 모든 진입점에 적용).
    라우트는 DomainError 메시지를 ?error=로 표시만 한다.
    """
    await validate_csrf(request, ctx)
    try:
        await account_service.unassign_service(db, user_id=user_id, service_id=service_id,
                                               actor_user_id=ctx.user.id)
    except DomainError as exc:
        return RedirectResponse(
            f"/admin/services/{service_id}?error={quote(exc.message)}", status_code=303)
    # 담당자 해제 성공 → 완료 모달 트리거
    return saved_redirect(f"/admin/services/{service_id}", "해제되었습니다")
