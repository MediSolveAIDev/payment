"""admin 목록 공통 페이징/검색/정렬 헬퍼.

사용:
    pp = PageParams.from_request(request, sortable={"created_at", "status"},
                                 default_sort="created_at")
    page = await paginate(db, base_query, count_query, pp)
    # 템플릿에서 page.items / page.page / page.pages / page.has_prev ...
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

PER_PAGE_DEFAULT = 15


@dataclass
class PageParams:
    page: int = 1
    per_page: int = PER_PAGE_DEFAULT
    q: str = ""                       # 검색어
    sort: str = ""                    # 정렬 컬럼명
    direction: str = "desc"           # asc | desc
    filters: dict[str, str] = field(default_factory=dict)  # {필드: 값}
    page_param: str = "page"          # 페이지 쿼리 파라미터명(같은 화면 다중 페이저 분리용)

    @classmethod
    def from_request(cls, request: Request, *, sortable: set[str],
                     default_sort: str, default_dir: str = "desc",
                     filter_keys: tuple[str, ...] = (),
                     page_param: str = "page") -> "PageParams":
        qp = request.query_params

        def to_int(v: str | None, default: int) -> int:
            try:
                return max(1, int(v))
            except (TypeError, ValueError):
                return default

        sort = qp.get("sort", default_sort)
        if sort not in sortable:
            sort = default_sort
        direction = qp.get("dir", default_dir)
        if direction not in ("asc", "desc"):
            direction = default_dir
        filters = {k: qp.get(k, "").strip() for k in filter_keys if qp.get(k, "").strip()}
        return cls(page=to_int(qp.get(page_param), 1), q=qp.get("q", "").strip(),
                   sort=sort, direction=direction, filters=filters,
                   page_param=page_param)

    def order_by(self, columns: dict[str, Any]):
        """sortable 컬럼명 → SQLAlchemy 컬럼 매핑으로 정렬식 반환."""
        col = columns[self.sort]
        return col.asc() if self.direction == "asc" else col.desc()

    def toggled_dir(self, column: str) -> str:
        """현재 정렬 컬럼을 다시 누르면 방향 토글, 아니면 asc 시작."""
        if self.sort == column:
            return "asc" if self.direction == "desc" else "desc"
        return "asc"

    def query_without(self, *drop: str) -> str:
        """현재 파라미터를 유지하되 일부 키를 제외한 쿼리스트링(페이지 링크용)."""
        parts = {}
        if self.q:
            parts["q"] = self.q
        if self.sort:
            parts["sort"] = self.sort
            parts["dir"] = self.direction
        parts.update(self.filters)
        for d in drop:
            parts.pop(d, None)
        from urllib.parse import urlencode
        return urlencode(parts)


@dataclass
class Page:
    items: list
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        return max(1, (self.total + self.per_page - 1) // self.per_page)

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def start(self) -> int:
        return 0 if self.total == 0 else (self.page - 1) * self.per_page + 1

    @property
    def end(self) -> int:
        return min(self.page * self.per_page, self.total)

    def window(self, span: int = 2) -> list[int]:
        """현재 페이지 주변 페이지 번호(생략은 템플릿이 처리)."""
        lo = max(1, self.page - span)
        hi = min(self.pages, self.page + span)
        return list(range(lo, hi + 1))


async def paginate(db: AsyncSession, items_query, count_query=None,
                   pp: PageParams | None = None, *, flatten: bool = False) -> Page:
    """목록 쿼리를 페이징 실행한다.

    권장 호출(감사 Phase 4 — S3): ``paginate(db, items_q, pp)`` — count 쿼리를
    내부에서 생성(count_of)해, 과거 11곳에서 반복되던
    ``select(func.count()).select_from(base.order_by(None).subquery())``
    보일러플레이트와 order_by(None) 누락 실수를 없앤다.

    레거시 호출 ``paginate(db, items_q, count_q, pp)``도 그대로 동작한다
    (조인 없는 별도 count 등 직접 제어가 필요한 경우에만 사용).

    flatten=True면 단일 엔티티 select의 Row를 엔티티로 평탄화해 반환한다
    (과거의 ``page.items = [r[0] for r in page.items]`` 후처리 대체).
    """
    # 신형 호출(paginate(db, q, pp)): 3번째 인자가 PageParams면 count는 자동 생성
    if isinstance(count_query, PageParams) and pp is None:
        pp, count_query = count_query, None
    if count_query is None:
        count_query = count_of(items_query)
    total = int(await db.scalar(count_query) or 0)
    # 페이지 범위 보정
    pages = max(1, (total + pp.per_page - 1) // pp.per_page)
    page_no = min(pp.page, pages)
    offset = (page_no - 1) * pp.per_page
    rows = (await db.execute(items_query.offset(offset).limit(pp.per_page))).all()
    items = [r[0] for r in rows] if flatten else rows
    return Page(items=items, page=page_no, per_page=pp.per_page, total=total)


def count_of(subquery) -> Any:
    """select(...) 쿼리의 행 수를 세는 count 쿼리 생성."""
    return select(func.count()).select_from(subquery.order_by(None).subquery())


def date_range(pp: PageParams, from_key: str = "from",
               to_key: str = "to") -> tuple[datetime | None, datetime | None]:
    """pp.filters의 YYYY-MM-DD 쌍 → (start, end) UTC. end는 익일 0시(반개구간).
    한쪽만 입력 허용. 형식 오류 키는 무시 + pp.filters에서 제거(링크 오염 방지)."""
    def parse(key: str) -> datetime | None:
        raw = pp.filters.get(key, "")
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pp.filters.pop(key, None)
            return None

    start = parse(from_key)
    end = parse(to_key)
    return start, (end + timedelta(days=1)) if end else None
