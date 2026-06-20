"""서비스 목록 조회 — 테스트/도구가 서비스를 식별·선택할 수 있도록 id·이름만 제공.

인증 없음(키 입력 전 단계에서 호출). 키/시크릿·구독 등 민감정보는 절대 포함하지 않는다.
운영에서 사내 서비스 구성 노출이 우려되면 PUBLIC_SERVICE_LIST_ENABLED=false로 끌 수
있다(감사 Phase 2 — 보안 L-1; 끄면 404 반환).
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_settings
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.models import Service
from app.schemas.api import ServiceListResponse

router = APIRouter()


@router.get(
    "/services",
    response_model=ServiceListResponse,
    summary="서비스 목록 조회 (무인증)",
    responses={200: {"description": "서비스 목록"}},
)
async def list_services(db: AsyncSession = Depends(get_db),
                        settings: Settings = Depends(get_settings)):
    """등록된 서비스의 id·이름·상태 목록(이름 오름차순 정렬). 민감정보 미포함.

    **인증 불필요** — API 키 입력 전 단계에서 서비스를 식별·선택하기 위한 용도.
    public_service_list_enabled=False면 404 — 존재 자체를 비노출(보안 L-1).
    """
    if not settings.public_service_list_enabled:
        raise NotFoundError("Not Found")
    rows = await db.scalars(select(Service).order_by(Service.name))
    return {"services": [{"id": str(s.id), "name": s.name, "status": s.status}
                         for s in rows.all()]}
