"""대시보드 집계 — SnowUI 차트용 데이터 v2 (요청 010).

모든 쿼리는 service_scope(None=SYSTEM_ADMIN 전체, UUID=해당 서비스)로 제한된다.
금액은 KRW 정수, 시간은 UTC 기준.
"""

import uuid
from dataclasses import dataclass, field

from dateutil.relativedelta import relativedelta
from sqlalchemy import String, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.models import (
    AuditLog,
    Payment,
    PaymentKind,
    PaymentStatus,
    Service,
    Subscription,
    SubscriptionStatus,
)

_STATUS_ORDER = [
    SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE, SubscriptionStatus.SUSPENDED,
    SubscriptionStatus.CANCELED, SubscriptionStatus.EXTENDED,
    SubscriptionStatus.EXPIRED,
]


@dataclass
class StatCard:
    label: str
    value: str
    delta: str        # 예: "+12.5%"
    up: bool
    tint: int         # 1~4
    href: str | None = None   # 클릭 시 이동(없으면 비링크)


@dataclass
class DashboardData:
    revenue_cards: list[StatCard] = field(default_factory=list)   # 총/구독/일반/환불
    service_revenue: list[dict] = field(default_factory=list)     # admin: {id,name,total,sub,one_off,refund}
    subs_months: list[dict] = field(default_factory=list)         # [{label, total(전체), new(신규)}]
    one_off_months: list[dict] = field(default_factory=list)      # [{label, value}]
    sub_flow: list[dict] = field(default_factory=list)            # 도넛 옆 흐름 지표 [{label,value,href}]
    status_breakdown: list[dict] = field(default_factory=list)    # 도넛(전체 상태, 만료 포함)
    daily_trend: list[dict] = field(default_factory=list)         # [{label,total,new,canceled,expired}] 30일
    service_subs: list[dict] = field(default_factory=list)        # admin: {id,name,open,new,canceled,expired,revenue}
    recent_subs: list = field(default_factory=list)               # 최근 구독(요청 015 1.1.1)
    recent: list = field(default_factory=list)                    # 최근 결제(트라이얼/0원 포함; dict 목록)
    past_due: list = field(default_factory=list)
    expiring: list = field(default_factory=list)


# 구독상태 도넛 팔레트(요청 015: 전체 색 교체) — 상태별로 뚜렷이 구분되는 새 조합
_STATUS_COLOR = {
    "TRIAL": "var(--accent-purple)",
    "ACTIVE": "var(--accent-blue)",
    "PAST_DUE": "var(--accent-yellow)",
    "SUSPENDED": "var(--accent-orange)",
    "CANCELED": "var(--accent-cyan)",
    "EXTENDED": "var(--accent-mint)",
    "EXPIRED": "var(--accent-red)",
}
_STATUS_KO = {"TRIAL": "체험", "ACTIVE": "활성", "PAST_DUE": "미수",
              "SUSPENDED": "정지", "CANCELED": "취소", "EXTENDED": "연장처리",
              "EXPIRED": "만료"}
# 최근 결제/구독 레일에 쓰는 결제·구독 상태 한글 라벨
_RECENT_STATUS_KO = {"DONE": "완료", "FAILED": "실패", "PENDING": "대기",
                     "CANCELED": "취소", "TRIAL": "체험"}

# '열린' 구독 상태 — CANCELED는 기간 내일 때만 열린 것으로 본다
_OPEN_STATUSES = (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE,
                  SubscriptionStatus.PAST_DUE, SubscriptionStatus.SUSPENDED,
                  SubscriptionStatus.EXTENDED)
_USER_CANCEL_ACTIONS = ("subscription.cancel", "subscription.force_cancel")
_PAYMENT_EXPIRE_ACTIONS = ("subscription.suspended",)
_EXPIRE_ACTIONS = ("subscription.expired",)


def _scoped(query, scope: list[uuid.UUID] | None, col):
    return query.where(col.in_(scope)) if scope is not None else query


def _won(amount: int) -> str:
    return f"{amount:,}원"


def _open_subs_cond(at):
    """at 시점에 '열려 있는' 구독 조건 (CANCELED는 기간 내만)."""
    return (Subscription.status.in_(_OPEN_STATUSES)
            | ((Subscription.status == SubscriptionStatus.CANCELED)
               & (Subscription.current_period_end > at)))


async def _count(db, scope, *where) -> int:
    q = select(func.count()).select_from(Subscription).where(*where)
    return int(await db.scalar(_scoped(q, scope, Subscription.service_id)) or 0)


async def _audit_count(db, scope, actions, start, end) -> int:
    """감사 액션 건수. 스코프는 target 구독의 서비스로 제한."""
    q = (select(func.count()).select_from(AuditLog)
         .where(AuditLog.action.in_(actions),
                AuditLog.created_at >= start, AuditLog.created_at < end))
    if scope is not None:
        sub_sq = select(cast(Subscription.id, String)).where(
            Subscription.service_id.in_(scope))
        q = q.where(AuditLog.target_id.in_(sub_sq))
    return int(await db.scalar(q) or 0)


# 매출에 잡히는 결제 상태 — 승인 완료(DONE) + 취소(CANCELED).
# 취소된 일반결제는 환불하지 않고 보유한 "취소 수수료"만 매출로 반영한다(요청).
_REVENUE_STATUSES = (PaymentStatus.DONE, PaymentStatus.CANCELED)


def _revenue_expr():
    """결제 1건이 매출에 기여하는 금액(순매출) 식.

    - DONE: amount − 누적 환불액(coalesce(canceled_amount,0)).
      어드민 부분취소는 status=DONE을 유지하므로 DONE에서도 환불액을 차감해야
      순매출이 정확하다(부분취소 반영).
    - CANCELED: amount − 실제 환불액(coalesce(canceled_amount, amount)).
      사용자 수수료 취소는 보유 수수료(amount−환불)만, 전액취소는 0.
    - 그 외: 0.
    정산(settlement)의 순매출(총매출−환불)과 동일한 금액이 된다.
    """
    return case(
        (Payment.status == PaymentStatus.DONE,
         Payment.amount - func.coalesce(Payment.canceled_amount, 0)),
        (Payment.status == PaymentStatus.CANCELED,
         Payment.amount - func.coalesce(Payment.canceled_amount, Payment.amount)),
        else_=0)


async def _revenue_between(db, scope, start, end, *, kind=None) -> int:
    """[start, end) 구간 매출 합계(approved_at 기준). kind 미지정 시 전체 종류.

    DONE은 전액, CANCELED(취소)는 보유한 취소 수수료만 매출로 잡는다 — 일반결제 취소 시
    수수료가 매출에 반영되도록 한다(요청). 취소 건의 매출 인식 시점은 원결제 승인일(approved_at).
    """
    q = select(func.coalesce(func.sum(_revenue_expr()), 0)).where(
        Payment.status.in_(_REVENUE_STATUSES),
        Payment.approved_at >= start, Payment.approved_at < end)
    if kind is not None:
        q = q.where(Payment.kind == kind)
    return int(await db.scalar(_scoped(q, scope, Payment.service_id)) or 0)


async def _refund_between(db, scope, start, end) -> int:
    """[start, end) 구간 환불 금액 합계.

    어드민 부분취소는 status=DONE을 유지하므로 CANCELED만으로는 부분환불을 놓친다.
    따라서 DONE·CANCELED 모두에서 실제 환불액을 집계한다:
      - DONE: coalesce(canceled_amount, 0) — 부분환불액(없으면 0).
      - CANCELED: coalesce(canceled_amount, amount) — 환불액(없으면 전액).
    환불 기준일은 기존과 동일하게 requested_at을 사용한다.
    """
    refund_expr = case(
        (Payment.status == PaymentStatus.DONE,
         func.coalesce(Payment.canceled_amount, 0)),
        (Payment.status == PaymentStatus.CANCELED,
         func.coalesce(Payment.canceled_amount, Payment.amount)),
        else_=0)
    q = select(func.coalesce(func.sum(refund_expr), 0)).where(
        Payment.status.in_(_REVENUE_STATUSES),
        Payment.requested_at >= start, Payment.requested_at < end)
    return int(await db.scalar(_scoped(q, scope, Payment.service_id)) or 0)


async def _payment_count_between(db, scope, status, start, end) -> int:
    """[start, end) 구간 지정 상태의 결제 건수. 주로 FAILED 미결제 집계에 사용."""
    q = select(func.count()).select_from(Payment).where(
        Payment.status == status,
        Payment.requested_at >= start, Payment.requested_at < end)
    return int(await db.scalar(_scoped(q, scope, Payment.service_id)) or 0)


async def _revenue_cards(db, scope, now, month_start) -> list[StatCard]:
    """이번 달 총매출·구독매출·일반매출·환불금액 StatCard 4개를 반환한다.

    환불금액은 Payment.status=CANCELED 행의
    coalesce(canceled_amount, amount) — 부분환불 반영 합산이며,
    환불이 0원이면 up=True(긍정 색상)로 표시한다.
    """
    end = now + relativedelta(seconds=1)
    # end에 1초를 더해 now 시각을 반개구간 [month_start, end)에 포함시킨다
    qs = f"from={month_start.strftime('%Y-%m-%d')}&to={now.strftime('%Y-%m-%d')}"
    total = await _revenue_between(db, scope, month_start, end)
    sub = await _revenue_between(db, scope, month_start, end, kind=PaymentKind.SUBSCRIPTION)
    one = await _revenue_between(db, scope, month_start, end, kind=PaymentKind.ONE_OFF)
    refund = await _refund_between(db, scope, month_start, end)
    return [
        StatCard("총매출", _won(total), "이번 달", True, 3, f"/admin/payments?status=DONE&{qs}"),
        StatCard("구독매출", _won(sub), "이번 달", True, 1,
                 f"/admin/payments?status=DONE&kind=SUBSCRIPTION&{qs}"),
        StatCard("일반매출", _won(one), "이번 달", True, 2,
                 f"/admin/payments?status=DONE&kind=ONE_OFF&{qs}"),
        StatCard("환불금액", _won(refund), "이번 달", refund == 0, 4,
                 f"/admin/payments?status=CANCELED&{qs}"),
    ]


async def _sub_flow(db, scope, now, month_start) -> list[dict]:
    """도넛 옆에 함께 표시하는 이번 달 흐름 지표(신규/취소/만료/미결제). 각 항목 클릭→상세."""
    end = now + relativedelta(seconds=1)
    qs = f"from={month_start.strftime('%Y-%m-%d')}&to={now.strftime('%Y-%m-%d')}"
    this_new = await _count(db, scope, Subscription.created_at >= month_start,
                            Subscription.created_at < end)
    # uc: 사용자 직접 취소, pe: 결제 실패로 인한 강제 취소(suspended) — 둘 다 "취소" 버킷
    uc = await _audit_count(db, scope, _USER_CANCEL_ACTIONS, month_start, end)
    pe = await _audit_count(db, scope, _PAYMENT_EXPIRE_ACTIONS, month_start, end)
    # expired: 만료 기준은 subscription.expired 감사 액션 발생 건수
    expired = await _audit_count(db, scope, _EXPIRE_ACTIONS, month_start, end)
    failed = await _payment_count_between(db, scope, PaymentStatus.FAILED, month_start, end)
    return [
        {"label": "신규 구독", "value": this_new,
         "href": "/admin/subscriptions?sort=created_at&dir=desc"},
        {"label": "구독 취소", "value": uc + pe,
         "href": "/admin/subscriptions?status=CANCELED"},
        {"label": "구독 만료", "value": expired,
         "href": "/admin/subscriptions?status=EXPIRED"},
        {"label": "미결제", "value": failed, "href": f"/admin/payments?status=FAILED&{qs}"},
    ]


async def _open_new_counts(db, scope, buckets, now) -> list[tuple[int, int]]:
    """버킷별 (열린 구독 수[버킷 끝 스냅샷], 신규 구독 수) — 단일 쿼리 집계.

    버킷 수×2개의 count(*) FILTER (WHERE ...) 컬럼을 만들어 구독 테이블을
    **1회만 스캔**한다(감사 Phase 3 — 성능 H3). 과거 구현은 스코프 구독 전체를
    메모리에 적재한 뒤 버킷×N 파이썬 루프(12개월+30일 = 42×N)를 돌아,
    구독이 늘수록 대시보드 로드가 이벤트 루프를 CPU로 점유했다.

    '열린' 판정은 _open_subs_cond와 동일 규칙:
    created_at <= at AND (status in _OPEN_STATUSES OR (CANCELED AND period_end > at))
    스냅샷 시점 at은 min(버킷 끝, now) — 미래 버킷은 현재 시점으로 클램프.
    """
    cols = []
    for i, (start, end) in enumerate(buckets):
        at = min(end, now)
        cols.append(func.count().filter(
            (Subscription.created_at <= at) & _open_subs_cond(at)).label(f"open{i}"))
        cols.append(func.count().filter(
            (Subscription.created_at >= start)
            & (Subscription.created_at < end)).label(f"new{i}"))
    q = select(*cols).select_from(Subscription)
    row = (await db.execute(_scoped(q, scope, Subscription.service_id))).one()
    return [(int(row[2 * i] or 0), int(row[2 * i + 1] or 0))
            for i in range(len(buckets))]


async def _oneoff_sums(db, scope, buckets) -> list[int]:
    """버킷별 ONE_OFF 매출 합계 — sum FILTER 단일 쿼리(테이블 1회 스캔).

    DONE 전액 + CANCELED 취소 수수료(_revenue_expr) — 취소 수수료도 일반매출로 잡는다.
    """
    cols = [func.coalesce(func.sum(_revenue_expr()).filter(
                (Payment.approved_at >= start) & (Payment.approved_at < end)), 0
            ).label(f"v{i}")
            for i, (start, end) in enumerate(buckets)]
    q = (select(*cols).select_from(Payment)
         .where(Payment.status.in_(_REVENUE_STATUSES),
                Payment.kind == PaymentKind.ONE_OFF,
                # 전체 범위로 1차 제한 — (service_id, approved_at) 인덱스 활용
                Payment.approved_at >= buckets[0][0],
                Payment.approved_at < buckets[-1][1]))
    row = (await db.execute(_scoped(q, scope, Payment.service_id))).one()
    return [int(v or 0) for v in row]


async def _audit_counts(db, scope, actions, buckets) -> list[int]:
    """버킷별 감사 액션 건수 — count FILTER 단일 쿼리. 스코프는 target 구독의 서비스로 제한."""
    cols = [func.count().filter(
                (AuditLog.created_at >= start) & (AuditLog.created_at < end)
            ).label(f"c{i}")
            for i, (start, end) in enumerate(buckets)]
    q = (select(*cols).select_from(AuditLog)
         .where(AuditLog.action.in_(actions),
                AuditLog.created_at >= buckets[0][0],
                AuditLog.created_at < buckets[-1][1]))
    if scope is not None:
        sub_sq = select(cast(Subscription.id, String)).where(
            Subscription.service_id.in_(scope))
        q = q.where(AuditLog.target_id.in_(sub_sq))
    row = (await db.execute(q)).one()
    return [int(v or 0) for v in row]


async def _series_12m(db, scope, now, month_start) -> tuple[list, list]:
    """(구독수[전체/신규], 일반매출) 12개월 — FILTER 집계 쿼리 2회(구독 1회 + 결제 1회)."""
    buckets = []
    for i in range(11, -1, -1):
        start = month_start - relativedelta(months=i)
        buckets.append((start, start + relativedelta(months=1)))
    counts = await _open_new_counts(db, scope, buckets, now)
    oneoff = await _oneoff_sums(db, scope, buckets)
    subs = [{"label": f"{start.month}월", "total": total, "new": new_n}
            for (start, _end), (total, new_n) in zip(buckets, counts)]
    one_off = [{"label": f"{start.month}월", "value": rev}
               for (start, _end), rev in zip(buckets, oneoff)]
    return subs, one_off


async def _daily_trend(db, scope, now) -> list[dict]:
    """최근 30일 일별 — 전체구독(일말 스냅샷)/신규/취소/만료. FILTER 집계 쿼리 3회."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    buckets = []
    for i in range(29, -1, -1):
        day = today - relativedelta(days=i)
        buckets.append((day, day + relativedelta(days=1)))
    counts = await _open_new_counts(db, scope, buckets, now)
    canceled = await _audit_counts(
        db, scope, _USER_CANCEL_ACTIONS + _PAYMENT_EXPIRE_ACTIONS, buckets)
    expired = await _audit_counts(db, scope, _EXPIRE_ACTIONS, buckets)
    return [{"label": f"{day.month}/{day.day}", "raw_date": day.strftime("%Y-%m-%d"), "total": total, "new": new_n,
             "canceled": c, "expired": e}
            for (day, _nxt), (total, new_n), c, e
            in zip(buckets, counts, canceled, expired)]


async def _service_revenue(db, now, month_start) -> list[dict]:
    """서비스별 이번달 매출(총/구독/일반/환불) — SYSTEM_ADMIN 전용."""
    end = now + relativedelta(seconds=1)

    def _sum(*conds):
        return (select(func.coalesce(func.sum(Payment.amount), 0))
                .where(Payment.service_id == Service.id, *conds)
                .correlate(Service).scalar_subquery())

    # 매출(취소 수수료 포함) 합 — DONE 전액 + CANCELED 취소 수수료(_revenue_expr)
    rev_m = (Payment.status.in_(_REVENUE_STATUSES),
             Payment.approved_at >= month_start, Payment.approved_at < end)

    def _rev_sum(*conds):
        return (select(func.coalesce(func.sum(_revenue_expr()), 0))
                .where(Payment.service_id == Service.id, *rev_m, *conds)
                .correlate(Service).scalar_subquery())

    done_m = (Payment.status == PaymentStatus.DONE,
              Payment.approved_at >= month_start, Payment.approved_at < end)
    total = _rev_sum()
    sub = _sum(*done_m, Payment.kind == PaymentKind.SUBSCRIPTION)  # 구독은 취소 없음 → DONE만
    one = _rev_sum(Payment.kind == PaymentKind.ONE_OFF)
    # 환불액 — DONE 부분환불(어드민 부분취소)과 CANCELED 환불을 모두 합산.
    # DONE: coalesce(canceled_amount,0), CANCELED: coalesce(canceled_amount, amount).
    refund_expr = case(
        (Payment.status == PaymentStatus.DONE,
         func.coalesce(Payment.canceled_amount, 0)),
        (Payment.status == PaymentStatus.CANCELED,
         func.coalesce(Payment.canceled_amount, Payment.amount)),
        else_=0)
    refund = (select(func.coalesce(func.sum(refund_expr), 0))
              .where(Payment.service_id == Service.id,
                     Payment.status.in_(_REVENUE_STATUSES),
                     Payment.requested_at >= month_start, Payment.requested_at < end)
              .correlate(Service).scalar_subquery())
    rows = (await db.execute(
        select(Service.id, Service.name, total, sub, one, refund)
        .order_by(total.desc(), Service.name))).all()
    return [{"id": sid, "name": name, "total": int(t), "sub": int(s),
             "one_off": int(o), "refund": int(r)} for sid, name, t, s, o, r in rows]


async def _service_subs(db, now, month_start) -> list[dict]:
    """서비스별 구독정보(현재/신규/취소/만료/구독매출) — SYSTEM_ADMIN 전용."""
    end = now + relativedelta(seconds=1)
    open_n = (select(func.count()).select_from(Subscription)
              .where(Subscription.service_id == Service.id, _open_subs_cond(now))
              .correlate(Service).scalar_subquery())
    new_n = (select(func.count()).select_from(Subscription)
             .where(Subscription.service_id == Service.id,
                    Subscription.created_at >= month_start, Subscription.created_at < end)
             .correlate(Service).scalar_subquery())

    def _audit_sub(actions):
        sub_ids = select(cast(Subscription.id, String)).where(
            Subscription.service_id == Service.id)
        return (select(func.count()).select_from(AuditLog)
                .where(AuditLog.action.in_(actions),
                       AuditLog.created_at >= month_start, AuditLog.created_at < end,
                       AuditLog.target_id.in_(sub_ids))
                .correlate(Service).scalar_subquery())
    canceled = _audit_sub(_USER_CANCEL_ACTIONS + _PAYMENT_EXPIRE_ACTIONS)
    expired = _audit_sub(_EXPIRE_ACTIONS)
    revenue = (select(func.coalesce(func.sum(Payment.amount), 0))
               .where(Payment.service_id == Service.id,
                      Payment.kind == PaymentKind.SUBSCRIPTION,
                      Payment.status == PaymentStatus.DONE,
                      Payment.approved_at >= month_start, Payment.approved_at < end)
               .correlate(Service).scalar_subquery())
    rows = (await db.execute(
        select(Service.id, Service.name, open_n, new_n, canceled, expired, revenue)
        .order_by(open_n.desc(), Service.name))).all()
    return [{"id": sid, "name": name, "open": int(o), "new": int(n),
             "canceled": int(c), "expired": int(e), "revenue": int(rv)}
            for sid, name, o, n, c, e, rv in rows]


async def _rails(db, scope, now) -> tuple[list, list, list, list]:
    """(최근 구독, 최근 결제, 미수 구독, 만료 임박) 우측 레일.

    최근 결제(요청 015 1.1.2): 실제 Payment + 트라이얼/0원 첫결제 구독(Payment 미생성)을
    한 목록에 0원으로 합쳐 시간순 상위 8건으로 반환한다. 통일 위해 dict 목록으로 만든다:
      {sub_id, external_user_id, amount, status, status_ko, when}
    """
    # 1) 실제 결제 — 시간(requested_at)·금액·결제상태
    recent_pay_q = (select(Payment, Subscription)
                    .join(Subscription, Payment.subscription_id == Subscription.id)
                    .order_by(Payment.requested_at.desc()).limit(8))
    pay_rows = (await db.execute(
        _scoped(recent_pay_q, scope, Subscription.service_id))).all()
    recent = [{"sub_id": sub.id, "external_user_id": sub.external_user_id,
               "amount": p.amount, "status": p.status,
               "status_ko": _RECENT_STATUS_KO.get(p.status, p.status),
               "when": p.requested_at}
              for p, sub in pay_rows]
    # 2) 트라이얼/0원 첫결제 구독 — Payment row가 없는 구독(첫결제 금액 0)을 0원으로 포함
    no_payment = ~select(Payment.id).where(
        Payment.subscription_id == Subscription.id).exists()
    trial_q = (select(Subscription).where(no_payment)
               .order_by(Subscription.created_at.desc()).limit(8))
    trial_subs = (await db.scalars(
        _scoped(trial_q, scope, Subscription.service_id))).all()
    for s in trial_subs:
        # 체험은 'TRIAL', 그 외(FREE/100%할인 첫결제)는 결제 완료로 간주해 '완료' 표시
        disp = "TRIAL" if s.status == SubscriptionStatus.TRIAL else "DONE"
        recent.append({"sub_id": s.id, "external_user_id": s.external_user_id,
                       "amount": 0, "status": disp,
                       "status_ko": _RECENT_STATUS_KO.get(disp, disp),
                       "when": s.created_at})
    # 시간순 정렬 후 상위 8건
    recent.sort(key=lambda r: r["when"], reverse=True)
    recent = recent[:8]

    # 최근 구독(요청 015 1.1.1) — 생성 최신순 상위 8건
    recent_subs_q = (select(Subscription)
                     .order_by(Subscription.created_at.desc()).limit(8))
    recent_subs = list((await db.scalars(
        _scoped(recent_subs_q, scope, Subscription.service_id))).all())

    pd_q = (select(Subscription)
            .where(Subscription.status.in_((SubscriptionStatus.PAST_DUE,
                                            SubscriptionStatus.SUSPENDED)))
            .order_by(Subscription.next_billing_at.asc().nullslast()).limit(5))
    past_due = list((await db.scalars(
        _scoped(pd_q, scope, Subscription.service_id))).all())

    # 만료 임박 — EXPIRED만 제외(기간 조건이 이미 미래 7일로 제한)
    exp_q = (select(Subscription)
             .where(Subscription.status.in_((*_OPEN_STATUSES,
                                             SubscriptionStatus.CANCELED)),
                    Subscription.current_period_end >= now,
                    Subscription.current_period_end < now + relativedelta(days=7))
             .order_by(Subscription.current_period_end.asc()).limit(5))
    expiring = list((await db.scalars(
        _scoped(exp_q, scope, Subscription.service_id))).all())
    return recent_subs, recent, past_due, expiring


async def build_dashboard(db: AsyncSession, scope: list[uuid.UUID] | None) -> DashboardData:
    """대시보드 전체 데이터를 조립해 DashboardData로 반환한다.

    3개 섹션:
    1. 요약 카드 + 흐름 지표: revenue_cards, sub_flow (이번 달 집계)
    2. 차트 시리즈: subs_months, one_off_months (12개월), daily_trend (30일),
       status_breakdown (도넛 — 만료 포함)
    3. 레일(우측 패널): recent(최근 결제), past_due(미수/정지), expiring(만료 임박)

    scope=None → SYSTEM_ADMIN 전체 조회, service_revenue·service_subs도 함께 반환.
    scope=[...] → 해당 서비스(들)만 조회, 서비스별 테이블은 생략.

    12개월/30일 시리즈는 count/sum FILTER 단일 스캔 쿼리로 DB에서 집계한다
    (감사 Phase 3 — 성능 H3; 과거의 구독 전체 메모리 적재 + 42×N 루프 제거).
    """
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    data = DashboardData()
    # 전체 상태별 집계 — 도넛에 만료(EXPIRED) 포함(요청 011)
    counts = {s: 0 for s in _STATUS_ORDER}
    rows = (await db.execute(_scoped(
        select(Subscription.status, func.count()).group_by(Subscription.status),
        scope, Subscription.service_id))).all()
    for status, n in rows:
        counts[status] = n
    data.revenue_cards = await _revenue_cards(db, scope, now, month_start)
    data.sub_flow = await _sub_flow(db, scope, now, month_start)
    data.subs_months, data.one_off_months = await _series_12m(db, scope, now, month_start)
    data.daily_trend = await _daily_trend(db, scope, now)
    data.status_breakdown = [
        {"label": _STATUS_KO[s], "value": counts[s], "color": _STATUS_COLOR[s],
         "href": f"/admin/subscriptions?status={s}"}
        for s in _STATUS_ORDER if counts[s] > 0
    ] or [{"label": "데이터 없음", "value": 1, "color": "var(--black-10)", "href": None}]
    # 서비스별 테이블은 SYSTEM_ADMIN 전용(scope=None)
    if scope is None:
        data.service_revenue = await _service_revenue(db, now, month_start)
        data.service_subs = await _service_subs(db, now, month_start)
    data.recent_subs, data.recent, data.past_due, data.expiring = await _rails(db, scope, now)
    return data
