# 날짜 범위 검색 + 정산 메뉴 구현 계획 (요청 009)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 구독/결제 리스트에 날짜 범위 필터를 추가하고(결제는 기존 month 필터 교체), 승인일 기준 DONE 결제를 집계하는 정산 메뉴를 신설한다.

**Architecture:** `pagination.py`에 공용 `date_range` 헬퍼, `_list.html` toolbar 매크로에 `date_inputs` 파라미터 추가로 htmx 툴바를 유지한 채 3화면에 일관 적용. 정산은 서비스 계층(`settlement_summary`) + 단일 라우트(service_id 유무로 전체/서비스별 모드 전환).

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Jinja2, pytest

**스펙:** `docs/superpowers/specs/2026-06-08-date-range-and-settlement-design.md`
**테스트 실행:** `uv run pytest <경로> -q`

## 파일 구조

- `app/admin/pagination.py` — `date_range(pp)` 공용 헬퍼 (Task 1)
- `app/admin/templates/_list.html` — toolbar 매크로 `date_inputs` 파라미터 (Task 2)
- `app/admin/routes/subscriptions.py` — 구독 from/to + 결제 month→from/to (Task 2~3)
- `app/admin/templates/subscriptions/_table.html`, `payments/list.html` (Task 2~3)
- `app/services/dashboard.py` — 카드 href month→from/to (Task 3)
- `app/services/settlement.py` — 신설: 정산 집계 (Task 4)
- `app/admin/routes/settlement.py`, `app/admin/templates/settlement/index.html`, `base.html` nav (Task 4)
- 테스트: `tests/unit/test_admin_helpers.py`, `tests/e2e/test_admin_operations.py`,
  `tests/e2e/test_dashboard_page.py`, `tests/integration/test_settlement.py`(신설),
  `tests/e2e/test_settlement_page.py`(신설)

---

### Task 1: 공용 `date_range` 헬퍼

**Files:**
- Modify: `app/admin/pagination.py`
- Test: `tests/unit/test_admin_helpers.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/unit/test_admin_helpers.py` 끝에 추가:

```python
def test_date_range_parses_pair():
    from datetime import datetime, timezone
    from app.admin.pagination import PageParams, date_range
    pp = PageParams(filters={"from": "2026-01-10", "to": "2026-01-20"})
    start, end = date_range(pp)
    assert start == datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert end == datetime(2026, 1, 21, tzinfo=timezone.utc)  # 익일 0시(반개구간)


def test_date_range_open_ended():
    from app.admin.pagination import PageParams, date_range
    pp = PageParams(filters={"from": "2026-01-10"})
    start, end = date_range(pp)
    assert start is not None and end is None
    pp2 = PageParams(filters={"to": "2026-01-20"})
    start2, end2 = date_range(pp2)
    assert start2 is None and end2 is not None


def test_date_range_invalid_removed_from_filters():
    from app.admin.pagination import PageParams, date_range
    pp = PageParams(filters={"from": "bogus", "to": "2026-01-20"})
    start, end = date_range(pp)
    assert start is None and end is not None
    assert "from" not in pp.filters       # 페이저 링크 오염 방지
    assert pp.filters.get("to") == "2026-01-20"
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_admin_helpers.py -k date_range -x -q`
Expected: FAIL — ImportError(`date_range` 없음)

- [ ] **Step 3: 구현** — `app/admin/pagination.py` 상단 import에 추가:

```python
from datetime import datetime, timedelta, timezone
```

파일 끝(`count_of` 아래)에 추가:

```python
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
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/unit/test_admin_helpers.py -q` → 전체 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/admin/pagination.py tests/unit/test_admin_helpers.py
git commit -m "feat(admin): 날짜 범위 공용 헬퍼 date_range"
```

---

### Task 2: toolbar 매크로 date_inputs + 구독리스트 구독일 범위

**Files:**
- Modify: `app/admin/templates/_list.html` (toolbar 매크로)
- Modify: `app/admin/routes/subscriptions.py` (subscriptions_list)
- Modify: `app/admin/templates/subscriptions/_table.html`
- Test: `tests/e2e/test_admin_operations.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_admin_operations.py` 끝에 추가 (파일에 factories/admin_login import 있음):

```python
async def test_subscriptions_date_range_filter(client, db, redis_client, cipher):
    """구독일(created_at) 시작~끝 범위 필터 — 경계 포함."""
    from datetime import datetime, timezone
    from sqlalchemy import update as sa_update
    from app.models import Subscription
    svc, _, _ = await create_service(db, cipher, name="sub-range-svc")
    plan = await create_plan(db, svc)
    s_in = await create_subscription(db, cipher, svc, plan, external_user_id="rng-in")
    s_out = await create_subscription(db, cipher, svc, plan, external_user_id="rng-out")
    # created_at은 server_default — 직접 update로 과거 날짜 부여
    await db.execute(sa_update(Subscription).where(Subscription.id == s_in.id)
                     .values(created_at=datetime(2026, 2, 15, tzinfo=timezone.utc)))
    await db.execute(sa_update(Subscription).where(Subscription.id == s_out.id)
                     .values(created_at=datetime(2026, 3, 5, tzinfo=timezone.utc)))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)

    resp = await client.get("/admin/subscriptions?from=2026-02-01&to=2026-02-28")
    assert "rng-in" in resp.text and "rng-out" not in resp.text
    # 한쪽만 입력(열린 범위)
    resp = await client.get("/admin/subscriptions?from=2026-03-01")
    assert "rng-out" in resp.text and "rng-in" not in resp.text
    # date input 렌더
    assert 'type="date"' in resp.text
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py::test_subscriptions_date_range_filter -x -q`
Expected: FAIL (필터 미적용)

- [ ] **Step 3: toolbar 매크로 확장** — `app/admin/templates/_list.html`의 toolbar 매크로 시그니처를:

```jinja
{%- macro toolbar(action, pp, placeholder='검색', extra_selects=None, target=None, date_inputs=None) -%}
```

으로 바꾸고, `{%- if extra_selects -%}...{%- endif -%}` 블록 바로 아래에 추가:

```jinja
  {%- if date_inputs -%}
    {%- for name, value in date_inputs -%}
    <input type="date" name="{{ name }}" value="{{ value }}" style="width:auto"
           onchange="this.form.requestSubmit()">
    {%- endfor -%}
  {%- endif -%}
```

- [ ] **Step 4: 라우트 수정** — `app/admin/routes/subscriptions.py`:

import에 `date_range` 추가:

```python
from app.admin.pagination import PageParams, date_range, paginate
```

`subscriptions_list`에서 `filter_keys=("status", "service_id")` → `filter_keys=("status", "service_id", "from", "to")`. status 필터 적용 아래에 추가:

```python
    start, end = date_range(pp)
    if start:
        base = base.where(Subscription.created_at >= start)
    if end:
        base = base.where(Subscription.created_at < end)
```

`render_list` 호출에 추가: `from_filter=pp.filters.get("from", ""), to_filter=pp.filters.get("to", "")`

- [ ] **Step 5: 템플릿 수정** — `app/admin/templates/subscriptions/_table.html`의 `L.toolbar` 호출을:

```jinja
{{ L.toolbar('/admin/subscriptions', pp, '사용자 ID 검색', [
   ('service_id', service_options, service_filter),
   ('status', [('','전체 상태'),('TRIAL','TRIAL'),('ACTIVE','ACTIVE'),('PAST_DUE','PAST_DUE'),('SUSPENDED','SUSPENDED'),('CANCELED','CANCELED'),('EXPIRED','EXPIRED')], status_filter)],
   target='list-subs',
   date_inputs=[('from', from_filter), ('to', to_filter)]) }}
```

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py tests/e2e/test_htmx_partials.py -q` → 전체 PASS (htmx 부분 갱신 회귀 포함)

- [ ] **Step 7: 커밋**

```bash
git add app/admin/templates/_list.html app/admin/routes/subscriptions.py app/admin/templates/subscriptions/_table.html tests/e2e/test_admin_operations.py
git commit -m "feat(subscriptions): 구독일 시작~끝 범위 검색"
```

---

### Task 3: 결제리스트 month → 결제일 범위 교체 + 대시보드 링크 갱신

**Files:**
- Modify: `app/admin/routes/subscriptions.py` (payments_list — `_month_range` 제거)
- Modify: `app/admin/templates/payments/list.html`
- Modify: `app/services/dashboard.py` (`_month_cards` href 3개)
- Test: `tests/e2e/test_admin_operations.py` (month 테스트 교체), `tests/e2e/test_dashboard_page.py`, `tests/integration/test_dashboard.py`

- [ ] **Step 1: 테스트 교체/갱신**

(a) `tests/e2e/test_admin_operations.py`의 `test_payments_month_filter`를 다음으로 **교체**:

```python
async def test_payments_date_range_filter(client, db, redis_client, cipher):
    """결제일(requested_at) 시작~끝 범위 필터 (기존 month 필터 대체)."""
    from datetime import datetime, timezone
    from app.models import Payment
    svc, _, _ = await create_service(db, cipher, name="pay-range-svc")
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="pr-user")
    db.add(Payment(subscription_id=sub.id, order_id="pr-old", amount=1000,
                   payment_type="FIRST", status="DONE", idempotency_key="pr-old",
                   requested_at=datetime(2025, 3, 10, tzinfo=timezone.utc)))
    db.add(Payment(subscription_id=sub.id, order_id="pr-new", amount=2000,
                   payment_type="RENEWAL", status="DONE", idempotency_key="pr-new",
                   requested_at=datetime(2025, 4, 10, tzinfo=timezone.utc)))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)

    resp = await client.get("/admin/payments?from=2025-03-01&to=2025-03-31")
    assert "pr-old" in resp.text and "pr-new" not in resp.text
    # 형식 오류는 무시(전체)
    resp = await client.get("/admin/payments?from=bogus")
    assert "pr-old" in resp.text and "pr-new" in resp.text
    # month 파라미터는 더 이상 동작하지 않음(전체 표시)
    resp = await client.get("/admin/payments?month=2025-03")
    assert "pr-old" in resp.text and "pr-new" in resp.text
```

(b) `tests/e2e/test_dashboard_page.py`의 `test_dashboard_cards_with_links`에서 month 링크 검증 2줄을:

```python
    assert "/admin/payments?status=DONE&amp;from=" in html or \
           "/admin/payments?status=DONE&from=" in html
```

으로 교체.

(c) `tests/integration/test_dashboard.py`에 카드 href 형식 검증 추가 (`test_cards_trial_count_and_href` 아래):

```python
async def test_cards_payment_hrefs_use_date_range(db, cipher):
    """매출/미결제/성공률 카드 링크가 from/to 날짜 범위를 사용한다(month 아님)."""
    data = await build_dashboard(db, None)
    rev = _card(data, "이번달 매출")
    assert "from=" in rev.href and "to=" in rev.href and "month=" not in rev.href
    fail = _card(data, "이번달 미결제")
    assert fail.href.startswith("/admin/payments?status=FAILED&from=")
    rate = _card(data, "결제 성공률")
    assert rate.href.startswith("/admin/payments?from=")
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py::test_payments_date_range_filter tests/integration/test_dashboard.py::test_cards_payment_hrefs_use_date_range -x -q`
Expected: FAIL

- [ ] **Step 3: payments_list 수정** — `app/admin/routes/subscriptions.py`:

`_month_range` 함수와 상단의 `from datetime import datetime, timezone` / `from dateutil.relativedelta import relativedelta` import 제거(다른 사용처 없으면). `payments_list`의 month 블록을 다음으로 교체:

```python
    pp = PageParams.from_request(request, sortable=set(_PAY_SORT),
                                 default_sort="requested_at",
                                 filter_keys=("status", "from", "to"))
```

```python
    start, end = date_range(pp)
    if start:
        base = base.where(Payment.requested_at >= start)
    if end:
        base = base.where(Payment.requested_at < end)
```

`render` 호출에서 `month_filter=...` 제거, 추가: `from_filter=pp.filters.get("from", ""), to_filter=pp.filters.get("to", "")`

- [ ] **Step 4: payments 템플릿 수정** — `app/admin/templates/payments/list.html`의 직접 폼을 toolbar 매크로로 회귀(일관성):

```jinja
{{ L.toolbar('/admin/payments', pp, '주문번호·사용자 검색',
   [('status', [('','전체 상태'),('DONE','DONE'),('FAILED','FAILED'),('PENDING','PENDING'),('CANCELED','CANCELED')], status_filter)],
   date_inputs=[('from', from_filter), ('to', to_filter)]) }}
```

(직접 `<form>` 블록 전체를 위 한 줄로 교체)

- [ ] **Step 5: 대시보드 href 갱신** — `app/services/dashboard.py` `_month_cards`에서:

```python
    month_qs = now.strftime("%Y-%m")
```
를 다음으로 교체:

```python
    range_qs = f"from={month_start.strftime('%Y-%m-%d')}&to={now.strftime('%Y-%m-%d')}"
```

카드 href 3개 교체:
- 매출: `f"/admin/payments?status=DONE&{range_qs}"`
- 미결제: `f"/admin/payments?status=FAILED&{range_qs}"`
- 성공률: `f"/admin/payments?{range_qs}"`

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py tests/e2e/test_dashboard_page.py tests/integration/test_dashboard.py -q` → 전체 PASS

- [ ] **Step 7: 커밋**

```bash
git add app/admin/routes/subscriptions.py app/admin/templates/payments/list.html app/services/dashboard.py tests/e2e/test_admin_operations.py tests/e2e/test_dashboard_page.py tests/integration/test_dashboard.py
git commit -m "feat(payments): 월 필터를 결제일 범위로 교체 + 대시보드 링크 갱신"
```

---

### Task 4: 정산 메뉴

**Files:**
- Create: `app/services/settlement.py`
- Create: `app/admin/routes/settlement.py`
- Create: `app/admin/templates/settlement/index.html`
- Modify: `app/admin/templates/base.html` (nav), `app/admin/__init__.py` 또는 라우터 등록 지점
- Test: `tests/integration/test_settlement.py` (신설), `tests/e2e/test_settlement_page.py` (신설)

- [ ] **Step 0: 라우터 등록** — `app/admin/__init__.py`의 `from app.admin.routes import (...)` 블록(27행 부근)에 `settlement` 추가하고, `router.include_router(subscriptions.router)`(41행) 아래에 `router.include_router(settlement.router)` 추가. (dashboard.router 포함 여부 등 기존 나열과 알파벳/논리 순서를 맞춰 배치)

- [ ] **Step 1: 통합 테스트 신설** — `tests/integration/test_settlement.py`:

```python
"""정산 집계 통합 테스트 (요청 009)."""
from datetime import datetime, timezone

from app.models import Payment
from app.services.settlement import settlement_summary
from tests.factories import create_plan, create_service, create_subscription

UTC = timezone.utc


async def _done(db, sub, amount, approved, *, order):
    db.add(Payment(subscription_id=sub.id, order_id=order, amount=amount,
                   payment_type="RENEWAL", status="DONE", idempotency_key=order,
                   requested_at=approved, approved_at=approved))
    await db.commit()


async def _seed_two_services(db, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="정산A")
    svc_b, _, _ = await create_service(db, cipher, name="정산B")
    plan_a = await create_plan(db, svc_a)
    plan_b = await create_plan(db, svc_b)
    sub_a = await create_subscription(db, cipher, svc_a, plan_a, external_user_id="sa")
    sub_b = await create_subscription(db, cipher, svc_b, plan_b, external_user_id="sb")
    await _done(db, sub_a, 10000, datetime(2026, 5, 10, tzinfo=UTC), order="st-a1")
    await _done(db, sub_a, 20000, datetime(2026, 5, 20, tzinfo=UTC), order="st-a2")
    await _done(db, sub_b, 5000, datetime(2026, 5, 15, tzinfo=UTC), order="st-b1")
    # 기간 밖 + FAILED는 제외 검증용
    await _done(db, sub_b, 99999, datetime(2026, 6, 1, tzinfo=UTC), order="st-b-out")
    db.add(Payment(subscription_id=sub_b.id, order_id="st-b-fail", amount=7777,
                   payment_type="RENEWAL", status="FAILED", idempotency_key="st-b-fail",
                   requested_at=datetime(2026, 5, 16, tzinfo=UTC)))
    await db.commit()
    return svc_a, svc_b


async def test_summary_groups_by_service_amount_desc(db, cipher):
    svc_a, svc_b = await _seed_two_services(db, cipher)
    count, amount, rows = await settlement_summary(
        db, None, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    assert count == 3 and amount == 35000          # 기간 밖/FAILED 제외
    assert [r.service_name for r in rows] == ["정산A", "정산B"]  # 금액 내림차순
    assert rows[0].count == 2 and rows[0].amount == 30000
    assert rows[1].count == 1 and rows[1].amount == 5000


async def test_summary_boundary_half_open(db, cipher):
    """[start, end) 반개구간 — end 정각 결제는 제외."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="bd")
    await _done(db, sub, 1000, datetime(2026, 5, 1, tzinfo=UTC), order="bd-start")
    await _done(db, sub, 2000, datetime(2026, 6, 1, tzinfo=UTC), order="bd-end")
    count, amount, _ = await settlement_summary(
        db, None, datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    assert count == 1 and amount == 1000


async def test_summary_scope_limits_services(db, cipher):
    svc_a, svc_b = await _seed_two_services(db, cipher)
    count, amount, rows = await settlement_summary(
        db, [svc_a.id], datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC))
    assert count == 2 and amount == 30000
    assert [r.service_name for r in rows] == ["정산A"]


async def test_summary_open_range(db, cipher):
    """start/end None이면 해당 방향 무제한."""
    svc_a, _ = await _seed_two_services(db, cipher)
    count, amount, _ = await settlement_summary(db, None, None, None)
    assert amount == 35000 + 99999                  # FAILED만 제외
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_settlement.py -x -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 서비스 계층 구현** — `app/services/settlement.py`:

```python
"""정산 집계 — 기간 내 DONE 결제(approved_at 기준)를 서비스별로 합산."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Payment, PaymentStatus, Service, Subscription


@dataclass
class SettlementRow:
    service_id: uuid.UUID
    service_name: str
    count: int      # DONE 결제 건수
    amount: int     # 합계 금액(KRW)


async def settlement_summary(db: AsyncSession, scope: list[uuid.UUID] | None,
                             start: datetime | None, end: datetime | None,
                             ) -> tuple[int, int, list[SettlementRow]]:
    """(총 건수, 총 금액, 서비스별 집계 — 금액 내림차순). 반개구간 [start, end)."""
    amount_sum = func.coalesce(func.sum(Payment.amount), 0)
    q = (select(Service.id, Service.name, func.count(Payment.id), amount_sum)
         .select_from(Payment)
         .join(Subscription, Payment.subscription_id == Subscription.id)
         .join(Service, Subscription.service_id == Service.id)
         .where(Payment.status == PaymentStatus.DONE)
         .group_by(Service.id, Service.name)
         .order_by(amount_sum.desc(), Service.name))
    if start:
        q = q.where(Payment.approved_at >= start)
    if end:
        q = q.where(Payment.approved_at < end)
    if scope is not None:
        q = q.where(Subscription.service_id.in_(scope))
    rows = [SettlementRow(sid, name, int(c), int(a))
            for sid, name, c, a in (await db.execute(q)).all()]
    return sum(r.count for r in rows), sum(r.amount for r in rows), rows
```

- [ ] **Step 4: 통합 테스트 통과 확인** — Run: `uv run pytest tests/integration/test_settlement.py -x -q` → 전체 PASS

- [ ] **Step 5: e2e 테스트 신설** — `tests/e2e/test_settlement_page.py`:

```python
"""정산 화면 e2e (요청 009)."""
from datetime import datetime, timezone

from app.models import Payment
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login

UTC = timezone.utc


async def _seed(db, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="정산서비스A")
    svc_b, _, _ = await create_service(db, cipher, name="정산서비스B")
    plan_a = await create_plan(db, svc_a)
    plan_b = await create_plan(db, svc_b)
    sub_a = await create_subscription(db, cipher, svc_a, plan_a, external_user_id="se-a")
    sub_b = await create_subscription(db, cipher, svc_b, plan_b, external_user_id="se-b")
    when = datetime(2026, 5, 10, tzinfo=UTC)
    db.add(Payment(subscription_id=sub_a.id, order_id="se-pay-a", amount=10000,
                   payment_type="RENEWAL", status="DONE", idempotency_key="se-pay-a",
                   requested_at=when, approved_at=when))
    db.add(Payment(subscription_id=sub_b.id, order_id="se-pay-b", amount=5000,
                   payment_type="RENEWAL", status="DONE", idempotency_key="se-pay-b",
                   requested_at=when, approved_at=when))
    await db.commit()
    return svc_a, svc_b, sub_a


async def test_settlement_all_mode_lists_services(client, db, redis_client, cipher):
    svc_a, svc_b, _ = await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get(
        "/admin/settlement?from=2026-05-01&to=2026-05-31")).text
    assert "15,000" in html                        # 전체 합계
    assert "정산서비스A" in html and "정산서비스B" in html
    # 상세보기 링크가 기간을 유지한 채 service_id를 채움
    assert f"service_id={svc_a.id}" in html
    assert "from=2026-05-01" in html and "to=2026-05-31" in html
    assert "승인일" in html                         # 기준 안내 문구


async def test_settlement_service_mode_lists_payments(client, db, redis_client, cipher):
    svc_a, _, sub_a = await _seed(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get(
        f"/admin/settlement?from=2026-05-01&to=2026-05-31&service_id={svc_a.id}")).text
    assert "10,000" in html                        # 해당 서비스 합계
    assert "se-pay-a" in html                      # 결제 건별 행
    assert "se-pay-b" not in html                  # 타 서비스 제외
    assert f'href="/admin/subscriptions/{sub_a.id}"' in html  # 상세보기 → 구독 상세


async def test_settlement_default_period_renders(client, db, redis_client, cipher):
    """파라미터 없으면 이번달 1일~오늘 기본값으로 렌더."""
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    resp = await client.get("/admin/settlement")
    assert resp.status_code == 200
    from app.core.clock import utcnow
    assert utcnow().strftime("%Y-%m-01") in resp.text   # from 기본값 렌더


async def test_settlement_manager_scope(client, db, redis_client, cipher):
    svc_a, svc_b, _ = await _seed(db, cipher)
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc_a.id)
    await admin_login(client, mgr.email, pw)
    html = (await client.get(
        "/admin/settlement?from=2026-05-01&to=2026-05-31")).text
    assert "정산서비스A" in html and "정산서비스B" not in html
    assert "10,000" in html and "15,000" not in html    # 스코프 합계
    # 타 서비스 service_id 직접 요청 → 404
    resp = await client.get(f"/admin/settlement?service_id={svc_b.id}")
    assert resp.status_code == 404


async def test_settlement_nav_menu(client, db, redis_client, cipher):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    html = (await client.get("/admin")).text
    assert '/admin/settlement' in html and "정산" in html
```

- [ ] **Step 6: 실패 확인** — Run: `uv run pytest tests/e2e/test_settlement_page.py -x -q` → FAIL (404)

- [ ] **Step 7: 라우트 구현** — `app/admin/routes/settlement.py`:

```python
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_any
from app.admin.pagination import PageParams, date_range, paginate
from app.api.deps import get_db
from app.core.clock import utcnow
from app.core.errors import NotFoundError
from app.models import Payment, PaymentStatus, Service, Subscription
from app.services.settlement import settlement_summary

router = APIRouter()

_SETTLE_SORT = {"approved_at": Payment.approved_at, "amount": Payment.amount}


@router.get("/settlement")
async def settlement_view(request: Request,
                          ctx: AdminContext = Depends(require_any),
                          db: AsyncSession = Depends(get_db)):
    pp = PageParams.from_request(request, sortable=set(_SETTLE_SORT),
                                 default_sort="approved_at",
                                 filter_keys=("from", "to", "service_id"))
    # 기간 파라미터가 전혀 없으면 기본값: 이번달 1일~오늘
    if "from" not in request.query_params and "to" not in request.query_params:
        now = utcnow()
        pp.filters["from"] = now.strftime("%Y-%m-01")
        pp.filters["to"] = now.strftime("%Y-%m-%d")
    start, end = date_range(pp)
    scope = ctx.service_ids

    # 서비스별 모드 판정 — 잘못된 UUID는 전체 모드로 폴백
    selected: Service | None = None
    raw_sid = pp.filters.get("service_id", "")
    if raw_sid:
        try:
            sid = uuid.UUID(raw_sid)
        except ValueError:
            pp.filters.pop("service_id", None)
        else:
            if scope is not None and sid not in scope:
                raise NotFoundError("서비스를 찾을 수 없습니다")
            selected = await db.get(Service, sid)
            if selected is None:
                raise NotFoundError("서비스를 찾을 수 없습니다")

    # 합계: 전체 모드=스코프 전체, 서비스별 모드=해당 서비스만
    sum_scope = [selected.id] if selected else scope
    total_count, total_amount, rows = await settlement_summary(
        db, sum_scope, start, end)

    # 서비스별 모드: 결제 건별 페이지
    pay_page = None
    if selected:
        base = (select(Payment, Subscription)
                .join(Subscription, Payment.subscription_id == Subscription.id)
                .where(Payment.status == PaymentStatus.DONE,
                       Subscription.service_id == selected.id))
        if start:
            base = base.where(Payment.approved_at >= start)
        if end:
            base = base.where(Payment.approved_at < end)
        count_q = select(func.count()).select_from(base.order_by(None).subquery())
        items_q = base.order_by(pp.order_by(_SETTLE_SORT))
        pay_page = await paginate(db, items_q, count_q, pp)

    # 서비스 select 옵션(스코프 내)
    svc_q = select(Service.id, Service.name).order_by(Service.name)
    if scope is not None:
        svc_q = svc_q.where(Service.id.in_(scope))
    service_options = [(str(sid), name) for sid, name in (await db.execute(svc_q)).all()]

    return render(request, "settlement/index.html", ctx=ctx,
                  total_count=total_count, total_amount=total_amount, rows=rows,
                  selected=selected, pay_page=pay_page, pp=pp,
                  service_options=service_options,
                  from_filter=pp.filters.get("from", ""),
                  to_filter=pp.filters.get("to", ""))
```

라우터 등록: Step 0에서 확인한 지점(다른 admin 라우터와 동일 방식)에 settlement router 추가.

- [ ] **Step 8: 템플릿 구현** — `app/admin/templates/settlement/index.html`:

```html
{% extends "base.html" %}
{% import "_list.html" as L %}
{% block title %}정산{% endblock %}
{% block crumb %}정산{% endblock %}
{% block content %}
<div class="page-head"><h1>정산</h1>
  <span class="muted" style="font-size:12px">결제 승인일(approved_at) 기준 · DONE 결제만 집계</span></div>

<form method="get" action="/admin/settlement" class="toolbar">
  <input type="date" name="from" value="{{ from_filter }}" style="width:auto"
         onchange="this.form.requestSubmit()">
  <input type="date" name="to" value="{{ to_filter }}" style="width:auto"
         onchange="this.form.requestSubmit()">
  <select name="service_id" onchange="this.form.requestSubmit()">
    <option value="">전체 서비스</option>
    {% for sid, name in service_options %}
    <option value="{{ sid }}" {{ 'selected' if selected and sid == selected.id|string }}>{{ name }}</option>
    {% endfor %}
  </select>
  <button class="btn btn-sub" type="submit">조회</button>
</form>

<div class="stats" style="margin-bottom:16px">
  <div class="stat stat-tint-2">
    <span class="stat-label">{{ selected.name ~ ' 정산 금액' if selected else '전체 정산 금액' }}</span>
    <div class="stat-row"><span class="stat-value">{{ "{:,}".format(total_amount) }}원</span></div>
  </div>
  <div class="stat stat-tint-1">
    <span class="stat-label">결제 건수</span>
    <div class="stat-row"><span class="stat-value">{{ "{:,}".format(total_count) }}건</span></div>
  </div>
</div>

{% if not selected %}
{# --- 전체 모드: 서비스별 정산 대상 목록 --- #}
<div class="card">
<table>
  <thead><tr><th>서비스</th><th>건수</th><th>금액</th><th></th></tr></thead>
  <tbody>
  {% for r in rows %}
    <tr>
      <td>{{ r.service_name }}</td>
      <td>{{ "{:,}".format(r.count) }}</td>
      <td style="font-weight:600">{{ "{:,}".format(r.amount) }}원</td>
      <td><a class="btn btn-sm btn-ghost"
             href="/admin/settlement?from={{ from_filter }}&to={{ to_filter }}&service_id={{ r.service_id }}">상세보기</a></td>
    </tr>
  {% else %}
    <tr><td colspan="4" class="muted">기간 내 정산 대상이 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}
{# --- 서비스별 모드: 결제 건별 목록 --- #}
<div class="card">
<table>
  <thead><tr>
    {{ L.sort_th(pp, '/admin/settlement', 'approved_at', '승인시각') }}
    <th>사용자</th><th>주문번호</th><th>유형</th>
    {{ L.sort_th(pp, '/admin/settlement', 'amount', '금액') }}
    <th></th>
  </tr></thead>
  <tbody>
  {% for p, sub in pay_page.items %}
    <tr>
      <td class="muted">{{ p.approved_at.strftime("%Y-%m-%d %H:%M") if p.approved_at else '-' }}</td>
      <td>{{ sub.external_user_id }}</td>
      <td style="font-family:ui-monospace,monospace;font-size:12px">{{ p.order_id }}</td>
      <td>{{ p.payment_type }}</td>
      <td style="font-weight:600">{{ "{:,}".format(p.amount) }}원</td>
      <td><a class="btn btn-sm btn-ghost" href="/admin/subscriptions/{{ sub.id }}">상세보기</a></td>
    </tr>
  {% else %}
    <tr><td colspan="6" class="muted">기간 내 정산 대상이 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
{{ L.pager(pay_page, '/admin/settlement', pp) }}
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 9: 사이드바 메뉴 추가** — `app/admin/templates/base.html`의 `{{ nav('/admin/payments', 'credit-card', '결제') }}` 아래에:

```jinja
    {{ nav('/admin/settlement', 'calculator', '정산') }}
```

- [ ] **Step 10: 통과 확인** — Run: `uv run pytest tests/e2e/test_settlement_page.py tests/integration/test_settlement.py -q` → 전체 PASS. 이어서 `uv run pytest tests/e2e -q` 회귀.

- [ ] **Step 11: 커밋**

```bash
git add app/services/settlement.py app/admin/routes/settlement.py app/admin/templates/settlement/index.html app/admin/templates/base.html app/admin/__init__.py tests/integration/test_settlement.py tests/e2e/test_settlement_page.py
git commit -m "feat(settlement): 정산 메뉴 — 기간/서비스별 DONE 결제 집계 + 상세보기"
```

---

### Task 5: 전체 검증

- [ ] **Step 1: 전체 테스트** — Run: `uv run pytest -q`
Expected: 전체 PASS (기존 363 + 신규 ~13)

- [ ] **Step 2: 잔여 확인**

- `grep -rn "_month_range\|month_filter" app/ --include="*.py" --include="*.html"` → 0건
- `grep -rn "month=" app/services/dashboard.py` → 0건

- [ ] **Step 3: 커밋(잔여 수정 시에만)**

```bash
git add -A app tests
git commit -m "test: 요청009 잔여 정리"
```

## 변경하지 않는 것 (스펙 동일)

- 외부 API(/api/v1), 알림, 스케줄러, 모델/마이그레이션
- 감사로그/엑셀, 구독·결제 리스트의 다른 필터
