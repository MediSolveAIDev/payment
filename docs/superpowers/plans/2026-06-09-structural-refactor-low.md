# 저위험 구조 리팩터 L1~L5 구현 계획 (동작 보존 + 주석 동반)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 만료 함수 공통화(L1)·`_Cfg` 타입 명시(L4)·`update_plan` _UNSET 정리(L2)·`subs_months` 키 rename(L3)·`services_detail` 탭 헬퍼 추출(L5)를 동작 변경 없이 수행하고, 바뀐 코드의 주석을 함께 갱신한다.

**Architecture:** 모두 순수 추출/정리(URL·응답·집계 결과·DB 무변경). 기존 418 테스트가 동작 보존을 검증. 변경한 함수·로직의 docstring/주석을 같은 커밋에서 정확히 갱신(프로젝트 표준: 코드+주석만으로 흐름 이해).

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Jinja2, pytest

**근거:** 직전 구조 분석 레포트 L1~L5.

## 파일 구조
- `app/services/renewals.py` — `_expire_subscription` 공통 헬퍼(L1), `_Cfg.settings` 타입(L4)
- `app/services/plans.py` — `update_plan` _UNSET 해소 헬퍼(L2)
- `app/services/dashboard.py` + `app/admin/templates/_charts.html` + `app/admin/templates/dashboard.html` — subs_months 키 rename(L3)
- `app/admin/routes/services.py` — `services_detail` 탭 데이터 헬퍼(L5)
- 테스트: `tests/integration/test_dashboard.py`(L3 키 변경 반영), 그 외는 기존 테스트로 회귀 보호

---

### Task 1 (L1): 만료 처리 공통 헬퍼 `_expire_subscription`

**Files:**
- Modify: `app/services/renewals.py`

`_expire_suspended`/`_expire_canceled`가 락→FOR UPDATE 조회→상태 재확인→빌링키 삭제→EXPIRED→감사→commit 구조가 동일. 차이는 (a) "만료 대상인지" 판정 조건, (b) 감사 reason 뿐.

- [ ] **Step 1: 공통 헬퍼 추가** — `app/services/renewals.py`에 `_expire_canceled` 위(또는 두 함수 앞)에:
```python
async def _expire_subscription(session_factory, redis, toss, cipher, sub_id: uuid.UUID,
                               *, reason: str, should_expire, stats: dict) -> None:
    """구독을 EXPIRED로 종료(빌링키 삭제 포함) — 정지/취소 만료 공통 로직.

    Redis 락으로 배치 중복 실행 경쟁을 막고, FOR UPDATE로 행을 잠근다.
    `should_expire(sub)`가 True일 때만 만료 처리(상태·시점 판정은 호출측이 주입).
    빌링키 삭제는 best-effort — 성공 시에만 암호문 제거(실패 시 운영자 재시도 위해 보존).
    감사 detail.reason은 호출 경위(suspended_timeout / canceled_period_end)를 기록한다.
    """
    lock_key = f"lock:renew:{sub_id}"
    token = await _acquire_lock(redis, lock_key)
    if token is None:
        stats["skipped"] += 1
        return
    try:
        async with session_factory() as db:
            sub = await db.get(Subscription, sub_id, with_for_update=True)
            if sub is None or not should_expire(sub):
                stats["skipped"] += 1
                return
            billing_key = (cipher.decrypt(sub.billing_key_encrypted)
                           if sub.billing_key_encrypted else None)
            sub.status = SubscriptionStatus.EXPIRED
            sub.next_billing_at = None
            # 삭제 먼저 → 성공 시에만 암호문 제거(실패 시 보존)
            if billing_key and await safe_delete_billing_key(toss, billing_key):
                sub.billing_key_encrypted = None
            await record_audit(db, actor_type="SYSTEM", action="subscription.expired",
                               target_type="subscription", target_id=str(sub.id),
                               detail={"reason": reason})
            await db.commit()
        stats["expired"] += 1
    finally:
        await _release_lock(redis, lock_key, token)
```

- [ ] **Step 2: `_expire_suspended`/`_expire_canceled`를 래퍼로 축소** — 본문을 헬퍼 호출로 교체(시그니처·docstring 유지하되 "공통 헬퍼 위임" 한 줄 추가):
```python
async def _expire_suspended(session_factory, redis, toss, cipher,
                            sub_id: uuid.UUID, *, now, cfg: _Cfg, stats: dict) -> None:
    """SUSPENDED 대기 일수(suspended_grace) 초과 → EXPIRED + 빌링키 삭제(종단).

    SUSPENDED는 수동 결제로 복구 가능해 빌링키를 보관해 오다가, 유예 초과 시 최종 삭제한다.
    판정: 상태가 SUSPENDED이고 suspended_at이 (now - grace) 이하일 때만 만료.
    실제 종료 처리는 _expire_subscription에 위임한다.
    """
    await _expire_subscription(
        session_factory, redis, toss, cipher, sub_id, reason="suspended_timeout",
        stats=stats,
        should_expire=lambda sub: (
            sub.status == SubscriptionStatus.SUSPENDED
            and sub.suspended_at is not None
            and sub.suspended_at <= now - cfg.suspended_grace))


async def _expire_canceled(session_factory, redis, toss, cipher,
                           sub_id: uuid.UUID, *, now: datetime, stats: dict) -> None:
    """CANCELED 구독의 기간 만료(current_period_end <= now) → EXPIRED + 빌링키 삭제.

    취소 후 혜택 유지 기간이 끝난 구독을 최종 종료한다. 처리는 _expire_subscription에 위임.
    """
    await _expire_subscription(
        session_factory, redis, toss, cipher, sub_id, reason="canceled_period_end",
        stats=stats,
        should_expire=lambda sub: (
            sub.status == SubscriptionStatus.CANCELED
            and sub.current_period_end <= now))
```
(주의: 기존 skip 조건 `suspended_at > now - grace`의 부정은 `suspended_at <= now - grace`, `current_period_end > now`의 부정은 `current_period_end <= now` — `sub is None`은 헬퍼가 처리하므로 should_expire에선 제외. 동작 동일.)

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest tests/integration/test_renewals.py -q` → 기존과 동일 PASS(정지/취소 만료 시나리오 회귀). 이어서 `uv run pytest -q`.

- [ ] **Step 4: 커밋**
```bash
git add app/services/renewals.py
git commit -m "refactor(L1): 정지/취소 만료를 _expire_subscription 공통 헬퍼로

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2 (L4): `_Cfg.settings` 타입 명시

**Files:**
- Modify: `app/services/renewals.py`

- [ ] **Step 1: 타입 어노테이션** — `_Cfg.__init__`의 `settings` 파라미터에 타입 추가. `app/services/renewals.py` 상단 import에 `from app.core.config import Settings` 추가(순환 없음 — config는 renewals를 import하지 않음). 본문 변경 없음:
```python
    def __init__(self, settings: "Settings | None") -> None:
```
(docstring에 "settings가 None이면 기본 상수 사용(테스트 편의)" 유지.)

- [ ] **Step 2: 순환 import 확인 + 통과** — Run: `uv run python -c "import app.main"` 정상. `uv run pytest tests/integration/test_renewals.py -q` → PASS.
  (순환이 생기면 `from __future__ import annotations` + `TYPE_CHECKING` 블록으로 import해 문자열 어노테이션 유지.)

- [ ] **Step 3: 커밋**
```bash
git add app/services/renewals.py
git commit -m "refactor(L4): _Cfg.settings 타입 명시(Settings | None)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3 (L2): `update_plan`의 `_UNSET` 패턴 정리

**Files:**
- Modify: `app/services/plans.py`

값/타입 부분수정의 3분기 패턴(first_payment, recurring_discount)이 반복. 공통 헬퍼로 추출.

- [ ] **Step 1: 헬퍼 추가** — `app/services/plans.py`에 `update_plan` 위에:
```python
def _resolve_unset(new, current):
    """_UNSET이면 기존 값 유지, 아니면 새 값(부분 수정용)."""
    return current if new is _UNSET else new


def _resolve_coupled_value(new_value, new_type, current_value, *, clears: tuple):
    """타입/값 쌍의 부분 수정에서 값(value)을 결정.

    - 값이 전달됨 → 그대로
    - 값 미전달·타입만 전달 → 타입이 clears(예: NONE/FREE)면 None, 아니면 기존 값 유지
    - 둘 다 미전달 → 기존 값
    """
    if new_value is not _UNSET:
        return new_value
    if new_type is not _UNSET:
        return None if new_type in clears else current_value
    return current_value
```

- [ ] **Step 2: update_plan 본문 교체** — 값 결정 블록을 헬퍼 호출로(동작 동일):
```python
    plan = await _get_plan(db, plan_id, service_id)
    new_name = name if name is not None else plan.name
    new_price = price if price is not None else plan.price
    new_fpt = _resolve_unset(first_payment_type, plan.first_payment_type)
    new_fpv = _resolve_coupled_value(first_payment_value, first_payment_type,
                                     plan.first_payment_value,
                                     clears=(FirstPaymentType.NONE, FirstPaymentType.FREE))
    new_rdt = _resolve_unset(recurring_discount_type, plan.recurring_discount_type)
    new_rdv = _resolve_coupled_value(recurring_discount_value, recurring_discount_type,
                                     plan.recurring_discount_value,
                                     clears=(DiscountType.NONE,))
    new_trial_enabled = (plan.trial_enabled if trial_enabled is _UNSET
                         else bool(trial_enabled))
    if trial_days is not _UNSET:
        new_trial_days = trial_days
    elif trial_enabled is not _UNSET:
        # 체험 토글만 바뀐 경우: 끄면 일수 제거, 켜면 기존 일수 유지
        new_trial_days = plan.trial_days if new_trial_enabled else None
    else:
        new_trial_days = plan.trial_days
```
(trial은 enabled bool에 따라 값이 정해져 clears 패턴과 달라 그대로 둔다 — 위 주석으로 의도 명시. 나머지 검증/할당부는 변경 없음.)

- [ ] **Step 3: docstring 보완** — `update_plan` docstring의 "값 연동 규칙" 설명은 그대로 유지(헬퍼와 일치). 헬퍼 추출로 "first_payment_value/recurring_discount_value는 _resolve_coupled_value로 결정" 한 줄 추가.

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/integration -k plan -q` → PASS(요금제 생성/수정/금액 회귀). 이어서 `uv run pytest -q`.

- [ ] **Step 5: 커밋**
```bash
git add app/services/plans.py
git commit -m "refactor(L2): update_plan _UNSET 값 결정을 헬퍼로 정리

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4 (L3): `subs_months` 키 `done/failed` → `total/new`

**Files:**
- Modify: `app/services/dashboard.py`, `app/admin/templates/_charts.html`, `app/admin/templates/dashboard.html`, `tests/integration/test_dashboard.py`

`subs_months`가 `{label, done(전체), failed(신규)}`로 오해 소지 → `{label, total, new}`. bars 매크로(p.done/p.failed)를 키명 파라미터화해 재사용성 유지.

- [ ] **Step 1: 테스트 키 변경** — `tests/integration/test_dashboard.py`에서 subs_months의 `["done"]`/`["failed"]`를 `["total"]`/`["new"]`로:
  - `test_twelve_month_series_subs_and_one_off`: `data.subs_months[-1]["done"]` → `["total"]`, `["failed"]` → `["new"]`.
  - `test_series_buckets_multi_period`: `["failed"]`/`["done"]` 사용처 동일 변경.

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/integration/test_dashboard.py -k "twelve_month or buckets" -x -q` → FAIL(현재 done/failed 키).

- [ ] **Step 3: dashboard `_series_12m` 키 rename** — `app/services/dashboard.py` `_series_12m`의 append를:
```python
        subs.append({"label": label, "total": total, "new": new_n})  # total=전체구독수, new=신규구독수
```
(주석의 "bars 재사용(done=전체, failed=신규)" → 위처럼 명확한 키로 갱신.)

- [ ] **Step 4: bars 매크로 키 파라미터화** — `app/admin/templates/_charts.html` `bars` 매크로에 키 이름 인자 추가(기본값으로 하위호환):
```jinja
{%- macro bars(series, height=200, label_a='성공', label_b='실패', color_a='var(--accent-mint)', color_b='var(--accent-red)', key_a='done', key_b='failed') -%}
```
본문에서 `p.done`→`p[key_a]`, `p.failed`→`p[key_b]`로 전부 교체(maxv 수집부·dh/fh 계산부 포함).

- [ ] **Step 5: dashboard.html 호출 갱신** — `charts.bars(d.subs_months, ...)` 호출에 `key_a='total', key_b='new'` 추가:
```jinja
    {{ charts.bars(d.subs_months, label_a='전체구독', label_b='신규구독',
                   color_a='var(--accent-indigo)', color_b='var(--accent-mint)',
                   key_a='total', key_b='new') }}
```
`DashboardData.subs_months` 필드 주석도 `# [{label, total(전체), new(신규)}]`로 갱신.

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/integration/test_dashboard.py tests/e2e/test_dashboard_page.py -q` → PASS. 이어서 `uv run pytest -q`.

- [ ] **Step 7: 커밋**
```bash
git add app/services/dashboard.py app/admin/templates/_charts.html app/admin/templates/dashboard.html tests/integration/test_dashboard.py
git commit -m "refactor(L3): subs_months 키 total/new로 명확화 + bars 매크로 키 파라미터화

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5 (L5): `services_detail` 탭 데이터 헬퍼 추출

**Files:**
- Modify: `app/admin/routes/services.py`

`services_detail`이 요금제·구독·단건결제 탭 데이터 구성을 한 함수에 모음. 탭별 헬퍼로 추출해 라우트를 짧게(동작·반환 kwargs 동일).

- [ ] **Step 1: 탭 헬퍼 3개 추가** — `app/admin/routes/services.py`의 `services_detail` 위에:
```python
async def _plans_tab(db, service_id):
    """요금제 탭 데이터 — 표시용 금액/툴팁을 각 Plan에 주입해 반환."""
    plans = (await db.scalars(select(Plan).where(Plan.service_id == service_id)
                              .order_by(Plan.created_at))).all()
    for p in plans:
        p.recurring_amount = plan_recurring_amount(p)
        p.first_amount = plan_first_amount(p)
        p.first_tooltip = first_amount_breakdown(p)
        p.recurring_tooltip = recurring_amount_breakdown(p)
    return plans


async def _subs_tab(db, request, service_id):
    """구독 탭 데이터 — 서비스 고정, /admin/subscriptions와 동일 필터/정렬. (sub_page, spp) 반환."""
    spp = PageParams.from_request(request, sortable=set(SUB_SORT),
                                  default_sort="created_at", filter_keys=("status",))
    base = (select(Subscription, Plan).join(Plan, Subscription.plan_id == Plan.id)
            .where(Subscription.service_id == service_id))
    if spp.q:
        base = base.where(Subscription.external_user_id.ilike(f"%{spp.q}%"))
    if spp.filters.get("status"):
        base = base.where(Subscription.status == spp.filters["status"])
    count_q = select(func.count()).select_from(base.order_by(None).subquery())
    page = await paginate(db, base.order_by(spp.order_by(SUB_SORT)), count_q, spp)
    return page, spp


async def _oneoff_tab(db, request, service_id):
    """단건결제 탭 데이터 — kind=ONE_OFF 고정. (oneoff_page, opp) 반환(Payment 평탄화)."""
    opp = PageParams.from_request(request, sortable={"requested_at"},
                                  default_sort="requested_at")
    base = select(Payment).where(Payment.service_id == service_id,
                                 Payment.kind == PaymentKind.ONE_OFF)
    count_q = select(func.count()).select_from(base.order_by(None).subquery())
    page = await paginate(db, base.order_by(opp.order_by(ONEOFF_SORT)), count_q, opp)
    page.items = [r[0] for r in page.items]   # 단일 엔티티 Row → Payment 평탄화
    return page, opp
```

- [ ] **Step 2: services_detail 본문 교체** — 탭 구성부를 헬퍼 호출로(나머지 동일):
```python
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    plans = await _plans_tab(db, service_id)
    sub_count = await db.scalar(select(func.count()).select_from(Subscription)
                                .where(Subscription.service_id == service_id)) or 0
    managers, assignable = await _service_managers(db, service_id)
    sub_page, spp = await _subs_tab(db, request, service_id)
    oneoff_page, opp = await _oneoff_tab(db, request, service_id)

    hx_target = (request.headers.get("HX-Target", "")
                 if request.headers.get("HX-Request") else "")
    template = {"list-svc-plans": "services/_plans_table.html",
                "list-svc-subs": "services/_subs_table.html",
                "list-svc-oneoff": "services/_oneoff_table.html"}.get(
                    hx_target, "services/detail.html")
    return render(request, template, ctx=ctx, service=service, ...)  # 기존 render kwargs 그대로
```
(render 호출의 kwargs 목록·이름은 기존과 동일하게 유지. docstring은 "탭 데이터는 _plans_tab/_subs_tab/_oneoff_tab로 분리" 반영해 갱신.)

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest tests/e2e/test_service_detail_page.py tests/e2e/test_htmx_partials.py -q` → PASS(상세·탭·htmx partial 회귀). 이어서 `uv run pytest -q`.

- [ ] **Step 4: 커밋**
```bash
git add app/admin/routes/services.py
git commit -m "refactor(L5): services_detail 탭 데이터를 _plans_tab/_subs_tab/_oneoff_tab로 분리

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 전체 검증 + 최종 리뷰
- [ ] **Step 1: 전체 테스트** — Run: `uv run pytest -q` → 전체 PASS(418+).
- [ ] **Step 2: 잔여 확인**
  - `grep -rn "subs_months\[.*\]\[.done.\|.failed.\]" tests` 류로 구 키 잔재 0.
  - `_charts.html`/`dashboard.html`에 `p.done`/`p.failed`로 subs_months 참조 잔재 없음.
  - `services_detail` 함수 길이가 줄고 탭 헬퍼 3개 존재.
- [ ] **Step 3: 최종 코드리뷰** — 동작 보존 + 주석이 변경된 코드와 일치하는지 중심.

## 변경하지 않는 것
- URL·응답·집계 결과·DB. 다른 bars 매크로 사용처(있다면 기본 키 done/failed로 하위호환).
