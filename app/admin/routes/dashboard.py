"""Admin 대시보드 라우트.

SYSTEM_ADMIN과 SERVICE_MANAGER 모두 접근할 수 있다(require_any).
서비스 스코프(ctx.service_ids)를 build_dashboard에 전달하므로
SERVICE_MANAGER는 자신이 담당한 서비스 데이터만 조회한다.
SYSTEM_ADMIN은 service_ids=None으로 전체 데이터를 조회한다.

is_admin 플래그는 템플릿에서 SYSTEM_ADMIN 전용 메뉴·수치를 조건부로 렌더링하는 데 사용한다.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_any
from app.core.deps import get_db
from app.core.clock import utcnow
from app.models import UserRole
from app.services.dashboard import build_dashboard

router = APIRouter()


@router.get("")
async def dashboard(request: Request,
                    ctx: AdminContext = Depends(require_any),
                    db: AsyncSession = Depends(get_db)):
    """대시보드 페이지를 렌더링한다.

    build_dashboard(db, ctx.service_ids)로 집계 데이터를 조회하고
    dashboard.html 템플릿에 전달한다. is_admin=True면 전체 통계와
    관리자 전용 섹션이 표시된다.
    """
    data = await build_dashboard(db, ctx.service_ids)
    return render(request, "dashboard.html", ctx=ctx, d=data, now=utcnow(),
                  is_admin=ctx.user.role == UserRole.SYSTEM_ADMIN)
