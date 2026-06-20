# 구조 리팩터 S1·S2·S3·S4 구현 계획 (동작 보존)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 결제 라우트 분리(S1)·결제 공통 유틸 추출(S2)·정합성 스윕 분리(S4)·대시보드 N+1 제거(S3)를 동작 변경 없이 수행한다.

**Architecture:** S1/S2/S4는 순수 이동·추출(로직 무변경, 전체 테스트로 보호). S3는 per-period 루프 쿼리(~200회)를 "스코프 구독 상태 1회 조회 + 결제/감사 범위 1회 조회 → Python 버킷팅"으로 바꿔 동일 결과를 내되 DB 왕복을 ~4회로 줄인다(UTC 경계 그대로라 tz 드리프트 없음).

**Tech Stack:** FastAPI, SQLAlchemy 2 async, pytest (PostgreSQL/asyncpg)

**근거 레포트:** 직전 구조 분석 레포트(S1~S4). 기존 테스트 416개가 동작 보존을 검증한다.

## 파일 구조(변경 후)
- `app/services/payment_utils.py` — (신설) `CUSTOMER_KEY_RE`, `PENDING_GRACE_MESSAGE`, `safe_delete_billing_key`, `resolve_charge` (S2)
- `app/admin/deps.py` — `service_scope(ctx)` 추가 (S1 공용 스코프)
- `app/admin/routes/payments.py` — (신설) 결제 목록/상세/export + `_PAY_SORT` + `_build_payments_query` (S1)
- `app/admin/routes/subscriptions.py` — 결제 관련 제거, `service_scope` 사용 (S1)
- `app/admin/__init__.py` — payments 라우터 등록 (S1)
- `app/services/reconciliation.py` — (신설) `reconcile_pending` + 한 건 확정 (S4)
- `app/services/renewals.py` — 정합성 스윕 제거, reconciliation 호출 (S4)
- `app/services/dashboard.py` — `_series_12m`/`_daily_trend` 재작성 (S3)

---

### Task 1 (S2): 결제 공통 유틸 추출 `payment_utils.py`

**Files:**
- Create: `app/services/payment_utils.py`
- Modify: `app/services/subscriptions.py`, `app/services/renewals.py`, `app/services/payments.py`, `tests/integration/test_renewals.py`

현재 `app/services/subscriptions.py`에 정의되어 다른 서비스가 import하는 4개 심볼을 옮긴다:
`CUSTOMER_KEY_RE`(re.compile), `PENDING_GRACE_MESSAGE`(str), `safe_delete_billing_key`, `resolve_charge`.
importers: `renewals.py:24`(resolve_charge, safe_delete_billing_key), `payments.py:13-18`(4개 전부), `subscriptions.py` 자신(131/226/324에서 사용), `tests/integration/test_renewals.py:476`(safe_delete_billing_key).

- [ ] **Step 1: 현재 정의 확인** — `app/services/subscriptions.py`의 해당 4개 심볼 정의(43, 46, 56, 70번째 줄 근방)와 그들이 쓰는 import(re, TossClient, TossError, TossTimeoutError, ChargeResult, logging 등)를 읽어 의존 import 목록을 파악한다.

- [ ] **Step 2: payment_utils.py 생성** — `app/services/payment_utils.py`에 모듈 docstring + 위 4개 심볼을 **그대로** 이동(로직 무변경). 필요한 import(예: `import re`, `from app.toss.client import TossClient`, `from app.toss.errors import TossError, TossTimeoutError`, `from app.toss.types import ChargeResult`, 로깅 등 — 실제 본문이 쓰는 것만)를 새 파일에 추가.
```python
"""결제 실행 공통 유틸 — 구독 결제·갱신·단건 결제가 공유.

- resolve_charge: 토스 결제 실행 + 타임아웃 시 order_id 재조회로 결과 확정.
- safe_delete_billing_key: 빌링키 삭제(실패는 삼켜 고아 키만 남김).
- CUSTOMER_KEY_RE / PENDING_GRACE_MESSAGE: 입력 검증 정규식 / 결과 불명 안내 문구.
"""
# (subscriptions.py에서 이동한 정의 그대로)
```

- [ ] **Step 3: subscriptions.py 갱신** — 옮긴 4개 정의를 subscriptions.py에서 제거하고, 파일 내부 사용(131/226/324)을 위해 상단에 `from app.services.payment_utils import (CUSTOMER_KEY_RE, PENDING_GRACE_MESSAGE, resolve_charge, safe_delete_billing_key)` 추가. 이동으로 더 이상 안 쓰이는 import(예: resolve_charge가 쓰던 toss errors)는 subscriptions.py에서 정리(여전히 다른 코드가 쓰면 유지).

- [ ] **Step 4: renewals.py / payments.py / 테스트 import 경로 변경**
  - `renewals.py:24` → `from app.services.payment_utils import resolve_charge, safe_delete_billing_key`
  - `payments.py:13-18` → `from app.services.payment_utils import (CUSTOMER_KEY_RE, PENDING_GRACE_MESSAGE, resolve_charge, safe_delete_billing_key)`
  - `tests/integration/test_renewals.py:476` → `from app.services.payment_utils import safe_delete_billing_key`

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/integration tests/unit -q` → 전체 PASS. 이어서 `uv run pytest -q`.
  잔여 확인: `grep -rn "from app.services.subscriptions import" app tests | grep -E "resolve_charge|safe_delete_billing_key|CUSTOMER_KEY_RE|PENDING_GRACE_MESSAGE"` → 0건.

- [ ] **Step 6: 커밋**
```bash
git add app/services/payment_utils.py app/services/subscriptions.py app/services/renewals.py app/services/payments.py tests/integration/test_renewals.py
git commit -m "refactor(S2): 결제 공통 유틸을 payment_utils.py로 추출

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2 (S1): 결제 Admin 라우트를 `payments.py`로 분리

**Files:**
- Modify: `app/admin/deps.py` (service_scope 추가)
- Create: `app/admin/routes/payments.py`
- Modify: `app/admin/routes/subscriptions.py`, `app/admin/__init__.py`

현재 `app/admin/routes/subscriptions.py`에 구독+결제가 혼재. 결제 관련 6요소를 새 파일로 이동:
`_PAY_SORT`(28줄), `_build_payments_query`(143줄), `payments_export`(174줄, `/payments/export.xlsx`), `payment_detail`(192줄, `/payments/{payment_id}`), `payments_list`(207줄, `/payments`). `_scope`는 양쪽이 쓰므로 공용화.

- [ ] **Step 1: 공용 스코프 헬퍼** — `app/admin/deps.py`에 추가(파일 끝, AdminContext 정의 이후):
```python
def service_scope(ctx: AdminContext) -> list[uuid.UUID] | None:
    """담당 서비스 ID 목록. SYSTEM_ADMIN이면 None(전체)."""
    return ctx.service_ids
```
(`uuid` import가 deps.py에 없으면 추가.)

- [ ] **Step 2: subscriptions.py에서 `_scope` 제거 + 공용 사용** — `app/admin/routes/subscriptions.py`의 `_scope` 정의(34줄) 제거. import에 `service_scope` 추가(`from app.admin.deps import AdminContext, require_any, validate_csrf, service_scope`). 남는 사용처(`_build_subscriptions_query` 등 구독 관련)에서 `_scope(ctx)` → `service_scope(ctx)`로 치환.

- [ ] **Step 3: payments.py 생성** — `app/admin/routes/payments.py`에 결제 라우트 이동:
```python
import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_any, service_scope
from app.admin.export import xlsx_response
from app.admin.filters import plan_name_options
from app.admin.pagination import PageParams, date_range, paginate
from app.api.deps import get_db
from app.core.clock import kst_format
from app.core.errors import NotFoundError
from app.models import Payment, Plan, Service, Subscription

router = APIRouter()

_PAY_SORT = { ... }                       # subscriptions.py에서 이동
def _build_payments_query(pp, ctx): ...   # 이동(내부 _scope → service_scope)
@router.get("/payments/export.xlsx") ...  # payments_export 이동
@router.get("/payments/{payment_id}") ...  # payment_detail 이동
@router.get("/payments") ...               # payments_list 이동
```
이동 시 본문 내 `_scope(ctx)` → `service_scope(ctx)`. 라우트 등록 순서는 export(정적) → {payment_id} → 목록 순 유지(기존과 동일하게 정적/동적 순서 보존).

- [ ] **Step 4: subscriptions.py에서 결제 요소 제거** — 이동한 `_PAY_SORT`, `_build_payments_query`, `payments_export`, `payment_detail`, `payments_list` 삭제. 그로 인해 안 쓰이는 import(`xlsx_response`, `plan_name_options`, `date_range`, `kst_format`, `Payment` 등)가 subscriptions.py에서 더 이상 안 쓰이면 정리(구독 코드가 여전히 쓰면 유지 — 예: Payment는 subscription_detail에서 사용하므로 유지될 수 있음. grep로 확인 후 정리).

- [ ] **Step 5: 라우터 등록** — `app/admin/__init__.py`:
  - import에 `payments` 추가(기존 `subscriptions` 옆).
  - `router.include_router(payments.router)` 추가(subscriptions 등록 근처).

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/e2e -q` → 전체 PASS(결제 목록/상세/export, 구독, 정산, 서비스상세 SUB_SORT import 회귀 포함). 이어서 `uv run pytest -q`.
  잔여 확인: `grep -rn "def _scope" app/admin/routes` → 0건. `grep -rn "from app.admin.routes.subscriptions import" app` → SUB_SORT만(payments 심볼 import 없음).

- [ ] **Step 7: 커밋**
```bash
git add app/admin/deps.py app/admin/routes/payments.py app/admin/routes/subscriptions.py app/admin/__init__.py
git commit -m "refactor(S1): 결제 Admin 라우트를 payments.py로 분리 + service_scope 공용화

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3 (S4): 정합성 스윕을 `reconciliation.py`로 분리

**Files:**
- Create: `app/services/reconciliation.py`
- Modify: `app/services/renewals.py`

현재 `app/services/renewals.py`의 `_reconcile_pending_payments`(346줄) + `_reconcile_one_payment`(373줄)을 분리. `process_due`(90줄)가 `_reconcile_pending_payments`를 호출(133줄).

- [ ] **Step 1: 의존 파악** — 두 함수가 쓰는 심볼(락 헬퍼 `_acquire_lock`/`_release_lock`, `PENDING_RECONCILE_GRACE` 상수, `Payment`/`Subscription`/`PaymentStatus`/`SubscriptionStatus`/`PaymentType`, `record_audit`, toss `get_payment_by_order_id`, `_DUE_STATUSES` 등)을 renewals.py에서 읽어 목록화. 락 헬퍼/상수가 renewals 전용이면 reconciliation에서 재사용할 방법 결정(공용은 import, 전용은 함께 이동 또는 import).

- [ ] **Step 2: reconciliation.py 생성** — `app/services/reconciliation.py`에 모듈 docstring + 두 함수 이동(로직 무변경). 락 헬퍼(`_acquire_lock`/`_release_lock`)가 두 함수에서 쓰이면: renewals도 계속 쓰므로 **락 헬퍼는 renewals.py에 두고 reconciliation이 import**하거나, 공용 위치(예: 같은 파일에 두되 renewals가 import). 가장 단순: 락 헬퍼와 grace 상수를 reconciliation에서 `from app.services.renewals import _acquire_lock, _release_lock, PENDING_RECONCILE_GRACE`로 가져오면 순환 import 위험(renewals가 reconciliation을 import). **순환 회피**: 락 헬퍼·grace를 `reconciliation.py`로 옮기거나 별도 `app/services/locks.py`로 추출 후 양쪽이 import. 구현자가 순환 없는 구조를 택하고 보고할 것(권장: 락 헬퍼는 renewals 전용 사용도 많으므로 `locks.py`로 추출하거나, reconciliation이 자체 락 헬퍼 사본 없이 renewals에서 import하되 renewals는 reconciliation을 함수 내부에서 지연 import).
```python
"""PENDING 결제 정합성 — 유예 지난 PENDING을 토스 재조회로 DONE/FAILED 확정.

구독 결제(FIRST/RENEWAL/RETRY)와 단건 결제(subscription_id NULL) 모두 처리.
"""
async def reconcile_pending(session_factory, redis, toss, cipher, email_sender, now): ...
async def _reconcile_one_payment(...): ...
```
(공개 이름은 `reconcile_pending`으로 명확화 — 기존 `_reconcile_pending_payments` 역할.)

- [ ] **Step 3: renewals.py 갱신** — 두 함수 제거. `process_due`의 호출부(133줄)를 `from app.services.reconciliation import reconcile_pending` 후 `await reconcile_pending(session_factory, redis, toss, cipher, email_sender, now=now)`로 변경. 안 쓰이는 import 정리. 순환 import 없도록 import 위치 조정.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/integration/test_renewals.py tests/integration/test_one_off_payment.py -q` → PASS(갱신·정산 스윕·단건 정산 회귀). 이어서 `uv run pytest -q`.
  잔여: `grep -rn "_reconcile_pending_payments\|_reconcile_one_payment" app` → reconciliation.py 내부만.

- [ ] **Step 5: 커밋**
```bash
git add app/services/reconciliation.py app/services/renewals.py
git commit -m "refactor(S4): PENDING 정합성 스윕을 reconciliation.py로 분리

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4 (S3): 대시보드 N+1 제거 — `_series_12m`/`_daily_trend` 재작성

**Files:**
- Modify: `app/services/dashboard.py`
- Test: `tests/integration/test_dashboard.py`

현재 `_series_12m`(per-month 3쿼리×12) + `_daily_trend`(per-day 5쿼리×30)이 루프마다 DB 왕복. 목표: **스코프 구독 상태 1회 조회 + ONE_OFF 결제 1회 조회 + 감사 1회 조회 → Python 버킷팅**으로 동일 결과. UTC 경계는 기존과 동일하게 유지(date_trunc 미사용 → tz 드리프트 없음).

기존 정의(보존해야 할 동작):
- 월 전체구독 `total[i]` = 구독 중 `created_at <= at_i` AND (`status in OPEN_STATUSES` OR (`status==CANCELED` AND `current_period_end > at_i`)). `at_i = min(month_end, now)`.
- 월 신규 `new[i]` = `created_at in [month_start_i, month_end_i)`.
- 월 일반매출 = ONE_OFF·DONE·`approved_at in [month_i, month_i+1)` 합.
- 일 `total` = 위 스냅샷 식, `at = min(day_end, now)`. 일 `new` = `created_at in [day, day+1)`. 일 `canceled` = 감사(cancel+force_cancel+suspended) `created_at in [day, day+1)`. 일 `expired` = 감사(subscription.expired) 동일.
- 반환 형태 동일: `subs=[{label, done, failed}]`(done=전체, failed=신규), `one_off=[{label, value}]`, `daily=[{label, total, new, canceled, expired}]`.

- [ ] **Step 1: 멀티-기간 검증 테스트 추가** — `tests/integration/test_dashboard.py`에 여러 달/여러 날에 걸친 데이터를 심고 시리즈 버킷을 검증(재작성 전/후 동일 보장). 예:
```python
async def test_series_buckets_multi_period(db, cipher):
    from datetime import timedelta
    from app.core.clock import utcnow
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    now = utcnow()
    # 이번달 신규 2, 지난달 신규 1(과거 created_at)
    await create_subscription(db, cipher, svc, plan, external_user_id="cur1", status="ACTIVE")
    await create_subscription(db, cipher, svc, plan, external_user_id="cur2", status="ACTIVE")
    old = await create_subscription(db, cipher, svc, plan, external_user_id="old1", status="ACTIVE")
    old.created_at = now - timedelta(days=40); await db.commit()
    data = await build_dashboard(db, None)
    assert data.subs_months[-1]["failed"] == 2          # 이번달 신규
    assert data.subs_months[-2]["failed"] == 1          # 지난달 신규
    assert data.subs_months[-1]["done"] == 3            # 전체구독(스냅샷)
    assert len(data.daily_trend) == 30
    assert data.daily_trend[-1]["new"] == 2             # 오늘 신규(cur1,cur2; old는 40일 전)
```
(기존 `test_twelve_month_series_subs_and_one_off`, `test_daily_trend_30_days`, `test_status_donut_*`도 회귀 보호.)

- [ ] **Step 2: 실패/현행 확인** — Run: `uv run pytest tests/integration/test_dashboard.py -q` → 신규 테스트가 현행 코드에서 PASS(현행 동작 기준선). (재작성 후에도 동일하게 PASS해야 함.)

- [ ] **Step 3: 조회 헬퍼 추가** — `app/services/dashboard.py`에 단일 조회 헬퍼 추가:
```python
async def _fetch_sub_states(db, scope):
    """스코프 구독의 (status, created_at, current_period_end) 1회 조회 — 스냅샷/신규 계산용."""
    q = select(Subscription.status, Subscription.created_at,
               Subscription.current_period_end)
    rows = (await db.execute(_scoped(q, scope, Subscription.service_id))).all()
    return rows


def _open_count_at(states, at):
    """at 시점 '열린' 구독 수 (Python 버킷 — _open_subs_cond와 동일 규칙)."""
    n = 0
    for status, created_at, period_end in states:
        if created_at <= at and (
                status in _OPEN_STATUSES_STR
                or (status == "CANCELED" and period_end is not None and period_end > at)):
            n += 1
    return n


def _new_count_between(states, start, end):
    return sum(1 for _s, c, _p in states if start <= c < end)
```
주의: `_OPEN_STATUSES`는 enum 튜플 — 문자열 비교를 위해 값 집합 `_OPEN_STATUSES_STR = {s.value for s in _OPEN_STATUSES}` 또는 `status in _OPEN_STATUSES`(status가 enum/str 무엇으로 오는지 확인 후 일치시킬 것). DB에서 status는 문자열로 오므로 값 비교가 안전.

ONE_OFF 월 매출 1회 조회:
```python
async def _fetch_oneoff_payments(db, scope, start, end):
    q = select(Payment.approved_at, Payment.amount).where(
        Payment.status == PaymentStatus.DONE, Payment.kind == PaymentKind.ONE_OFF,
        Payment.approved_at >= start, Payment.approved_at < end)
    return (await db.execute(_scoped(q, scope, Payment.service_id))).all()
```
감사 이벤트 1회 조회(30일):
```python
async def _fetch_audit_events(db, scope, actions, start, end):
    q = (select(AuditLog.action, AuditLog.created_at)
         .where(AuditLog.action.in_(actions),
                AuditLog.created_at >= start, AuditLog.created_at < end))
    if scope is not None:
        sub_sq = select(cast(Subscription.id, String)).where(
            Subscription.service_id.in_(scope))
        q = q.where(AuditLog.target_id.in_(sub_sq))
    return (await db.execute(q)).all()
```

- [ ] **Step 4: `_series_12m` 재작성** — per-month 루프를 단일 조회 + Python 버킷으로:
```python
async def _series_12m(db, scope, now, month_start):
    states = await _fetch_sub_states(db, scope)
    first = month_start - relativedelta(months=11)
    oneoff_rows = await _fetch_oneoff_payments(db, scope, first, now + relativedelta(seconds=1))
    subs, one_off = [], []
    for i in range(11, -1, -1):
        start = month_start - relativedelta(months=i)
        end = start + relativedelta(months=1)
        label = f"{start.month}월"
        new_n = _new_count_between(states, start, end)
        at = min(end, now)
        total = _open_count_at(states, at)
        subs.append({"label": label, "done": total, "failed": new_n})
        rev = sum(amt for ap, amt in oneoff_rows if start <= ap < end)
        one_off.append({"label": label, "value": rev})
    return subs, one_off
```

- [ ] **Step 5: `_daily_trend` 재작성** — per-day 루프를 단일 조회 + Python 버킷으로:
```python
async def _daily_trend(db, scope, now):
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - relativedelta(days=29)
    states = await _fetch_sub_states(db, scope)
    cancel_actions = _USER_CANCEL_ACTIONS + _PAYMENT_EXPIRE_ACTIONS
    cancel_events = await _fetch_audit_events(db, scope, cancel_actions, first, now + relativedelta(seconds=1))
    expire_events = await _fetch_audit_events(db, scope, _EXPIRE_ACTIONS, first, now + relativedelta(seconds=1))
    out = []
    for i in range(29, -1, -1):
        day = today - relativedelta(days=i)
        nxt = day + relativedelta(days=1)
        at = min(nxt, now)
        total = _open_count_at(states, at)
        new_n = _new_count_between(states, day, nxt)
        canceled = sum(1 for _a, c in cancel_events if day <= c < nxt)
        expired = sum(1 for _a, c in expire_events if day <= c < nxt)
        out.append({"label": f"{day.month}/{day.day}", "total": total, "new": new_n,
                    "canceled": canceled, "expired": expired})
    return out
```
주의: 기존 `_count`/`_audit_count`/`_revenue_between`은 다른 곳(revenue_cards/sub_flow/status counts)에서 여전히 사용 — **그대로 둔다**(여기서만 루프 제거).

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/integration/test_dashboard.py tests/e2e/test_dashboard_page.py -q` → 전체 PASS(신규 멀티-기간 + 기존 회귀 동일 값). 이어서 `uv run pytest -q`.

- [ ] **Step 7: 커밋**
```bash
git add app/services/dashboard.py tests/integration/test_dashboard.py
git commit -m "perf(S3): 대시보드 12개월·30일 시리즈를 단일 조회+Python 버킷으로(N+1 제거)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 전체 검증 + 최종 리뷰
- [ ] **Step 1: 전체 테스트** — Run: `uv run pytest -q` → 전체 PASS(기존 416 + 신규).
- [ ] **Step 2: 구조 확인**
  - `wc -l app/admin/routes/subscriptions.py app/admin/routes/payments.py app/services/renewals.py app/services/reconciliation.py app/services/payment_utils.py` — 분리 확인.
  - `grep -rn "from app.services.subscriptions import" app tests | grep -E "resolve_charge|safe_delete|CUSTOMER_KEY|PENDING_GRACE"` → 0.
  - `grep -rn "def _scope\b" app/admin/routes` → 0(service_scope로 통일).
- [ ] **Step 3: 최종 코드리뷰** — 동작 보존(로직 무변경) + S3 결과 동일성 중심으로 점검.

## 변경하지 않는 것
- 모델/마이그레이션/외부 API/Admin URL 경로. 모든 라우트 URL·응답은 동일.
- S5/S6/S7 등 미선택 항목.
