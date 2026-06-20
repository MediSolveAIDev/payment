"""목록 화면 공용 필터 옵션 빌더 + 구독 검색 쿼리 빌더.

subscription_query/SUB_SORT는 구독 목록·구독 엑셀·서비스 상세 구독 탭(+탭 엑셀)
네 곳이 공유한다(감사 Phase 4 — S2). 과거에는 같은 필터 로직이 세 곳에 복붙되어
검색 조건을 추가할 때 한 곳을 빠뜨리면 목록·엑셀·탭 결과가 어긋나는 버그가 생겼다.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.pagination import PageParams, date_range
from app.models import Plan, Service, Subscription

# 서비스 목록 정렬 가능 컬럼 맵 (PageParams.order_by에 전달)
SVC_SORT = {"name": Service.name, "status": Service.status,
            "created_at": Service.created_at}


def services_query(pp: PageParams):
    """서비스 목록·엑셀이 공유하는 검색/필터 쿼리 (감사 Phase 4 — S6 분리와 함께 이동).

    필터:
        q      — Service.name 또는 manager_email 부분 일치
        status — 정확 일치
    """
    base = select(Service)
    if pp.q:
        base = base.where(Service.name.ilike(f"%{pp.q}%")
                          | Service.manager_email.ilike(f"%{pp.q}%"))
    if pp.filters.get("status"):
        base = base.where(Service.status == pp.filters["status"])
    return base


# 구독 목록 정렬 가능 컬럼 맵 (PageParams.order_by에 전달)
SUB_SORT = {
    "external_user_id": Subscription.external_user_id,
    "status": Subscription.status,
    "current_period_end": Subscription.current_period_end,
    "next_billing_at": Subscription.next_billing_at,
    "created_at": Subscription.created_at,
}


def subscription_query(pp: PageParams, *, scope: list[uuid.UUID] | None = None,
                       service_id: uuid.UUID | None = None):
    """구독 검색/필터 쿼리 — select(Subscription, Plan, Service) 3-튜플 행을 반환.

    호출처별 사용:
        구독 목록/엑셀      — scope=service_scope(ctx) (SYSTEM_ADMIN이면 None=전체)
        서비스 상세 탭/엑셀 — service_id=<해당 서비스> (단일 서비스 고정)

    필터(pp.filters에 있는 키만 적용 — 호출처의 filter_keys가 사용 범위를 결정):
        q         — external_user_id 부분 일치
        status    — Subscription.status 정확 일치
        plan_name — Plan.name 정확 일치
        service_id — UUID 파싱 실패 시 pp.filters에서 제거해 링크 오염 방지
        from/to   — created_at 범위 (date_range는 end를 익일 0시 반개구간으로 변환)
    """
    base = (select(Subscription, Plan, Service)
            .join(Plan, Subscription.plan_id == Plan.id)
            .join(Service, Subscription.service_id == Service.id))
    if scope is not None:
        base = base.where(Subscription.service_id.in_(scope))
    if service_id is not None:
        base = base.where(Subscription.service_id == service_id)
    if pp.q:
        base = base.where(Subscription.external_user_id.ilike(f"%{pp.q}%"))
    if pp.filters.get("status"):
        base = base.where(Subscription.status == pp.filters["status"])
    if pp.filters.get("plan_name"):
        base = base.where(Plan.name == pp.filters["plan_name"])
    sid = pp.filters.get("service_id", "")
    if sid:
        try:
            base = base.where(Subscription.service_id == uuid.UUID(sid))
        except ValueError:
            pp.filters.pop("service_id", None)
    start, end = date_range(pp)
    if start:
        base = base.where(Subscription.created_at >= start)
    if end:
        base = base.where(Subscription.created_at < end)
    return base


async def service_options(db: AsyncSession, scope: list[uuid.UUID] | None,
                          *, include_all: bool = True) -> list[tuple[str, str]]:
    """서비스 드롭다운 옵션. 스코프 내, 이름순. include_all이면 맨 앞에 '전체 서비스'."""
    q = select(Service.id, Service.name).order_by(Service.name)
    if scope is not None:
        q = q.where(Service.id.in_(scope))
    opts = [(str(sid), name) for sid, name in (await db.execute(q)).all()]
    return ([("", "전체 서비스")] + opts) if include_all else opts


async def plan_name_options(db: AsyncSession, scope: list[uuid.UUID] | None,
                            service_filter: str) -> list[tuple[str, str]]:
    """요금제명 드롭다운 옵션. 스코프 내 distinct, 서비스 선택 시 그 서비스의 요금제만."""
    q = select(Plan.name).distinct().order_by(Plan.name)
    if scope is not None:
        q = q.where(Plan.service_id.in_(scope))
    if service_filter:
        try:
            q = q.where(Plan.service_id == uuid.UUID(service_filter))
        except ValueError:
            pass
    return [("", "전체 요금제")] + [(n, n) for n in (await db.scalars(q)).all()]
