# 대시보드 재구성 v2 구현 계획 (요청 010)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대시보드를 매출/12개월차트/구독 3섹션으로 재구성(환불금액·만료수·서비스별 매출/구독 추가)하고, 결제이력 필터 순서 변경 + 서비스 상세에 일반결제 표를 추가한다.

**Architecture:** `dashboard.py` 집계를 v2 구조(revenue_cards/sub_cards/service_revenue/service_subs/subs_months/one_off_months/daily_trend)로 재작성. 환불=CANCELED 결제 합(requested_at), 만료수=감사 `subscription.expired` 건수(취소수와 동일 패턴). 모델/마이그레이션 없음. 기존 SVG 매크로 재사용 + multiline 1개 추가.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Jinja2 SVG 차트, pytest

**스펙:** `docs/superpowers/specs/2026-06-08-dashboard-v2-design.md`
**테스트 실행:** `uv run pytest <경로> -q`

## 파일 구조
- `app/services/dashboard.py` — 집계 v2 재작성 (Task 1)
- `app/admin/templates/_charts.html` — `multiline` 매크로 추가 (Task 2)
- `app/admin/templates/dashboard.html` — 3섹션 재구성 (Task 2)
- `app/admin/routes/subscriptions.py` `payments_list` / `payments/list.html` — 필터 순서 (Task 3)
- `app/admin/routes/services.py` `services_detail` + `services/_oneoff_table.html`(신설) + `detail.html` (Task 4)
- 테스트: `tests/integration/test_dashboard.py`(재작성), `tests/e2e/test_dashboard_page.py`(재작성),
  `tests/e2e/test_admin_operations.py`(필터 순서), `tests/e2e/test_service_detail_page.py`(일반결제)

---

### Task 1: dashboard.py 집계 v2 재작성

**Files:**
- Modify: `app/services/dashboard.py`
- Test: `tests/integration/test_dashboard.py` (재작성)

- [ ] **Step 1: 통합 테스트 재작성** — `tests/integration/test_dashboard.py` 전체를 다음으로 교체:

```python
"""대시보드 v2 집계 통합 테스트 (요청 010)."""
from datetime import datetime, timedelta, timezone

from app.core.clock import utcnow
from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
from app.services.audit import record_audit
from app.services.dashboard import build_dashboard
from tests.factories import create_plan, create_service, create_subscription

UTC = timezone.utc


async def _pay(db, *, svc, amount, status="DONE", kind=PaymentKind.SUBSCRIPTION,
               sub=None, order, approved=None, requested=None):
    now = requested or utcnow()
    db.add(Payment(
        subscription_id=(sub.id if sub else None), service_id=svc.id,
        external_user_id=(sub.external_user_id if sub else "oo"),
        order_id=order, amount=amount,
        payment_type=(PaymentType.RENEWAL if kind == PaymentKind.SUBSCRIPTION else PaymentType.ONE_OFF),
        kind=kind, status=status, idempotency_key=order,
        requested_at=now, approved_at=(approved or now if status == "DONE" else None)))
    await db.commit()


def _card(data, label):
    for c in data.revenue_cards + data.sub_cards:
        if c.label == label:
            return c
    raise AssertionError(f"카드 없음: {label}")


async def test_revenue_cards_total_sub_oneoff_refund(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u", status="ACTIVE")
    await _pay(db, svc=svc, sub=sub, amount=10000, kind=PaymentKind.SUBSCRIPTION, order="r-sub")
    await _pay(db, svc=svc, amount=4000, kind=PaymentKind.ONE_OFF, order="r-oo")
    await _pay(db, svc=svc, sub=sub, amount=3000, status="CANCELED", order="r-refund")  # 환불
    data = await build_dashboard(db, None)
    assert _card(data, "총매출").value == "14,000원"      # 구독10k+일반4k (CANCELED 제외)
    assert _card(data, "구독매출").value == "10,000원"
    assert _card(data, "일반매출").value == "4,000원"
    assert _card(data, "환불금액").value == "3,000원"      # CANCELED 합


async def test_sub_cards_counts_and_expired_from_audit(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    s1 = await create_subscription(db, cipher, svc, plan, external_user_id="c1")
    s2 = await create_subscription(db, cipher, svc, plan, external_user_id="c2")
    s3 = await create_subscription(db, cipher, svc, plan, external_user_id="e1")
    await record_audit(db, actor_type="SERVICE", action="subscription.cancel",
                       target_type="subscription", target_id=str(s1.id))
    await record_audit(db, actor_type="SYSTEM", action="subscription.suspended",
                       target_type="subscription", target_id=str(s2.id))
    await record_audit(db, actor_type="SYSTEM", action="subscription.expired",
                       target_type="subscription", target_id=str(s3.id))
    await db.commit()
    data = await build_dashboard(db, None)
    assert _card(data, "구독 취소").value == "2"          # 사용자취소1 + 결제만료1
    assert _card(data, "사용자취소").value == "1"
    assert _card(data, "구독만료").value == "1"            # 감사 subscription.expired
    assert _card(data, "전체 구독").value == "3"           # 열린 구독(감사만 추가, 상태 미변경)


async def test_twelve_month_series_subs_and_one_off(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u", status="ACTIVE")
    await _pay(db, svc=svc, amount=4000, kind=PaymentKind.ONE_OFF, order="m-oo")
    data = await build_dashboard(db, None)
    assert len(data.subs_months) == 12
    assert data.subs_months[-1]["done"] == 1      # 전체구독수(이번달)
    assert data.subs_months[-1]["failed"] == 1    # 신규구독수(이번달)
    assert len(data.one_off_months) == 12
    assert data.one_off_months[-1]["value"] == 4000


async def test_daily_trend_30_days(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="d1", status="ACTIVE")
    data = await build_dashboard(db, None)
    assert len(data.daily_trend) == 30
    last = data.daily_trend[-1]
    assert set(last) >= {"label", "total", "new", "canceled", "expired"}
    assert last["total"] == 1 and last["new"] == 1


async def test_service_revenue_and_subs_admin_only(db, cipher):
    svc, _, _ = await create_service(db, cipher, name="서비스X")
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u", status="ACTIVE")
    await _pay(db, svc=svc, sub=sub, amount=10000, kind=PaymentKind.SUBSCRIPTION, order="sv-sub")
    await _pay(db, svc=svc, amount=2000, kind=PaymentKind.ONE_OFF, order="sv-oo")
    data = await build_dashboard(db, None)
    rev = next(r for r in data.service_revenue if r["name"] == "서비스X")
    assert rev["total"] == 12000 and rev["sub"] == 10000 and rev["one_off"] == 2000
    subs = next(r for r in data.service_subs if r["name"] == "서비스X")
    assert subs["open"] == 1 and subs["new"] == 1 and subs["revenue"] == 10000
    # 매니저 스코프: 서비스별 표 없음
    scoped = await build_dashboard(db, [svc.id])
    assert scoped.service_revenue == [] and scoped.service_subs == []
    assert _card(scoped, "구독매출").value == "10,000원"   # 카드는 스코프 집계
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_dashboard.py -x -q`
Expected: FAIL (revenue_cards/sub_cards/one_off_months/daily_trend/service_revenue 없음)

- [ ] **Step 3: dashboard.py 재작성**

(a) `DashboardData`를 교체:
```python
@dataclass
class DashboardData:
    revenue_cards: list[StatCard] = field(default_factory=list)   # 총/구독/일반/환불
    service_revenue: list[dict] = field(default_factory=list)     # admin: {id,name,total,sub,one_off,refund}
    subs_months: list[dict] = field(default_factory=list)         # [{label, done(전체), failed(신규)}]
    one_off_months: list[dict] = field(default_factory=list)      # [{label, value}]
    sub_cards: list[StatCard] = field(default_factory=list)       # 6개
    status_breakdown: list[dict] = field(default_factory=list)    # 도넛
    daily_trend: list[dict] = field(default_factory=list)         # [{label,total,new,canceled,expired}] 30일
    service_subs: list[dict] = field(default_factory=list)        # admin: {id,name,open,new,canceled,expired,revenue}
    recent: list = field(default_factory=list)
    past_due: list = field(default_factory=list)
    expiring: list = field(default_factory=list)
```

(b) `_cancel_counts`를 범용 `_audit_count`로 대체(아래 추가) — `_cancel_counts` 호출처는 `_audit_count`로 바뀌므로 `_cancel_counts`는 제거. `_audit_count` 추가:
```python
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


_EXPIRE_ACTIONS = ("subscription.expired",)
```

(c) `_refund_between` 추가:
```python
async def _refund_between(db, scope, start, end, *, kind=None) -> int:
    q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.status == PaymentStatus.CANCELED,
        Payment.requested_at >= start, Payment.requested_at < end)
    if kind is not None:
        q = q.where(Payment.kind == kind)
    return int(await db.scalar(_scoped(q, scope, Payment.service_id)) or 0)
```

(d) `_month_cards`/`_grand_totals`/`_service_totals`/`_series_12m` 제거하고 다음으로 교체:
```python
async def _revenue_cards(db, scope, now, month_start) -> list[StatCard]:
    end = now + relativedelta(seconds=1)
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


async def _sub_cards(db, scope, counts, now, month_start) -> list[StatCard]:
    end = now + relativedelta(seconds=1)
    qs = f"from={month_start.strftime('%Y-%m-%d')}&to={now.strftime('%Y-%m-%d')}"
    open_total = await _count(db, scope, _open_subs_cond(now))
    this_new = await _count(db, scope, Subscription.created_at >= month_start,
                            Subscription.created_at < end)
    uc = await _audit_count(db, scope, _USER_CANCEL_ACTIONS, month_start, end)
    pe = await _audit_count(db, scope, _PAYMENT_EXPIRE_ACTIONS, month_start, end)
    expired = await _audit_count(db, scope, _EXPIRE_ACTIONS, month_start, end)
    failed = await _payment_count_between(db, scope, PaymentStatus.FAILED, month_start, end)
    return [
        StatCard("전체 구독", f"{open_total:,}", "", True, 1, "/admin/subscriptions"),
        StatCard("신규 구독", f"{this_new:,}", "이번 달", True, 2,
                 "/admin/subscriptions?sort=created_at&dir=desc"),
        StatCard("구독 취소", f"{uc + pe:,}", "이번 달", uc + pe == 0, 4,
                 "/admin/subscriptions?status=CANCELED"),
        StatCard("미결제", f"{failed:,}", "이번 달", failed == 0, 4,
                 f"/admin/payments?status=FAILED&{qs}"),
        StatCard("사용자취소", f"{uc:,}", "이번 달", True, 2, None),
        StatCard("구독만료", f"{expired:,}", "이번 달", True, 3,
                 "/admin/subscriptions?status=EXPIRED"),
    ]


async def _series_12m(db, scope, now, month_start) -> tuple[list, list]:
    """(구독수[전체/신규], 일반매출) 12개월."""
    subs, one_off = [], []
    for i in range(11, -1, -1):
        start = month_start - relativedelta(months=i)
        end = start + relativedelta(months=1)
        label = f"{start.month}월"
        new_n = await _count(db, scope, Subscription.created_at >= start,
                             Subscription.created_at < end)
        at = min(end, now)
        total = await _count(db, scope, Subscription.created_at <= at, _open_subs_cond(at))
        subs.append({"label": label, "done": total, "failed": new_n})  # bars 재사용(done=전체, failed=신규)
        one_off.append({"label": label,
                        "value": await _revenue_between(db, scope, start, end,
                                                        kind=PaymentKind.ONE_OFF)})
    return subs, one_off


async def _daily_trend(db, scope, now) -> list[dict]:
    """최근 30일 일별 — 전체구독(일말 스냅샷)/신규/취소/만료."""
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for i in range(29, -1, -1):
        day = today - relativedelta(days=i)
        nxt = day + relativedelta(days=1)
        at = min(nxt, now)
        total = await _count(db, scope, Subscription.created_at <= at, _open_subs_cond(at))
        new_n = await _count(db, scope, Subscription.created_at >= day,
                             Subscription.created_at < nxt)
        uc = await _audit_count(db, scope, _USER_CANCEL_ACTIONS, day, nxt)
        pe = await _audit_count(db, scope, _PAYMENT_EXPIRE_ACTIONS, day, nxt)
        expired = await _audit_count(db, scope, _EXPIRE_ACTIONS, day, nxt)
        out.append({"label": f"{day.month}/{day.day}", "total": total, "new": new_n,
                    "canceled": uc + pe, "expired": expired})
    return out


async def _service_revenue(db, now, month_start) -> list[dict]:
    """서비스별 이번달 매출(총/구독/일반/환불) — SYSTEM_ADMIN 전용."""
    end = now + relativedelta(seconds=1)

    def _sum(*conds):
        return (select(func.coalesce(func.sum(Payment.amount), 0))
                .where(Payment.service_id == Service.id, *conds)
                .correlate(Service).scalar_subquery())
    done_m = (Payment.status == PaymentStatus.DONE,
              Payment.approved_at >= month_start, Payment.approved_at < end)
    total = _sum(*done_m)
    sub = _sum(*done_m, Payment.kind == PaymentKind.SUBSCRIPTION)
    one = _sum(*done_m, Payment.kind == PaymentKind.ONE_OFF)
    refund = _sum(Payment.status == PaymentStatus.CANCELED,
                  Payment.requested_at >= month_start, Payment.requested_at < end)
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
```

(e) `build_dashboard` 교체:
```python
async def build_dashboard(db, scope):
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    data = DashboardData()
    counts = {s: 0 for s in _STATUS_ORDER}
    rows = (await db.execute(_scoped(
        select(Subscription.status, func.count()).group_by(Subscription.status),
        scope, Subscription.service_id))).all()
    for status, n in rows:
        counts[status] = n
    data.revenue_cards = await _revenue_cards(db, scope, now, month_start)
    data.sub_cards = await _sub_cards(db, scope, counts, now, month_start)
    data.subs_months, data.one_off_months = await _series_12m(db, scope, now, month_start)
    data.daily_trend = await _daily_trend(db, scope, now)
    data.status_breakdown = [
        {"label": _STATUS_KO[s], "value": counts[s], "color": _STATUS_COLOR[s],
         "href": f"/admin/subscriptions?status={s}"}
        for s in _STATUS_ORDER if counts[s] > 0
    ] or [{"label": "데이터 없음", "value": 1, "color": "var(--black-10)", "href": None}]
    if scope is None:
        data.service_revenue = await _service_revenue(db, now, month_start)
        data.service_subs = await _service_subs(db, now, month_start)
    data.recent, data.past_due, data.expiring = await _rails(db, scope, now)
    return data
```
`and_`가 필요하면 `from sqlalchemy import and_` 추가(위 코드는 .where 다중인자라 불필요). `_pct_delta`는 미사용이 되면 두고(다른 곳 미사용) 무방하나 lint 경고 시 제거.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/integration/test_dashboard.py -x -q` → 전체 PASS.
이어서 `uv run pytest tests/integration tests/unit -q` 회귀. (e2e 대시보드는 템플릿이 구 필드를 참조해 깨짐 — Task 2에서 수정. 여기선 건드리지 말 것.)

- [ ] **Step 5: 커밋**
```bash
git add app/services/dashboard.py tests/integration/test_dashboard.py
git commit -m "feat(dashboard): 집계 v2 — 매출/환불 카드, 구독 6카드, 12개월·30일 시리즈, 서비스별

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 차트 매크로 + dashboard.html 3섹션

**Files:**
- Modify: `app/admin/templates/_charts.html` (multiline 매크로 추가)
- Modify: `app/admin/templates/dashboard.html`
- Test: `tests/e2e/test_dashboard_page.py` (재작성)

- [ ] **Step 1: e2e 재작성** — `tests/e2e/test_dashboard_page.py` 전체 교체:

```python
"""대시보드 v2 화면 e2e (요청 010)."""
from datetime import timedelta

from app.core.clock import utcnow
from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login


async def _seed(db, cipher):
    svc, _, _ = await create_service(db, cipher, name="대시보드서비스")
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="d-act",
                                    status="ACTIVE")
    now = utcnow()
    db.add(Payment(subscription_id=sub.id, service_id=svc.id, external_user_id="d-act",
                   order_id="d-sub", amount=10000, payment_type=PaymentType.RENEWAL,
                   kind=PaymentKind.SUBSCRIPTION, status=PaymentStatus.DONE,
                   idempotency_key="d-sub", requested_at=now, approved_at=now))
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="oo",
                   order_id="d-oo", amount=4000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="d-oo", requested_at=now, approved_at=now))
    await db.commit()
    return svc


async def test_dashboard_revenue_section(client, db, redis_client, cipher):
    await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    for label in ["총매출", "구독매출", "일반매출", "환불금액"]:
        assert label in html
    assert "서비스별 매출" in html
    assert "대시보드서비스" in html


async def test_dashboard_subscription_section(client, db, redis_client, cipher):
    await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    for label in ["전체 구독", "신규 구독", "구독 취소", "미결제", "사용자취소", "구독만료"]:
        assert label in html
    assert "구독 상태" in html             # 도넛
    assert "최근 30일" in html             # 일별 추이
    assert "서비스별 구독" in html         # 서비스별 구독 표
    assert 'href="/admin/subscriptions?status=ACTIVE"' in html   # 도넛 범례 링크


async def test_dashboard_twelve_month_charts(client, db, redis_client, cipher):
    await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    assert "최근 12개월 구독" in html
    assert "최근 12개월 일반매출" in html


async def test_dashboard_manager_scope_no_service_tables(client, db, redis_client, cipher):
    svc = await _seed(db, cipher)
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, mgr.email, pw)
    html = (await client.get("/admin")).text
    assert "서비스별 매출" not in html and "서비스별 구독" not in html
    assert "총매출" in html                # 카드는 스코프 집계로 노출
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_dashboard_page.py -x -q` → FAIL.

- [ ] **Step 3: multiline 매크로** — `app/admin/templates/_charts.html`의 `hbar` 매크로 위(또는 파일 끝)에 추가:
```jinja
{# ---- Multi-line (여러 시리즈 추이) — series=[{label, k1, k2, ...}], lines=[(key,라벨,색)] ---- #}
{%- macro multiline(series, lines, height=200) -%}
  {%- set vals = [] -%}
  {%- for p in series -%}{%- for k, _l, _c in lines -%}{%- set _ = vals.append(p[k]) -%}{%- endfor -%}{%- endfor -%}
  {%- set vmax = (vals | max) if vals and (vals | max) > 0 else 1 -%}
  {%- set n = series | length -%}{%- set w = 720 -%}{%- set h = height -%}{%- set pad = 24 -%}
  {%- set iw = w - pad * 2 -%}{%- set ih = h - pad * 2 -%}{%- set ns = (n - 1) if n > 1 else 1 -%}
  <svg viewBox="0 0 {{ w }} {{ h }}" class="chart" preserveAspectRatio="none" style="height:{{ height }}px">
    {%- for g in range(4) -%}
      {%- set gy = pad + ih * g / 3 -%}
      <line x1="{{ pad }}" y1="{{ gy }}" x2="{{ w - pad }}" y2="{{ gy }}" stroke="rgba(28,28,28,.06)" stroke-width="1"/>
    {%- endfor -%}
    {%- for key, _label, color in lines -%}
      {%- set pts = [] -%}
      {%- for p in series -%}
        {%- set x = pad + iw * loop.index0 / ns -%}
        {%- set y = pad + ih - (ih * p[key] / vmax) -%}
        {%- set _ = pts.append(x ~ ',' ~ y) -%}
      {%- endfor -%}
      <polyline points="{{ pts | join(' ') }}" fill="none" stroke="{{ color }}" stroke-width="2"
                stroke-linejoin="round" stroke-linecap="round"/>
    {%- endfor -%}
  </svg>
  <div class="legend">
    {%- for _key, label, color in lines -%}<span><i style="background:{{ color }}"></i>{{ label }}</span>{%- endfor -%}
  </div>
{%- endmacro -%}
```

- [ ] **Step 4: dashboard.html 재구성** — content 블록(`{% block content %}` ~ `{% endblock %}`)의
시계 스크립트 아래부터 끝까지를 다음으로 교체(rail 블록은 그대로 유지):
```jinja
{# ===== 매출 섹션 ===== #}
<div class="block-head" style="margin-top:8px"><h2>매출 <span class="muted" style="font-size:12px;font-weight:400">이번 달</span></h2></div>
<div class="stats">
  {% for c in d.revenue_cards %}
  {% if c.href %}<a class="stat stat-tint-{{ c.tint }}" href="{{ c.href }}" style="color:inherit">
  {% else %}<div class="stat stat-tint-{{ c.tint }}">{% endif %}
    <span class="stat-label">{{ c.label }}</span>
    <div class="stat-row"><span class="stat-value">{{ c.value }}</span></div>
  {% if c.href %}</a>{% else %}</div>{% endif %}
  {% endfor %}
</div>
{% if is_admin and d.service_revenue %}
<div class="block">
  <div class="block-head"><h2>서비스별 매출</h2></div>
  <table>
    <thead><tr><th>서비스</th><th>총매출</th><th>구독</th><th>일반</th><th>환불</th></tr></thead>
    <tbody>
    {% for r in d.service_revenue %}
      <tr style="cursor:pointer" onclick="location.href='/admin/services/{{ r.id }}'">
        <td><a href="/admin/services/{{ r.id }}">{{ r.name }}</a></td>
        <td style="font-weight:600">{{ "{:,}".format(r.total) }}원</td>
        <td class="muted">{{ "{:,}".format(r.sub) }}원</td>
        <td class="muted">{{ "{:,}".format(r.one_off) }}원</td>
        <td class="muted">{{ "{:,}".format(r.refund) }}원</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}

{# ===== 12개월 차트 ===== #}
<div class="grid grid-2">
  <div class="block">
    <div class="block-head"><h2>최근 12개월 구독</h2><span class="muted" style="font-size:12px">전체/신규</span></div>
    {{ charts.bars(d.subs_months, label_a='전체구독', label_b='신규구독',
                   color_a='var(--accent-indigo)', color_b='var(--accent-mint)') }}
  </div>
  <div class="block">
    <div class="block-head"><h2>최근 12개월 일반매출</h2></div>
    {{ charts.area(d.one_off_months) }}
  </div>
</div>

{# ===== 구독 섹션 ===== #}
<div class="block-head"><h2>구독 <span class="muted" style="font-size:12px;font-weight:400">이번 달</span></h2></div>
<div class="stats">
  {% for c in d.sub_cards %}
  {% if c.href %}<a class="stat stat-tint-{{ c.tint }}" href="{{ c.href }}" style="color:inherit">
  {% else %}<div class="stat stat-tint-{{ c.tint }}">{% endif %}
    <span class="stat-label">{{ c.label }}</span>
    <div class="stat-row"><span class="stat-value">{{ c.value }}</span></div>
  {% if c.href %}</a>{% else %}</div>{% endif %}
  {% endfor %}
</div>
<div class="grid grid-2">
  <div class="block">
    <div class="block-head"><h2>구독 상태</h2></div>
    {{ charts.donut(d.status_breakdown) }}
  </div>
  <div class="block">
    <div class="block-head"><h2>최근 30일 구독 추이</h2><span class="muted" style="font-size:12px">전체/신규/취소/만료</span></div>
    {{ charts.multiline(d.daily_trend,
        [('total','전체구독','var(--accent-indigo)'), ('new','신규','var(--accent-mint)'),
         ('canceled','취소','var(--accent-orange)'), ('expired','만료','var(--accent-red)')]) }}
  </div>
</div>
{% if is_admin and d.service_subs %}
<div class="block">
  <div class="block-head"><h2>서비스별 구독</h2></div>
  <table>
    <thead><tr><th>서비스</th><th>현재구독</th><th>신규</th><th>취소</th><th>만료</th><th>구독매출</th></tr></thead>
    <tbody>
    {% for r in d.service_subs %}
      <tr style="cursor:pointer" onclick="location.href='/admin/services/{{ r.id }}'">
        <td><a href="/admin/services/{{ r.id }}">{{ r.name }}</a></td>
        <td>{{ "{:,}".format(r.open) }}</td>
        <td class="muted">{{ "{:,}".format(r.new) }}</td>
        <td class="muted">{{ "{:,}".format(r.canceled) }}</td>
        <td class="muted">{{ "{:,}".format(r.expired) }}</td>
        <td style="font-weight:600">{{ "{:,}".format(r.revenue) }}원</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
{% endblock %}
```
(상단 page-head + 시계 스크립트 + `{% block rail %}`는 그대로 둔다.)

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/e2e/test_dashboard_page.py tests/e2e/test_admin_flows.py tests/e2e/test_htmx_partials.py -q` → 전체 PASS. 이어서 `uv run pytest tests/e2e -q` 회귀.

- [ ] **Step 6: 커밋**
```bash
git add app/admin/templates/_charts.html app/admin/templates/dashboard.html tests/e2e/test_dashboard_page.py
git commit -m "feat(dashboard): 화면 3섹션 재구성(매출/12개월/구독) + multiline 차트

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 결제이력 필터 순서 변경

**Files:**
- Modify: `app/admin/templates/payments/list.html`
- Test: `tests/e2e/test_admin_operations.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/e2e/test_admin_operations.py` 끝에:
```python
async def test_payments_filter_order(client, db, redis_client, cipher):
    """필터 순서: 서비스 → 종류 → 상태 → 기간."""
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin/payments")).text
    i_service = html.find('name="service_id"')
    i_kind = html.find('name="kind"')
    i_status = html.find('name="status"')
    i_from = html.find('name="from"')
    assert -1 < i_service < i_kind < i_status < i_from
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py::test_payments_filter_order -x -q`

- [ ] **Step 3: 템플릿 수정** — `app/admin/templates/payments/list.html`의 `L.toolbar` 호출에서
extra_selects 순서를 **service_id → kind → status**로 재배치(date_inputs는 그 뒤로 toolbar가 자동):
```jinja
{{ L.toolbar('/admin/payments', pp, '주문번호·사용자 검색',
   [('service_id', service_options, service_filter),
    ('kind', [('','전체 종류'),('SUBSCRIPTION','구독'),('ONE_OFF','일반')], kind_filter),
    ('status', [('','전체 상태'),('DONE','DONE'),('FAILED','FAILED'),('PENDING','PENDING'),('CANCELED','CANCELED')], status_filter)],
   date_inputs=[('from', from_filter), ('to', to_filter)]) }}
```
(`_list.html` toolbar는 extra_selects를 먼저, date_inputs를 그 다음에 렌더하므로 기간이 마지막에 온다 — 확인.)

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py -q` → 전체 PASS.

- [ ] **Step 5: 커밋**
```bash
git add app/admin/templates/payments/list.html tests/e2e/test_admin_operations.py
git commit -m "feat(payments): 결제이력 필터 순서 서비스→종류→상태→기간

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 서비스 상세 일반결제 표

**Files:**
- Modify: `app/admin/routes/services.py` (`services_detail`)
- Create: `app/admin/templates/services/_oneoff_table.html`
- Modify: `app/admin/templates/services/detail.html`
- Test: `tests/e2e/test_service_detail_page.py`

- [ ] **Step 1: 실패 테스트 추가** — `tests/e2e/test_service_detail_page.py` 끝에:
```python
async def test_service_detail_shows_one_off_payments(client, db, redis_client, cipher):
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc, _, _ = await create_service(db, cipher, name="상세일반결제")
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="oo-u",
                   order_id="det-oo", amount=5000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="det-oo", requested_at=utcnow(), approved_at=utcnow()))
    await db.commit()
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "일반결제" in html
    assert "det-oo" in html and "oo-u" in html
```
(`_admin` 헬퍼가 이 파일에 있는지 확인 — 있으면 사용, 없으면 기존 로그인 패턴 사용.)

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_service_detail_page.py::test_service_detail_shows_one_off_payments -x -q`

- [ ] **Step 3: 라우트 수정** — `app/admin/routes/services.py` `services_detail`:
import에 `PaymentKind` 추가(`from app.models import ... PaymentKind ...`). 구독 페이지 블록 아래에 추가:
```python
    # 일반(단건) 결제 페이지 (요청 010)
    opp = PageParams.from_request(request, sortable={"requested_at"},
                                  default_sort="requested_at")
    oneoff_base = (select(Payment).where(Payment.service_id == service_id,
                                         Payment.kind == PaymentKind.ONE_OFF))
    oneoff_count_q = select(func.count()).select_from(oneoff_base.order_by(None).subquery())
    oneoff_items_q = oneoff_base.order_by(Payment.requested_at.desc())
    oneoff_page = await paginate(db, oneoff_items_q, oneoff_count_q, opp)
```
주의: `paginate`는 단일 엔티티 select면 `page.items`가 Row가 되므로 `[r[0] for r in ...]` 변환 필요.
`oneoff_page.items = [r[0] for r in oneoff_page.items]` 추가.
`Payment` import 확인(이미 import됨).
htmx 타깃 매핑에 추가: `"list-svc-oneoff": "services/_oneoff_table.html"`.
render kwargs에 `oneoff_page=oneoff_page` 추가.
PageParams 충돌 주의: 구독은 `spp`, 일반결제는 `opp`로 별도 사용(둘 다 q/sort 파라미터를 읽지만 서로
다른 정렬키라 영향 적음 — opp는 requested_at만 sortable).

- [ ] **Step 4: 템플릿 신설** — `app/admin/templates/services/_oneoff_table.html`:
```jinja
{# 서비스 상세 — 일반(단건) 결제 partial #}
{% import "_list.html" as L %}
<div id="list-svc-oneoff">
<div class="card">
  <div class="block-head"><h2 style="margin:0">일반결제</h2>
    <span class="muted" style="font-size:12px">구독과 무관한 단건 결제</span></div>
  <table>
    <thead><tr>
      <th>승인시각</th><th>사용자</th><th>주문번호</th>
      <th>금액</th><th>상태</th>
    </tr></thead>
    <tbody>
    {% for p in oneoff_page.items %}
      <tr>
        <td class="muted">{{ p.approved_at|kst("%Y-%m-%d %H:%M") if p.approved_at else '-' }}</td>
        <td>{{ p.external_user_id or '-' }}</td>
        <td style="font-family:ui-monospace,monospace;font-size:12px">{{ p.order_id }}</td>
        <td style="font-weight:600">{{ "{:,}".format(p.amount) }}원</td>
        <td><span class="badge badge-{{ p.status }}">{{ p.status }}</span></td>
      </tr>
    {% else %}
      <tr><td colspan="5" class="muted">일반결제 내역이 없습니다</td></tr>
    {% endfor %}
    </tbody>
  </table>
  {{ L.pager(oneoff_page, '/admin/services/' ~ service.id, opp) }}
</div>
</div>
```
주의: pager가 `opp`를 참조하므로 render에 `opp=opp`도 전달해야 함 — Step 3 render kwargs에 `opp=opp` 추가.

- [ ] **Step 5: detail.html include 추가** — `{% include "services/_subs_table.html" %}` 다음 줄에:
```jinja
{% include "services/_oneoff_table.html" %}
```

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/e2e/test_service_detail_page.py tests/e2e/test_htmx_partials.py -q` → 전체 PASS.

- [ ] **Step 7: 커밋**
```bash
git add app/admin/routes/services.py app/admin/templates/services/_oneoff_table.html app/admin/templates/services/detail.html tests/e2e/test_service_detail_page.py
git commit -m "feat(services): 서비스 상세에 일반결제 표 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 전체 검증
- [ ] **Step 1: 전체 테스트** — Run: `uv run pytest -q` → 전체 PASS.
- [ ] **Step 2: 잔여 확인**
  - `grep -rn "d.cards\|revenue_months\|trend_months\|service_totals\|d.totals" app/admin/templates` → 0건(구 필드 잔존 없음).
  - `grep -rn "_month_cards\|_grand_totals\|_service_totals" app/services/dashboard.py` → 0건.
- [ ] **Step 3: 커밋(잔여 정리 시)**
```bash
git add -A app tests
git commit -m "test: 대시보드 v2 잔여 정리"
```

## 변경하지 않는 것 (스펙 동일)
- 결제/구독 도메인 로직, 외부 API, 감사 기록 방식, 모델/마이그레이션.
- 우측 레일(최근결제/미수/만료임박), 도넛, 상단 실시간 시계.
