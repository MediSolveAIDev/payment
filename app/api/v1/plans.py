"""요금제 라우터 — 외부 서비스에 공개할 활성 요금제 목록 조회 엔드포인트."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import authenticate_service, get_db
from app.api.openapi import AUTH_RESPONSES
from app.models import Service
from app.schemas.api import PlanListResponse, PlanResponse
from app.services.plans import list_plans

router = APIRouter()


@router.get(
    "/plans",
    response_model=PlanListResponse,
    summary="활성 요금제 목록 조회",
    responses={200: {"description": "활성 요금제 목록"}, **AUTH_RESPONSES},
)
async def get_plans(service: Service = Depends(authenticate_service),
                    db: AsyncSession = Depends(get_db)):
    """인증된 서비스에 속한 활성(status=ACTIVE) 요금제 목록을 반환한다.

    **인증 필요**(HMAC 4개 헤더). only_active=True: ACTIVE 상태가 아닌 요금제는
    외부 서비스에 노출하지 않는다. 비활성 요금제가 보이면 외부 서비스가
    이미 판매 종료된 요금제로 신규 구독을 요청할 수 있기 때문이다.
    """
    plans = await list_plans(db, service_id=service.id, only_active=True)
    return {"plans": [PlanResponse.from_model(p) for p in plans]}
