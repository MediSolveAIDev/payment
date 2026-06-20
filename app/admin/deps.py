"""Admin 화면 의존성 주입 및 인증·인가 유틸리티.

세션 쿠키(admin_session)를 기반으로 로그인 여부를 확인하고,
역할(role)에 따른 접근 제어와 CSRF 검증을 FastAPI Depends 체인으로 제공한다.

인증 흐름:
  1. require_user  — 세션 쿠키 → Redis 세션 조회 → DB 사용자 확인 → AdminContext 반환.
  2. require_role  — AdminContext.user.role을 허용 목록과 대조.
  3. require_admin — SYSTEM_ADMIN 전용 축약.
  4. require_any   — SYSTEM_ADMIN 또는 SERVICE_MANAGER 허용 축약.
  5. validate_csrf — 폼 hidden 필드 또는 X-CSRF-Token 헤더와 세션 토큰 비교.
"""

import uuid
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_client_ip, get_db, get_redis, get_settings, is_loopback_ip
from app.core.config import Settings
from app.core.errors import PermissionDeniedError
from app.core.security import constant_time_equals
from app.models import User, UserRole, UserStatus
from app.services import accounts as account_service
from app.services import auth as auth_service
from app.services.app_settings import get_global_settings

SESSION_COOKIE = "admin_session"


class AdminAuthRequired(Exception):
    """미인증 — /admin/login으로 리다이렉트.

    register_admin_exception_handlers에 등록된 핸들러가 잡아
    일반 요청은 303 리다이렉트, HX-Request(htmx)는 HX-Redirect 헤더(204)로 처리한다.
    """


@dataclass
class AdminContext:
    """인증된 관리자 요청의 컨텍스트.

    Attributes:
        user: 현재 로그인한 사용자 ORM 객체.
        session_id: Redis 세션 키 (로그아웃 시 삭제에 사용).
        csrf_token: 세션에 저장된 CSRF 토큰 (폼·헤더 값과 비교).
        service_ids: 담당 서비스 UUID 목록. SYSTEM_ADMIN이면 None(전체 접근).
    """

    user: User
    session_id: str
    csrf_token: str
    # 담당 서비스 ID. SYSTEM_ADMIN이면 None(전체 접근).
    service_ids: list[uuid.UUID] | None = None


async def require_user(request: Request,
                       db: AsyncSession = Depends(get_db),
                       redis: Redis = Depends(get_redis),
                       settings: Settings = Depends(get_settings)) -> AdminContext:
    """세션 쿠키로 로그인 여부를 확인하고 AdminContext를 반환한다.

    세션이 없거나 만료됐거나 사용자가 비활성 상태면 AdminAuthRequired를 발생시킨다.
    또한 GlobalSettings.admin_allowed_ips가 비어있지 않으면(요청 013) 현재 접속 IP가
    목록에 포함돼야 하며, 아니면 PermissionDeniedError(403)로 차단한다(빈 목록=제한 없음).
    """
    session_id = request.cookies.get(SESSION_COOKIE, "")
    data = await auth_service.get_session(redis, settings, session_id)
    if data is None:
        raise AdminAuthRequired()
    user = await auth_service.get_user(db, data.get("user_id", ""))
    if user is None or user.status != UserStatus.ACTIVE:
        raise AdminAuthRequired()
    service_ids = await account_service.effective_service_ids(db, user)
    # 어드민 접속 IP 제한(요청 013): admin_allowed_ips가 비어있지 않으면 현재 IP가 포함돼야 함.
    # 단, 127.0.0.1/::1(같은 서버, 로컬)은 목록과 무관하게 항상 허용한다.
    gs = await get_global_settings(db)
    ip = get_client_ip(request, settings)
    if gs.admin_allowed_ips and ip not in gs.admin_allowed_ips and not is_loopback_ip(ip):
        raise PermissionDeniedError("허용되지 않은 IP입니다")
    return AdminContext(user=user, session_id=session_id,
                        csrf_token=data.get("csrf_token", ""), service_ids=service_ids)


def require_role(*roles: str):
    """지정한 역할 중 하나를 가진 사용자만 허용하는 Depends 팩토리.

    반환된 checker를 Depends()에 사용하면 역할 검사 후 AdminContext를 주입한다.
    역할이 일치하지 않으면 PermissionDeniedError(403)를 발생시킨다.
    """
    async def checker(ctx: AdminContext = Depends(require_user)) -> AdminContext:
        if ctx.user.role not in roles:
            raise PermissionDeniedError("접근 권한이 없습니다")
        return ctx
    return checker


# SYSTEM_ADMIN 전용 엔드포인트에 사용하는 Depends 축약
require_admin = require_role(UserRole.SYSTEM_ADMIN)
# SYSTEM_ADMIN 또는 SERVICE_MANAGER 모두 허용하는 Depends 축약
require_any = require_role(UserRole.SYSTEM_ADMIN, UserRole.SERVICE_MANAGER)


async def validate_csrf(request: Request, ctx: AdminContext) -> None:
    """모든 admin POST는 호출 필수. 폼 hidden 필드 또는 X-CSRF-Token 헤더."""
    form = await request.form()
    token = str(form.get("csrf_token", "")) or request.headers.get("x-csrf-token", "")
    if not token or not constant_time_equals(token, ctx.csrf_token):
        raise PermissionDeniedError("CSRF 토큰이 유효하지 않습니다")


def service_scope(ctx: AdminContext) -> list[uuid.UUID] | None:
    """담당 서비스 ID 목록. SYSTEM_ADMIN이면 None(전체)."""
    return ctx.service_ids


def register_admin_exception_handlers(app: FastAPI) -> None:
    """AdminAuthRequired 예외를 로그인 리다이렉트로 변환하는 핸들러를 등록한다.

    htmx 요청(HX-Request 헤더 존재)은 HX-Redirect 헤더를 포함한 204로 응답해
    클라이언트 측 htmx가 전체 페이지 이동을 수행하도록 한다.
    일반 요청은 303 See Other로 /admin/login에 리다이렉트한다.
    """
    @app.exception_handler(AdminAuthRequired)
    async def auth_required_handler(request: Request, exc: AdminAuthRequired):
        if request.headers.get("hx-request"):
            return Response(status_code=204, headers={"HX-Redirect": "/admin/login"})
        return RedirectResponse("/admin/login", status_code=303)
