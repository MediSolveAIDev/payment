# 대시보드 개편 구현 계획 (요청 008 §2~5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 대시보드를 카드 8개(클릭 이동)·12개월 차트 3종·서비스별 누적 테이블·우측 레일 3섹션으로 개편하고, 결제 리스트에 월 필터를 추가한다.

**Architecture:** `app/services/dashboard.py`를 책임별 함수(`_month_cards`/`_series_12m`/`_service_totals`/`_rails`)로 분해해 확장. 취소 구분은 감사로그 기반(target_id→구독 조인으로 스코프 적용), 과거 월 전체 구독수는 스냅샷 근사. 템플릿은 기존 SnowUI 매크로 재사용(bars 라벨 파라미터화, donut 범례 링크).

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Jinja2 SVG 차트 매크로, pytest

**스펙:** `docs/superpowers/specs/2026-06-08-dashboard-revamp-design.md`
**테스트 실행:** `uv run pytest <경로> -q`

## 파일 구조

- `app/admin/routes/subscriptions.py` — `payments_list`에 month 필터 (Task 1)
- `app/admin/templates/payments/list.html` — month 입력 (Task 1)
- `app/services/dashboard.py` — 전면 개편: 카드 8 + 시리즈 + 토탈 + 레일 (Task 2~3)
- `app/admin/templates/_charts.html` — bars 라벨 파라미터, donut href (Task 4)
- `app/admin/templates/dashboard.html` — 레이아웃 개편 (Task 4)
- `tests/integration/test_dashboard.py` — 신설 (Task 2~3)
- `tests/e2e/test_dashboard_page.py` — 신설 (Task 4)

---

### Task 1: 결제 리스트 월(month) 필터

**Files:**
- Modify: `app/admin/routes/subscriptions.py` (payments_list)
- Modify: `app/admin/templates/payments/list.html`
- Test: `tests/e2e/test_admin_operations.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_admin_operations.py` 끝에 추가 (파일에 `create_service/create_plan/create_subscription/create_user/admin_login` import가 이미 있는지 확인하고 없으면 factories에서 추가):

```python
async def test_payments_month_filter(client, db, redis_client, cipher):
    """month=YYYY-MM 필터가 requested_at 기준으로 해당 월만 보여준다."""
    from datetime import datetime, timezone
    from app.models import Payment
    svc, _, _ = await create_service(db, cipher, name="pay-month-svc")
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="pm-user")
    db.add(Payment(subscription_id=sub.id, order_id="pm-old", amount=1000,
                   payment_type="FIRST", status="DONE", idempotency_key="pm-old",
                   requested_at=datetime(2025, 3, 10, tzinfo=timezone.utc)))
    db.add(Payment(subscription_id=sub.id, order_id="pm-new", amount=2000,
                   payment_type="RECURRING", status="DONE", idempotency_key="pm-new",
                   requested_at=datetime(2025, 4, 10, tzinfo=timezone.utc)))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)

    resp = await client.get("/admin/payments?month=2025-03")
    assert "pm-old" in resp.text and "pm-new" not in resp.text
    # 잘못된 형식은 무시(전체)
    resp = await client.get("/admin/payments?month=bogus")
    assert "pm-old" in resp.text and "pm-new" in resp.text
    # month 입력 렌더
    assert 'type="month"' in resp.text
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py::test_payments_month_filter -x -q`
Expected: FAIL (`pm-new`가 필터링되지 않음)

- [ ] **Step 3: 라우트 수정** — `app/admin/routes/subscriptions.py`:

상단에 import 추가:

```python
from datetime import datetime, timezone

from dateutil.relativedelta import relativedelta
```

모듈 레벨에 헬퍼 추가 (`_PAY_SORT` 아래):

```python
def _month_range(raw: str):
    """'YYYY-MM' → (월초, 익월초) UTC. 형식 오류는 None(필터 무시)."""
    try:
        start = datetime.strptime(raw, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return start, start + relativedelta(months=1)
```

`payments_list`에서 `filter_keys=("status",)` → `filter_keys=("status", "month")` 로 바꾸고, status 필터 적용 아래에 추가:

```python
    month_filter = pp.filters.get("month", "")
    if month_filter:
        rng = _month_range(month_filter)
        if rng:
            base = base.where(Payment.requested_at >= rng[0],
                              Payment.requested_at < rng[1])
        else:
            month_filter = ""
```

`render` 호출에 `month_filter=month_filter` kwarg 추가.

- [ ] **Step 4: 템플릿 수정** — `app/admin/templates/payments/list.html`의 `L.toolbar(...)` 호출을 다음으로 교체 (toolbar 매크로는 select만 지원하므로 month 입력은 toolbar 폼 밖 별도 GET 폼으로 두면 필터가 분리되어 어색함 — toolbar를 쓰지 않고 직접 폼 구성):

```html
<form method="get" action="/admin/payments" class="toolbar">
  <div class="searchbox" style="width:240px">
    <span data-lucide="search"></span>
    <input type="text" name="q" value="{{ pp.q }}" placeholder="주문번호·사용자 검색">
  </div>
  <select name="status" onchange="this.form.requestSubmit()">
    {% for value, label in [('','전체 상태'),('DONE','DONE'),('FAILED','FAILED'),('PENDING','PENDING'),('CANCELED','CANCELED')] %}
    <option value="{{ value }}" {{ 'selected' if status_filter == value }}>{{ label }}</option>
    {% endfor %}
  </select>
  <input type="month" name="month" value="{{ month_filter }}" style="width:auto"
         onchange="this.form.requestSubmit()">
  {% if pp.sort %}<input type="hidden" name="sort" value="{{ pp.sort }}"><input type="hidden" name="dir" value="{{ pp.direction }}">{% endif %}
  <button class="btn btn-sub" type="submit">검색</button>
  {% if pp.q or pp.filters %}<a class="btn-text" href="/admin/payments">초기화</a>{% endif %}
</form>
```

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py -q`
Expected: 전체 PASS

- [ ] **Step 6: 커밋**

```bash
git add app/admin/routes/subscriptions.py app/admin/templates/payments/list.html tests/e2e/test_admin_operations.py
git commit -m "feat(payments): 결제 리스트 월(month) 필터"
```

---

### Task 2: 대시보드 집계 — 카드 8개 (StatCard.href)

**Files:**
- Modify: `app/services/dashboard.py`
- Test: `tests/integration/test_dashboard.py` (신설)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_dashboard.py` 신설:

```python
"""대시보드 집계 통합 테스트 (요청 008)."""
from datetime import timedelta

from app.core.clock import utcnow
from app.models import Payment, PaymentStatus, PaymentType
from app.services.audit import record_audit
from app.services.dashboard import build_dashboard
from tests.factories import create_plan, create_service, create_subscription


async def _paid(db, sub, amount, *, status="DONE", requested_at=None, order=None):
    now = requested_at or utcnow()
    p = Payment(subscription_id=sub.id, order_id=order or f"o-{sub.id.hex[:8]}-{amount}",
                amount=amount, payment_type=PaymentType.RECURRING, status=status,
                idempotency_key=f"k-{order or amount}-{sub.id.hex[:6]}",
                requested_at=now,
                approved_at=now if status == PaymentStatus.DONE else None)
    db.add(p)
    await db.commit()
    return p


def _card(data, label):
    return next(c for c in data.cards if c.label == label)


async def test_cards_open_subs_includes_canceled_in_period(db, cipher):
    """전체 구독 = 열린 상태 + 기간 내 CANCELED."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-act",
                              status="ACTIVE")
    await create_subscription(db, cipher, svc, plan, external_user_id="u-can",
                              status="CANCELED",
                              period_end=utcnow() + timedelta(days=5),
                              next_billing_at=None)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-exp",
                              status="EXPIRED",
                              period_end=utcnow() - timedelta(days=5),
                              next_billing_at=None)
    data = await build_dashboard(db, None)
    card = _card(data, "전체 구독")
    assert card.value == "2"          # ACTIVE + 기간 내 CANCELED (EXPIRED 제외)
    assert card.href == "/admin/subscriptions"


async def test_cards_cancel_split_from_audit(db, cipher):
    """취소 카드 — 사용자취소(cancel/force_cancel) vs 결제만료(suspended) 구분."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    s1 = await create_subscription(db, cipher, svc, plan, external_user_id="c-1")
    s2 = await create_subscription(db, cipher, svc, plan, external_user_id="c-2")
    s3 = await create_subscription(db, cipher, svc, plan, external_user_id="c-3")
    await record_audit(db, actor_type="SERVICE", action="subscription.cancel",
                       target_type="subscription", target_id=str(s1.id))
    await record_audit(db, actor_type="USER", action="subscription.force_cancel",
                       target_type="subscription", target_id=str(s2.id))
    await record_audit(db, actor_type="SYSTEM", action="subscription.suspended",
                       target_type="subscription", target_id=str(s3.id))
    await db.commit()
    data = await build_dashboard(db, None)
    card = _card(data, "구독 취소")
    assert card.value == "3"
    assert "사용자 2" in card.delta and "결제만료 1" in card.delta


async def test_cards_cancel_scope_via_target_subscription(db, cipher):
    """SERVICE_MANAGER 스코프 — 취소 감사가 target 구독의 서비스로 제한된다."""
    svc_a, _, _ = await create_service(db, cipher)
    svc_b, _, _ = await create_service(db, cipher)
    plan_a = await create_plan(db, svc_a)
    plan_b = await create_plan(db, svc_b)
    sa = await create_subscription(db, cipher, svc_a, plan_a, external_user_id="a-1")
    sb = await create_subscription(db, cipher, svc_b, plan_b, external_user_id="b-1")
    for s in (sa, sb):
        await record_audit(db, actor_type="SERVICE", action="subscription.cancel",
                           target_type="subscription", target_id=str(s.id))
    await db.commit()
    data = await build_dashboard(db, [svc_a.id])
    assert _card(data, "구독 취소").value == "1"


async def test_cards_success_rate_and_arpu(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="r-1",
                                    status="ACTIVE")
    await _paid(db, sub, 10000, order="r-done-1")
    await _paid(db, sub, 10000, order="r-done-2")
    await _paid(db, sub, 10000, status="FAILED", order="r-fail-1")
    data = await build_dashboard(db, None)
    assert _card(data, "결제 성공률").value == "67%"      # 2/3 반올림
    assert _card(data, "ARPU").value == "20,000원"        # 20000 / ACTIVE 1
    assert _card(data, "이번달 미결제").value == "1"


async def test_cards_zero_division_safe(db, cipher):
    """데이터 없음 — 성공률/ARPU '—', 에러 없이 렌더 데이터 생성."""
    data = await build_dashboard(db, None)
    assert _card(data, "결제 성공률").value == "—"
    assert _card(data, "ARPU").value == "—"


async def test_cards_trial_count_and_href(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, trial_enabled=True, trial_days=7)
    await create_subscription(db, cipher, svc, plan, external_user_id="t-1",
                              status="TRIAL")
    data = await build_dashboard(db, None)
    card = _card(data, "체험 구독")
    assert card.value == "1"
    assert card.href == "/admin/subscriptions?status=TRIAL"
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_dashboard.py -x -q`
Expected: FAIL (카드 라벨 "전체 구독" 없음 — StopIteration)

- [ ] **Step 3: dashboard.py 카드 구현**

`app/services/dashboard.py`에서:

(a) import에 추가: `String`, `cast` (sqlalchemy), `AuditLog` (app.models).

```python
from sqlalchemy import String, cast, func, select
...
from app.models import (
    AuditLog,
    Payment,
    PaymentStatus,
    Service,
    Subscription,
    SubscriptionStatus,
)
```

(b) `StatCard`에 href 추가:

```python
@dataclass
class StatCard:
    label: str
    value: str
    delta: str        # 예: "+12.5%"
    up: bool
    tint: int         # 1~4
    href: str | None = None   # 클릭 시 이동(없으면 비링크)
```

(c) 모듈 상수 추가 (`_STATUS_KO` 아래):

```python
# '열린' 구독 상태 — CANCELED는 기간 내일 때만 열린 것으로 본다
_OPEN_STATUSES = (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE,
                  SubscriptionStatus.PAST_DUE, SubscriptionStatus.SUSPENDED)
_USER_CANCEL_ACTIONS = ("subscription.cancel", "subscription.force_cancel")
_PAYMENT_EXPIRE_ACTIONS = ("subscription.suspended",)
```

(d) 공용 헬퍼 추가 (`_pct_delta` 아래):

```python
def _open_subs_cond(at):
    """at 시점에 '열려 있는' 구독 조건 (CANCELED는 기간 내만)."""
    return (Subscription.status.in_(_OPEN_STATUSES)
            | ((Subscription.status == SubscriptionStatus.CANCELED)
               & (Subscription.current_period_end > at)))


async def _count(db, scope, *where) -> int:
    q = select(func.count()).select_from(Subscription).where(*where)
    return int(await db.scalar(_scoped(q, scope, Subscription.service_id)) or 0)


async def _cancel_counts(db, scope, start, end) -> tuple[int, int]:
    """(사용자취소, 결제만료) — 감사로그 기반. 스코프는 target 구독의 서비스로 제한."""
    async def count_actions(actions) -> int:
        q = (select(func.count()).select_from(AuditLog)
             .where(AuditLog.action.in_(actions),
                    AuditLog.created_at >= start, AuditLog.created_at < end))
        if scope is not None:
            sub_sq = select(cast(Subscription.id, String)).where(
                Subscription.service_id.in_(scope))
            q = q.where(AuditLog.target_id.in_(sub_sq))
        return int(await db.scalar(q) or 0)

    return (await count_actions(_USER_CANCEL_ACTIONS),
            await count_actions(_PAYMENT_EXPIRE_ACTIONS))


async def _revenue_between(db, scope, start, end) -> int:
    q = select(func.coalesce(func.sum(Payment.amount), 0)).select_from(Payment).join(
        Subscription, Payment.subscription_id == Subscription.id).where(
        Payment.status == PaymentStatus.DONE,
        Payment.approved_at >= start, Payment.approved_at < end)
    return int(await db.scalar(_scoped(q, scope, Subscription.service_id)) or 0)


async def _payment_count_between(db, scope, status, start, end) -> int:
    q = select(func.count()).select_from(Payment).join(
        Subscription, Payment.subscription_id == Subscription.id).where(
        Payment.status == status,
        Payment.requested_at >= start, Payment.requested_at < end)
    return int(await db.scalar(_scoped(q, scope, Subscription.service_id)) or 0)
```

(기존 `build_dashboard` 내부의 `revenue_between`/`new_subs_between` 지역 함수는 이 모듈 헬퍼로 대체)

(e) 카드 빌더 — `build_dashboard` 위에 추가:

```python
async def _month_cards(db, scope, counts, now, month_start) -> list[StatCard]:
    prev_start = month_start - relativedelta(months=1)
    end = now + relativedelta(seconds=1)
    month_qs = now.strftime("%Y-%m")

    open_total = await _count(db, scope, _open_subs_cond(now))
    active = counts[SubscriptionStatus.ACTIVE]
    active_ratio = (active / open_total * 100) if open_total else 0

    this_new = await _count(db, scope, Subscription.created_at >= month_start,
                            Subscription.created_at < end)
    last_new = await _count(db, scope, Subscription.created_at >= prev_start,
                            Subscription.created_at < month_start)
    new_delta, new_up = _pct_delta(this_new, last_new)

    this_rev = await _revenue_between(db, scope, month_start, end)
    last_rev = await _revenue_between(db, scope, prev_start, month_start)
    rev_delta, rev_up = _pct_delta(this_rev, last_rev)

    failed = await _payment_count_between(db, scope, PaymentStatus.FAILED,
                                          month_start, end)
    done = await _payment_count_between(db, scope, PaymentStatus.DONE,
                                        month_start, end)
    user_cancel, pay_expire = await _cancel_counts(db, scope, month_start, end)

    rate = f"{done / (done + failed) * 100:.0f}%" if (done + failed) else "—"
    arpu = _won(round(this_rev / active)) if active and this_rev else "—"

    return [
        StatCard("전체 구독", f"{open_total:,}", f"활성 {active_ratio:.0f}%", True, 1,
                 "/admin/subscriptions"),
        StatCard("신규 구독", f"{this_new:,}", new_delta, new_up, 2,
                 "/admin/subscriptions?sort=created_at&dir=desc"),
        StatCard("이번달 매출", _won(this_rev), rev_delta, rev_up, 3,
                 f"/admin/payments?status=DONE&month={month_qs}"),
        StatCard("이번달 미결제", f"{failed:,}",
                 "주의" if failed else "안정", failed == 0, 4,
                 f"/admin/payments?status=FAILED&month={month_qs}"),
        StatCard("구독 취소", f"{user_cancel + pay_expire:,}",
                 f"사용자 {user_cancel} · 결제만료 {pay_expire}",
                 (user_cancel + pay_expire) == 0, 1,
                 "/admin/subscriptions?status=CANCELED"),
        StatCard("결제 성공률", rate, "", True, 2,
                 f"/admin/payments?month={month_qs}"),
        StatCard("ARPU", arpu, "활성 구독당 매출", True, 3, None),
        StatCard("체험 구독", f"{counts[SubscriptionStatus.TRIAL]:,}", "", True, 4,
                 "/admin/subscriptions?status=TRIAL"),
    ]
```

(f) `build_dashboard`에서 기존 카드 4개 구성 블록(이번달/지난달 매출·신규 계산, 지역 함수
`revenue_between`/`new_subs_between` 정의 포함)을 제거하고:

```python
    data.cards = await _month_cards(db, scope, counts, now, month_start)
```

**주의:** 기존 12개월 매출 시리즈 블록이 지역 함수 `revenue_between(start, end)`를 호출하고
있다 — 지역 함수를 제거했으므로 그 호출을 모듈 헬퍼로 교체해야 한다:

```python
        amt = await _revenue_between(db, scope, start, end)
```

(상태별 `counts` 집계와 도넛/12개월 매출/6개월 결제/랭킹/최근 결제 등 나머지 블록은 이
시점에는 그대로 둔다 — Task 3에서 정리. `prev_month_start` 변수가 미사용이 되면 함께 제거)

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/integration/test_dashboard.py -x -q` → PASS. 이어서 `uv run pytest tests/e2e/test_admin_flows.py -q` (대시보드 렌더 회귀 — dashboard.html이 아직 구 템플릿이므로 카드 4→8이어도 루프 렌더라 통과해야 함)

- [ ] **Step 5: 커밋**

```bash
git add app/services/dashboard.py tests/integration/test_dashboard.py
git commit -m "feat(dashboard): 이번달 카드 8종 (클릭 이동 href, 취소 구분, 성공률/ARPU)"
```

---

### Task 3: 대시보드 집계 — 12개월 시리즈 + 토탈 + 레일

**Files:**
- Modify: `app/services/dashboard.py`
- Test: `tests/integration/test_dashboard.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_dashboard.py` 끝에 추가:

```python
async def test_series_new_vs_canceled_and_snapshot(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="s-1",
                                    status="ACTIVE")
    await record_audit(db, actor_type="SERVICE", action="subscription.cancel",
                       target_type="subscription", target_id=str(sub.id))
    await db.commit()
    data = await build_dashboard(db, None)
    assert len(data.trend_months) == 12
    cur = data.trend_months[-1]                  # 이번 달
    assert cur["done"] == 1                       # 신규 1
    assert cur["failed"] == 1                     # 취소 1
    assert len(data.subs_months) == 12
    # 이번 달 스냅샷은 아직 월말 전 — 마지막 달은 현재 열린 구독 수와 동일해야 함
    assert data.subs_months[-1]["value"] == 1


async def test_service_totals_admin_only(db, cipher):
    svc, _, _ = await create_service(db, cipher, name="총계서비스")
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="tt-1",
                                    status="ACTIVE")
    await _paid(db, sub, 30000, order="tt-pay")
    data = await build_dashboard(db, None)
    assert data.totals["sub_count"] == 1
    assert data.totals["revenue"] == 30000
    row = next(r for r in data.service_totals if r["name"] == "총계서비스")
    assert row["open_count"] == 1 and row["total_count"] == 1
    assert row["revenue"] == 30000
    assert row["id"]                              # 서비스 상세 링크용
    # SERVICE_MANAGER 스코프에서는 서비스별 테이블 없음 (totals는 스코프 합계)
    scoped = await build_dashboard(db, [svc.id])
    assert scoped.service_totals == []
    assert scoped.totals["sub_count"] == 1


async def test_rails_past_due_and_expiring(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="pd-1",
                              status="PAST_DUE")
    await create_subscription(db, cipher, svc, plan, external_user_id="ex-1",
                              status="ACTIVE",
                              period_end=utcnow() + timedelta(days=3))
    await create_subscription(db, cipher, svc, plan, external_user_id="ex-far",
                              status="ACTIVE",
                              period_end=utcnow() + timedelta(days=30))
    data = await build_dashboard(db, None)
    assert [s.external_user_id for s in data.past_due] == ["pd-1"]
    expiring_ids = [s.external_user_id for s in data.expiring]
    assert "ex-1" in expiring_ids and "ex-far" not in expiring_ids
    # PAST_DUE도 기간종료가 7일 내면 만료 임박에 포함될 수 있음 — pd-1의 period_end는
    # factories 기본(한 달 뒤)이므로 미포함
    assert "pd-1" not in expiring_ids
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_dashboard.py -k "series or totals or rails" -x -q`
Expected: FAIL (`trend_months` 속성 없음)

- [ ] **Step 3: dashboard.py 시리즈/토탈/레일 구현**

(a) `DashboardData` 교체:

```python
@dataclass
class DashboardData:
    cards: list[StatCard] = field(default_factory=list)
    revenue_months: list[dict] = field(default_factory=list)   # [{label, value}]
    trend_months: list[dict] = field(default_factory=list)     # [{label, done(신규), failed(취소)}]
    subs_months: list[dict] = field(default_factory=list)      # [{label, value}] 월말 스냅샷
    status_breakdown: list[dict] = field(default_factory=list)  # [{label, value, color, href}]
    totals: dict = field(default_factory=dict)                  # {sub_count, revenue}
    service_totals: list[dict] = field(default_factory=list)    # [{id, name, open_count, total_count, revenue}]
    recent: list = field(default_factory=list)
    past_due: list = field(default_factory=list)                # [Subscription]
    expiring: list = field(default_factory=list)                # [Subscription]
```

(`payment_months`, `service_ranking` 필드 제거 — 사용처는 Task 4에서 템플릿과 함께 정리)

(b) 시리즈/토탈/레일 빌더 추가:

```python
async def _series_12m(db, scope, now, month_start) -> tuple[list, list, list]:
    """(매출, 신규vs취소, 월말 스냅샷) 12개월 시리즈."""
    revenue, trend, snapshots = [], [], []
    for i in range(11, -1, -1):
        start = month_start - relativedelta(months=i)
        end = start + relativedelta(months=1)
        label = f"{start.month}월"
        revenue.append({"label": label,
                        "value": await _revenue_between(db, scope, start, end)})
        new_n = await _count(db, scope, Subscription.created_at >= start,
                             Subscription.created_at < end)
        uc, pe = await _cancel_counts(db, scope, start, end)
        trend.append({"label": label, "done": new_n, "failed": uc + pe})
        # 월말 스냅샷 근사 — 진행 중인 이번 달은 현재 시점 기준
        at = min(end, now)
        snap = await _count(db, scope, Subscription.created_at <= at,
                            _open_subs_cond(at))
        snapshots.append({"label": label, "value": snap})
    return revenue, trend, snapshots


async def _grand_totals(db, scope) -> dict:
    """누적 구독개수(전체 기간 생성) + 누적 매출(DONE 합)."""
    sub_count = await _count(db, scope)
    rev_q = select(func.coalesce(func.sum(Payment.amount), 0)).select_from(Payment).join(
        Subscription, Payment.subscription_id == Subscription.id).where(
        Payment.status == PaymentStatus.DONE)
    revenue = int(await db.scalar(_scoped(rev_q, scope, Subscription.service_id)) or 0)
    return {"sub_count": sub_count, "revenue": revenue}


async def _service_totals(db, now) -> list[dict]:
    """서비스별 현재/누적 구독수 + 누적 매출 (SYSTEM_ADMIN 전용, 누적 매출 내림차순)."""
    open_n = (select(func.count()).select_from(Subscription)
              .where(Subscription.service_id == Service.id, _open_subs_cond(now))
              .correlate(Service).scalar_subquery())
    total_n = (select(func.count()).select_from(Subscription)
               .where(Subscription.service_id == Service.id)
               .correlate(Service).scalar_subquery())
    rev = (select(func.coalesce(func.sum(Payment.amount), 0))
           .select_from(Payment)
           .join(Subscription, Payment.subscription_id == Subscription.id)
           .where(Subscription.service_id == Service.id,
                  Payment.status == PaymentStatus.DONE)
           .correlate(Service).scalar_subquery())
    rows = (await db.execute(
        select(Service.id, Service.name, open_n, total_n, rev)
        .order_by(rev.desc(), Service.name))).all()
    return [{"id": sid, "name": name, "open_count": int(o), "total_count": int(t),
             "revenue": int(r)} for sid, name, o, t, r in rows]


async def _rails(db, scope, now) -> tuple[list, list, list]:
    """(최근 결제, 미수 구독, 만료 임박) 우측 레일."""
    recent_q = (select(Payment, Subscription)
                .join(Subscription, Payment.subscription_id == Subscription.id)
                .order_by(Payment.requested_at.desc()).limit(8))
    recent = (await db.execute(
        _scoped(recent_q, scope, Subscription.service_id))).all()

    pd_q = (select(Subscription)
            .where(Subscription.status.in_((SubscriptionStatus.PAST_DUE,
                                            SubscriptionStatus.SUSPENDED)))
            .order_by(Subscription.next_billing_at.asc().nullslast()).limit(5))
    past_due = list((await db.scalars(
        _scoped(pd_q, scope, Subscription.service_id))).all())

    exp_q = (select(Subscription)
             .where(_open_subs_cond(now)
                    | (Subscription.status == SubscriptionStatus.CANCELED),
                    Subscription.current_period_end >= now,
                    Subscription.current_period_end < now + relativedelta(days=7))
             .order_by(Subscription.current_period_end.asc()).limit(5))
    expiring = list((await db.scalars(
        _scoped(exp_q, scope, Subscription.service_id))).all())
    return recent, past_due, expiring
```

(c) `build_dashboard` 전체를 조합 함수로 교체:

```python
async def build_dashboard(db: AsyncSession, scope: list[uuid.UUID] | None) -> DashboardData:
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    data = DashboardData()

    # 상태별 구독 수 (카드/도넛 공용)
    counts = {s: 0 for s in _STATUS_ORDER}
    rows = (await db.execute(_scoped(
        select(Subscription.status, func.count()).group_by(Subscription.status),
        scope, Subscription.service_id))).all()
    for status, n in rows:
        counts[status] = n

    data.cards = await _month_cards(db, scope, counts, now, month_start)
    data.revenue_months, data.trend_months, data.subs_months = \
        await _series_12m(db, scope, now, month_start)
    data.status_breakdown = [
        {"label": _STATUS_KO[s], "value": counts[s], "color": _STATUS_COLOR[s],
         "href": f"/admin/subscriptions?status={s}"}
        for s in _STATUS_ORDER if counts[s] > 0
    ] or [{"label": "데이터 없음", "value": 1, "color": "var(--black-10)", "href": None}]
    data.totals = await _grand_totals(db, scope)
    if scope is None:
        data.service_totals = await _service_totals(db, now)
    data.recent, data.past_due, data.expiring = await _rails(db, scope, now)
    return data
```

(기존 6개월 결제 시리즈(`pay_series`)·`service_ranking` 블록 삭제)

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/integration/test_dashboard.py -x -q` → 전체 PASS.
이 시점에 dashboard.html이 `d.payment_months`/`d.service_ranking`을 참조해 e2e가 깨진다 — Task 4에서 템플릿을 고치므로 여기서는 통합 테스트만 확인. 단 `uv run pytest tests/integration tests/unit -q`는 전체 통과해야 함.

- [ ] **Step 5: 커밋**

```bash
git add app/services/dashboard.py tests/integration/test_dashboard.py
git commit -m "feat(dashboard): 12개월 시리즈(신규/취소/스냅샷) + 누적 토탈 + 레일 집계"
```

---

### Task 4: 템플릿 개편 (대시보드 + 차트 매크로)

**Files:**
- Modify: `app/admin/templates/_charts.html` (bars 라벨 파라미터, donut 범례 링크)
- Modify: `app/admin/templates/dashboard.html`
- Test: `tests/e2e/test_dashboard_page.py` (신설)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_dashboard_page.py` 신설:

```python
"""대시보드 화면 e2e (요청 008)."""
from datetime import timedelta

from app.core.clock import utcnow
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login


async def _seed(db, cipher):
    svc, _, _ = await create_service(db, cipher, name="대시보드서비스")
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="dash-act",
                              status="ACTIVE")
    await create_subscription(db, cipher, svc, plan, external_user_id="dash-pd",
                              status="PAST_DUE")
    await create_subscription(db, cipher, svc, plan, external_user_id="dash-exp",
                              status="ACTIVE",
                              period_end=utcnow() + timedelta(days=2))
    return svc


async def test_dashboard_cards_with_links(client, db, redis_client, cipher):
    await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    for label in ["전체 구독", "신규 구독", "이번달 매출", "이번달 미결제",
                  "구독 취소", "결제 성공률", "ARPU", "체험 구독"]:
        assert label in html
    assert 'href="/admin/subscriptions?status=TRIAL"' in html
    assert "/admin/payments?status=DONE&amp;month=" in html or \
           "/admin/payments?status=DONE&month=" in html


async def test_dashboard_rails_and_totals(client, db, redis_client, cipher):
    svc = await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    # 레일 3섹션
    assert "최근 결제" in html and "미수 구독" in html and "만료 임박" in html
    assert "dash-pd" in html and "dash-exp" in html
    # 토탈 테이블 (admin 전용) + 서비스 상세 링크
    assert "서비스별 누적" in html
    assert f'href="/admin/services/{svc.id}"' in html
    # 12개월 차트 3종 제목
    assert "최근 12개월 매출" in html
    assert "신규 vs 취소" in html
    assert "전체 구독수 추이" in html


async def test_dashboard_manager_scope_no_totals_table(client, db, redis_client, cipher):
    svc = await _seed(db, cipher)
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, mgr.email, pw)
    html = (await client.get("/admin")).text
    assert "서비스별 누적" not in html      # admin 전용 테이블 미노출
    assert "전체 구독" in html              # 카드는 스코프 집계로 노출


async def test_dashboard_donut_legend_links(client, db, redis_client, cipher):
    await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    assert 'href="/admin/subscriptions?status=ACTIVE"' in html
    assert 'href="/admin/subscriptions?status=PAST_DUE"' in html
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_dashboard_page.py -x -q`
Expected: FAIL (구 템플릿 — `d.payment_months` 참조로 500 또는 라벨 부재)

- [ ] **Step 3: `_charts.html` 매크로 확장**

(a) `bars` 매크로 시그니처/범례를 파라미터화 — `{%- macro bars(series, height=200) -%}` 를 `{%- macro bars(series, height=200, label_a='성공', label_b='실패', color_a='var(--accent-mint)', color_b='var(--accent-red)') -%}` 로 바꾸고, 본문의 `fill="var(--accent-mint)"` → `fill="{{ color_a }}"`, `fill="var(--accent-red)"` → `fill="{{ color_b }}"`, legend 블록을:

```html
  <div class="legend">
    <span><i style="background:{{ color_a }}"></i>{{ label_a }}</span>
    <span><i style="background:{{ color_b }}"></i>{{ label_b }}</span>
  </div>
```

(b) `donut` 매크로 범례에 링크 — 범례 `div.kv` 안의 라벨 span을:

```html
        <span style="display:flex;align-items:center;gap:8px">
          <i style="width:9px;height:9px;border-radius:9999px;display:inline-block;background:{{ it.color }}"></i>
          {%- if it.href -%}<a href="{{ it.href }}" style="color:inherit">{{ it.label }}</a>
          {%- else -%}{{ it.label }}{%- endif -%}
        </span>
```

(items에 href가 없을 수도 있으므로 `it.href` 미존재 시에도 동작 — Jinja는 미정의 속성을 Undefined로 처리해 falsy)

- [ ] **Step 4: `dashboard.html` 개편** — content/rail 블록 전체를 다음으로 교체:

```html
{% extends "base.html" %}
{% import "_charts.html" as charts %}
{% block title %}대시보드{% endblock %}
{% block crumb %}대시보드{% endblock %}
{% block content %}
<div class="page-head"><h1>대시보드</h1></div>

{# --- 통계 카드 8종 (이번달 기준, 클릭 시 상세 이동) --- #}
<div class="stats">
  {% for c in d.cards %}
  {% if c.href %}<a class="stat stat-tint-{{ c.tint }}" href="{{ c.href }}" style="color:inherit">
  {% else %}<div class="stat stat-tint-{{ c.tint }}">{% endif %}
    <span class="stat-label">{{ c.label }}</span>
    <div class="stat-row">
      <span class="stat-value">{{ c.value }}</span>
      {% if c.delta %}
      <span class="stat-delta {{ 'delta-up' if c.up else 'delta-down' }}">
        {{ c.delta }}<span data-lucide="{{ 'trending-up' if c.up else 'trending-down' }}"></span>
      </span>
      {% endif %}
    </div>
  {% if c.href %}</a>{% else %}</div>{% endif %}
  {% endfor %}
</div>

{# --- 12개월 매출 + 상태 도넛 --- #}
<div class="grid grid-3">
  <div class="block">
    <div class="block-head"><h2>최근 12개월 매출</h2><span class="muted" style="font-size:12px">DONE 결제 합계</span></div>
    {{ charts.area(d.revenue_months) }}
  </div>
  <div class="block">
    <div class="block-head"><h2>구독 상태</h2></div>
    {{ charts.donut(d.status_breakdown) }}
  </div>
</div>

{# --- 12개월 신규 vs 취소 + 전체 구독수 추이 --- #}
<div class="grid grid-3">
  <div class="block">
    <div class="block-head"><h2>신규 vs 취소 (12개월)</h2><span class="muted" style="font-size:12px">취소 = 사용자취소+결제만료</span></div>
    {{ charts.bars(d.trend_months, label_a='신규', label_b='취소',
                   color_a='var(--accent-indigo)', color_b='var(--accent-orange)') }}
  </div>
  <div class="block">
    <div class="block-head"><h2>전체 구독수 추이</h2><span class="muted" style="font-size:12px">월말 시점 근사</span></div>
    {{ charts.area(d.subs_months) }}
  </div>
</div>

{# --- 서비스별 누적 (SYSTEM_ADMIN 전용) --- #}
{% if is_admin and d.service_totals %}
<div class="block">
  <div class="block-head">
    <h2>서비스별 누적</h2>
    <span class="muted" style="font-size:12px">
      누적 구독 {{ "{:,}".format(d.totals.sub_count) }}건 · 누적 매출 {{ "{:,}".format(d.totals.revenue) }}원
    </span>
  </div>
  <table>
    <thead><tr><th>서비스</th><th>현재 구독</th><th>누적 구독</th><th>누적 매출</th></tr></thead>
    <tbody>
    {% for r in d.service_totals %}
      <tr style="cursor:pointer" onclick="location.href='/admin/services/{{ r.id }}'">
        <td><a href="/admin/services/{{ r.id }}">{{ r.name }}</a></td>
        <td>{{ "{:,}".format(r.open_count) }}</td>
        <td class="muted">{{ "{:,}".format(r.total_count) }}</td>
        <td style="font-weight:600">{{ "{:,}".format(r.revenue) }}원</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
{% endblock %}

{% block rail %}
<aside class="sidebar" style="width:300px;border-right:none;border-left:1px solid var(--border)">
  <h3 style="font:var(--t-h2);margin:4px 8px 14px">최근 결제</h3>
  {% for p, sub in d.recent %}
  <a href="/admin/subscriptions/{{ sub.id }}"
     style="display:flex;gap:10px;align-items:flex-start;padding:8px;border-radius:12px;transition:background .12s"
     onmouseover="this.style.background='var(--black-4)'" onmouseout="this.style.background='transparent'">
    <span style="flex:none;width:32px;height:32px;border-radius:10px;display:flex;align-items:center;justify-content:center;
                 background:{{ 'var(--accent-mint)' if p.status=='DONE' else 'var(--accent-red)' if p.status=='FAILED' else 'var(--accent-orange)' }}">
      <span data-lucide="credit-card" style="width:16px;height:16px"></span>
    </span>
    <span style="min-width:0;flex:1">
      <span style="display:block;font-size:13px">{{ sub.external_user_id }} · {{ "{:,}".format(p.amount) }}원</span>
      <span style="display:flex;align-items:center;gap:6px;margin-top:2px">
        <span class="badge badge-{{ p.status }}" style="padding:1px 8px;font-size:11px">{{ p.status }}</span>
        <span style="font-size:12px;color:var(--black-40)">{{ p.requested_at.strftime('%m-%d %H:%M') }}</span>
      </span>
    </span>
  </a>
  {% else %}
  <p class="muted" style="padding:8px;font-size:13px">결제 내역이 없습니다</p>
  {% endfor %}

  <h3 style="font:var(--t-h2);margin:18px 8px 14px">미수 구독</h3>
  {% for s in d.past_due %}
  <a href="/admin/subscriptions/{{ s.id }}" style="display:flex;justify-content:space-between;gap:8px;padding:8px;border-radius:12px"
     onmouseover="this.style.background='var(--black-4)'" onmouseout="this.style.background='transparent'">
    <span style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ s.external_user_id }}</span>
    <span class="badge badge-{{ s.status }}" style="padding:1px 8px;font-size:11px;flex:none">{{ s.status }}</span>
  </a>
  {% else %}
  <p class="muted" style="padding:8px;font-size:13px">미수 구독이 없습니다</p>
  {% endfor %}

  <h3 style="font:var(--t-h2);margin:18px 8px 14px">만료 임박 <span class="muted" style="font-size:12px;font-weight:400">7일 이내</span></h3>
  {% for s in d.expiring %}
  <a href="/admin/subscriptions/{{ s.id }}" style="display:flex;justify-content:space-between;gap:8px;padding:8px;border-radius:12px"
     onmouseover="this.style.background='var(--black-4)'" onmouseout="this.style.background='transparent'">
    <span style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ s.external_user_id }}</span>
    <span style="font-size:12px;color:var(--black-40);flex:none">{{ s.current_period_end.strftime('%m-%d') }}</span>
  </a>
  {% else %}
  <p class="muted" style="padding:8px;font-size:13px">만료 임박 구독이 없습니다</p>
  {% endfor %}
</aside>
{% endblock %}
```

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/e2e/test_dashboard_page.py tests/e2e/test_admin_flows.py tests/e2e/test_htmx_partials.py -q` → 전체 PASS. 이어서 `uv run pytest tests/e2e -q` 회귀 확인.

- [ ] **Step 6: 커밋**

```bash
git add app/admin/templates/_charts.html app/admin/templates/dashboard.html tests/e2e/test_dashboard_page.py
git commit -m "feat(dashboard): 화면 개편 — 카드 링크/차트 3종/누적 테이블/레일 3섹션"
```

---

### Task 5: 전체 검증

- [ ] **Step 1: 전체 테스트** — Run: `uv run pytest -q`
Expected: 전체 PASS (기존 349 + 신규 ~14)

- [ ] **Step 2: 수동 점검 포인트**

- `grep -n "payment_months\|service_ranking" app/ -r --include="*.py" --include="*.html"` → 0건 (잔여 참조 정리 확인)
- 카드 href의 `&` 가 템플릿에서 `&amp;`로 이스케이프되어도 브라우저 동작 동일 — e2e가 둘 다 허용하는지 확인됨

- [ ] **Step 3: 커밋(잔여 수정 시에만)**

```bash
git add -A app tests
git commit -m "test: 대시보드 개편 잔여 정리"
```

## 변경하지 않는 것 (스펙 동일)

- 외부 API(/api/v1), 알림, 스케줄러, 모델/마이그레이션
- 기존 구독/서비스/감사 리스트 화면 (payments 월 필터 제외)
