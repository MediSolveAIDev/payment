# MINUTE 결제주기 (자동연장 테스트용) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 요금제 결제주기에 분(MINUTE) 단위를 추가해 자동연장 흐름을 몇 분 만에 테스트할 수 있게 한다.

**Architecture:** 기존 `DAY`/`cycle_days`(일수) 패턴을 평행하게 확장한다 — `MINUTE`/`cycle_minutes`(분, 새 nullable 컬럼). 기간 계산은 단일 지점 `compute_period_end`에서 분기한다. 운영 오사용을 막기 위해 검증 계층에서 `environment == "prod"`이면 MINUTE를 거부한다.

**Tech Stack:** FastAPI, SQLAlchemy(async), Alembic, pydantic, htmx(Jinja2), pytest/pytest-asyncio.

## Global Constraints

- `cycle_minutes`는 MINUTE 주기일 때만 사용하며 **최소 5분**(스케줄러 기본 스윕 주기 5분과 정합). 그 외 주기에서는 NULL/전달 금지.
- MINUTE는 **비운영 전용** — `settings.environment == "prod"`이면 생성 거부(API·어드민 공통, 검증 계층에서 차단).
- 결제주기(billing_cycle/cycle_days/cycle_minutes)는 **생성 시에만 설정**하며 `update_plan`은 변경하지 않는다(기존 규칙 유지).
- 모든 변경 코드에 주석/docstring을 함께 갱신한다(프로젝트 규칙).
- 스케줄러 기본 주기(`scheduler_interval_minutes=5`)는 변경하지 않는다.
- 한국어 주석 스타일을 기존 코드와 맞춘다.

---

### Task 1: enum MINUTE + compute_period_end 분기

**Files:**
- Modify: `app/models/enums.py:31-37` (BillingCycle)
- Modify: `app/services/billing_math.py:39-51` (compute_period_end)
- Test: `tests/unit/test_billing_math.py`

**Interfaces:**
- Produces: `BillingCycle.MINUTE == "MINUTE"`; `compute_period_end(start, cycle, cycle_days=None, cycle_minutes=None) -> datetime` — MINUTE일 때 `cycle_minutes`(≥5) 사용.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/unit/test_billing_math.py`의 기간계산 테스트 클래스에 추가

```python
def test_minute_cycle_adds_minutes(self):
    # MINUTE 주기: cycle_minutes 분 만큼 더한다
    assert compute_period_end(dt(2026, 6, 5), "MINUTE", cycle_minutes=5) == dt(2026, 6, 5, 0, 5)

def test_minute_cycle_requires_min_5(self):
    # cycle_minutes 누락/5 미만이면 오류
    import pytest
    from app.core.errors import InputValidationError
    with pytest.raises(InputValidationError):
        compute_period_end(dt(2026, 6, 5), "MINUTE", cycle_minutes=None)
    with pytest.raises(InputValidationError):
        compute_period_end(dt(2026, 6, 5), "MINUTE", cycle_minutes=4)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/unit/test_billing_math.py -k minute -v`
Expected: FAIL (MINUTE 분기 없음 → `지원하지 않는 결제 주기` 또는 시그니처 불일치)

- [ ] **Step 3: enum에 MINUTE 추가** — `app/models/enums.py` BillingCycle

```python
class BillingCycle(StrEnum):
    """요금제 결제 주기. DAY 선택 시 Plan.cycle_days, MINUTE 선택 시 Plan.cycle_minutes(5 이상)를 함께 지정.

    MINUTE는 자동연장 테스트용이며 비운영 환경(environment != prod)에서만 생성 가능하다.
    """

    YEAR = "YEAR"      # 연 단위 결제
    MONTH = "MONTH"    # 월 단위 결제
    WEEK = "WEEK"      # 주 단위 결제
    DAY = "DAY"        # 일 단위 결제(cycle_days로 실제 일수 지정)
    MINUTE = "MINUTE"  # 분 단위 결제(cycle_minutes로 실제 분 지정, 최소 5분; 테스트용·비운영 전용)
```

- [ ] **Step 4: compute_period_end 확장** — `app/services/billing_math.py`

```python
def compute_period_end(start: datetime, cycle: str, cycle_days: int | None = None,
                       cycle_minutes: int | None = None) -> datetime:
    """구독 기간 종료일 계산. MONTH/YEAR는 월말 클램프(relativedelta).

    DAY는 cycle_days(1 이상), MINUTE는 cycle_minutes(5 이상)를 사용한다.
    MINUTE는 자동연장 테스트용 주기다(비운영 전용 — 생성 검증에서 운영 차단).
    """
    if cycle == BillingCycle.YEAR:
        return start + relativedelta(years=1)
    if cycle == BillingCycle.MONTH:
        return start + relativedelta(months=1)
    if cycle == BillingCycle.WEEK:
        return start + timedelta(weeks=1)
    if cycle == BillingCycle.DAY:
        if not cycle_days or cycle_days < 1:
            raise InputValidationError("DAY 주기는 cycle_days(1 이상)가 필요합니다")
        return start + timedelta(days=cycle_days)
    if cycle == BillingCycle.MINUTE:
        if not cycle_minutes or cycle_minutes < 5:
            raise InputValidationError("MINUTE 주기는 cycle_minutes(5 이상)가 필요합니다")
        return start + timedelta(minutes=cycle_minutes)
    raise InputValidationError(f"지원하지 않는 결제 주기입니다: {cycle}")
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_billing_math.py -v`
Expected: PASS (기존 + 신규 minute 테스트)

- [ ] **Step 6: 커밋**

```bash
git add app/models/enums.py app/services/billing_math.py tests/unit/test_billing_math.py
git commit -m "feat: BillingCycle.MINUTE + compute_period_end 분 단위 분기"
```

---

### Task 2: plans.cycle_minutes 컬럼 + 마이그레이션 + 팩토리

**Files:**
- Modify: `app/models/plan.py:33` (cycle_minutes 컬럼 추가)
- Create: `alembic/versions/<rev>_plan_cycle_minutes.py`
- Modify: `tests/factories.py:35-43` (create_plan 팩토리에 cycle_minutes)

**Interfaces:**
- Produces: `Plan.cycle_minutes: int | None`; 팩토리 `create_plan(..., cycle_minutes=None)`.

- [ ] **Step 1: 모델에 컬럼 추가** — `app/models/plan.py` (cycle_days 줄 바로 아래)

```python
    cycle_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # DAY 주기일 때 실제 일수; 나머지는 NULL
    cycle_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # MINUTE 주기일 때 실제 분(5 이상); 나머지는 NULL. 테스트용·비운영 전용
```

- [ ] **Step 2: alembic 마이그레이션 생성**

Run: `uv run alembic revision -m "plan cycle_minutes"`
(또는 autogenerate: `uv run alembic revision --autogenerate -m "plan cycle_minutes"`)

- [ ] **Step 3: 마이그레이션 본문 작성** — 생성된 파일의 upgrade/downgrade

```python
def upgrade() -> None:
    # MINUTE 주기 요금제의 분 수 보관 컬럼(nullable). 기존 행은 NULL.
    op.add_column("plans", sa.Column("cycle_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("plans", "cycle_minutes")
```

- [ ] **Step 4: 마이그레이션 적용 확인**

Run: `uv run alembic upgrade head`
Expected: 오류 없이 적용. `uv run alembic current` 로 head 확인.

- [ ] **Step 5: 팩토리 갱신** — `tests/factories.py` create_plan

```python
async def create_plan(db, service, *, name="기본 요금제", price=10000,
                      billing_cycle="MONTH", cycle_days=None, cycle_minutes=None,
                      ...):
    plan = Plan(service_id=service.id, name=name, price=price,
                billing_cycle=billing_cycle, cycle_days=cycle_days,
                cycle_minutes=cycle_minutes,
                ...)
```

(기존 인자/본문은 유지하고 `cycle_minutes`만 시그니처와 `Plan(...)`에 추가한다.)

- [ ] **Step 6: 커밋**

```bash
git add app/models/plan.py alembic/versions/ tests/factories.py
git commit -m "feat: plans.cycle_minutes 컬럼 + 마이그레이션"
```

---

### Task 3: 검증 + create_plan 연결 (최소 5분 · 비운영 가드)

**Files:**
- Modify: `app/services/plans.py` (`_validate_plan_fields`, `create_plan`, import)
- Test: `tests/integration/test_plans_service.py`

**Interfaces:**
- Consumes: `BillingCycle.MINUTE`, `default_settings().environment`.
- Produces: `create_plan(..., cycle_minutes: int | None = None, environment: str | None = None)`; `_validate_plan_fields(..., cycle_minutes, environment)`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_plans_service.py`

```python
import pytest
from app.core.errors import InputValidationError
from app.services import plans as plan_service

@pytest.mark.asyncio
async def test_create_minute_plan_dev(db, service):
    # 비운영(dev)에서는 MINUTE + cycle_minutes>=5 허용
    plan = await plan_service.create_plan(
        db, service_id=service.id, name="분테스트", price=1000,
        billing_cycle="MINUTE", cycle_minutes=5, environment="dev")
    assert plan.billing_cycle == "MINUTE"
    assert plan.cycle_minutes == 5

@pytest.mark.asyncio
async def test_create_minute_plan_min_5(db, service):
    with pytest.raises(InputValidationError):
        await plan_service.create_plan(
            db, service_id=service.id, name="x", price=1000,
            billing_cycle="MINUTE", cycle_minutes=4, environment="dev")

@pytest.mark.asyncio
async def test_create_minute_plan_rejected_in_prod(db, service):
    with pytest.raises(InputValidationError):
        await plan_service.create_plan(
            db, service_id=service.id, name="x", price=1000,
            billing_cycle="MINUTE", cycle_minutes=5, environment="prod")

@pytest.mark.asyncio
async def test_cycle_minutes_forbidden_on_non_minute(db, service):
    with pytest.raises(InputValidationError):
        await plan_service.create_plan(
            db, service_id=service.id, name="x", price=1000,
            billing_cycle="MONTH", cycle_minutes=5, environment="dev")
```

(`db`, `service` 픽스처는 기존 `tests/integration/conftest.py`/`test_plans_service.py` 패턴을 따른다. 기존 파일의 픽스처 이름이 다르면 그 이름을 사용한다.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/integration/test_plans_service.py -k minute -v`
Expected: FAIL (create_plan에 cycle_minutes/environment 인자 없음)

- [ ] **Step 3: import 추가** — `app/services/plans.py` 상단

```python
from app.core.config import default_settings
from app.models.enums import BillingCycle  # 이미 import되어 있으면 생략
```

- [ ] **Step 4: `_validate_plan_fields` 확장**

```python
def _validate_plan_fields(*, price: int, billing_cycle: str, cycle_days: int | None,
                          cycle_minutes: int | None,
                          first_payment_type: str, first_payment_value: int | None,
                          environment: str) -> None:
    """기본 요금제 필드 검증.

    규칙(주기 관련):
    - billing_cycle: BillingCycle 열거값에 없으면 거부
    - DAY: cycle_days 1 이상 필수, cycle_minutes 전달 금지
    - MINUTE: cycle_minutes 5 이상 필수, cycle_days 전달 금지
             + 비운영 전용 — environment == "prod"이면 거부(테스트용 주기)
    - 그 외(YEAR/MONTH/WEEK): cycle_days·cycle_minutes 둘 다 전달 금지
    (first_payment 규칙은 기존과 동일)
    """
    if price <= 0:
        raise InputValidationError("가격은 1원 이상이어야 합니다")
    if billing_cycle not in tuple(BillingCycle):
        raise InputValidationError(f"지원하지 않는 결제 주기입니다: {billing_cycle}")
    if billing_cycle == BillingCycle.DAY:
        if not cycle_days or cycle_days < 1:
            raise InputValidationError("DAY 주기는 cycle_days(1 이상)가 필요합니다")
        if cycle_minutes is not None:
            raise InputValidationError("cycle_minutes는 MINUTE 주기에서만 사용합니다")
    elif billing_cycle == BillingCycle.MINUTE:
        if environment == "prod":
            raise InputValidationError("MINUTE 주기는 비운영 환경에서만 사용합니다")
        if not cycle_minutes or cycle_minutes < 5:
            raise InputValidationError("MINUTE 주기는 cycle_minutes(5 이상)가 필요합니다")
        if cycle_days is not None:
            raise InputValidationError("cycle_days는 DAY 주기에서만 사용합니다")
    else:
        if cycle_days is not None:
            raise InputValidationError("cycle_days는 DAY 주기에서만 사용합니다")
        if cycle_minutes is not None:
            raise InputValidationError("cycle_minutes는 MINUTE 주기에서만 사용합니다")
    # ── first_payment 검증(기존 로직 그대로 유지) ──
    if first_payment_type not in tuple(FirstPaymentType):
        raise InputValidationError(f"지원하지 않는 첫결제 유형입니다: {first_payment_type}")
    if first_payment_type in (FirstPaymentType.NONE, FirstPaymentType.FREE):
        if first_payment_value is not None:
            raise InputValidationError("첫결제 값은 할인 유형에서만 사용합니다")
    else:
        if first_payment_value is None or first_payment_value < 1:
            raise InputValidationError("할인 값은 1 이상이어야 합니다")
        if first_payment_type == FirstPaymentType.DISCOUNT_PERCENT and first_payment_value > 100:
            raise InputValidationError("할인율은 1~100 사이여야 합니다")
```

- [ ] **Step 5: `create_plan` 시그니처/본문 연결** — `app/services/plans.py`

```python
async def create_plan(db: AsyncSession, *, service_id: uuid.UUID, name: str, price: int,
                      billing_cycle: str, cycle_days: int | None = None,
                      cycle_minutes: int | None = None,   # MINUTE 주기 분 수(5 이상); 비운영 전용
                      first_payment_type: str = "NONE",
                      first_payment_value: int | None = None,
                      recurring_discount_type: str = "NONE",
                      recurring_discount_value: int | None = None,
                      trial_enabled: bool = False, trial_days: int | None = None,
                      auto_renew: bool = True,
                      extra_info: dict | None = None,
                      environment: str | None = None,      # MINUTE 비운영 가드용(None=현재 실행 환경)
                      actor_user_id: uuid.UUID | None = None) -> Plan:
    ...
    if not name or not name.strip():
        raise InputValidationError("요금제 이름은 필수입니다")
    env = environment if environment is not None else default_settings().environment
    _validate_plan_fields(price=price, billing_cycle=billing_cycle, cycle_days=cycle_days,
                          cycle_minutes=cycle_minutes,
                          first_payment_type=first_payment_type,
                          first_payment_value=first_payment_value, environment=env)
    _validate_recurring_discount(recurring_discount_type, recurring_discount_value)
    _validate_trial(trial_enabled, trial_days)
    plan = Plan(service_id=service_id, name=name.strip(), price=price,
                billing_cycle=billing_cycle, cycle_days=cycle_days,
                cycle_minutes=cycle_minutes,
                first_payment_type=first_payment_type,
                ...  # 나머지 기존 필드 그대로
                )
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `uv run pytest tests/integration/test_plans_service.py -v`
Expected: PASS (신규 minute 4종 + 기존 회귀)

- [ ] **Step 7: 커밋**

```bash
git add app/services/plans.py tests/integration/test_plans_service.py
git commit -m "feat: MINUTE 검증(최소 5분·비운영 가드) + create_plan 연결"
```

---

### Task 4: compute_period_end 호출부 3곳에 cycle_minutes 전달

**Files:**
- Modify: `app/services/subscriptions.py:232`, `:440`
- Modify: `app/services/renewals.py:124`
- Modify: `tests/factories.py:146` (create_subscription의 period_end 계산)
- Test: `tests/integration/test_renewals.py`

**Interfaces:**
- Consumes: `Plan.cycle_minutes`, `compute_period_end(..., cycle_minutes=...)`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_renewals.py`

```python
@pytest.mark.asyncio
async def test_minute_subscription_period_end(db, cipher, service):
    # MINUTE 요금제 구독은 기간 종료일이 시작 + cycle_minutes 분
    from tests.factories import create_plan, create_subscription
    plan = await create_plan(db, service, billing_cycle="MINUTE", cycle_minutes=5)
    sub = await create_subscription(db, cipher, service, plan)
    delta = sub.current_period_end - sub.current_period_start
    assert delta.total_seconds() == 5 * 60
```

(픽스처 이름은 `tests/integration/test_renewals.py`/`conftest.py`의 기존 것을 사용. `create_subscription` 호출 시 period_end 미지정이면 팩토리가 compute_period_end로 계산.)

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/integration/test_renewals.py -k minute -v`
Expected: FAIL (팩토리/서비스가 cycle_minutes 미전달 → MINUTE에서 InputValidationError)

- [ ] **Step 3: subscriptions.py 두 호출부 수정** — `app/services/subscriptions.py:232`, `:440`

```python
# :232 부근
        period_end = compute_period_end(now, plan.billing_cycle, plan.cycle_days,
                                        plan.cycle_minutes)
# :440 부근
    sub.current_period_end = compute_period_end(now, plan.billing_cycle, plan.cycle_days,
                                                plan.cycle_minutes)
```

- [ ] **Step 4: renewals.py 호출부 수정** — `app/services/renewals.py:124`

```python
    sub.current_period_end = compute_period_end(new_start, plan.billing_cycle,
                                                plan.cycle_days, plan.cycle_minutes)
```

- [ ] **Step 5: 팩토리 period_end 계산 수정** — `tests/factories.py:146`

```python
    end = period_end or compute_period_end(start, plan.billing_cycle, plan.cycle_days,
                                           plan.cycle_minutes)
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `uv run pytest tests/integration/test_renewals.py tests/integration/test_subscription_create.py -v`
Expected: PASS (MINUTE 신규 + 기존 회귀)

- [ ] **Step 7: 커밋**

```bash
git add app/services/subscriptions.py app/services/renewals.py tests/factories.py tests/integration/test_renewals.py
git commit -m "feat: 구독 생성·갱신 기간계산에 cycle_minutes 전달"
```

---

### Task 5: API 스키마 PlanResponse에 cycle_minutes 노출

**Files:**
- Modify: `app/schemas/api.py:62-63`, `:79` (PlanResponse 필드 + from_model)
- Test: `tests/integration/test_api_endpoints.py`

**Interfaces:**
- Produces: `PlanResponse.cycle_minutes: int | None`; billing_cycle 설명에 MINUTE 포함.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_api_endpoints.py` (요금제 목록/응답 검증 테스트에 추가)

```python
@pytest.mark.asyncio
async def test_plan_response_exposes_cycle_minutes(db, service):
    from app.schemas.api import PlanResponse
    from tests.factories import create_plan
    plan = await create_plan(db, service, billing_cycle="MINUTE", cycle_minutes=5)
    resp = PlanResponse.from_model(plan)
    assert resp.billing_cycle == "MINUTE"
    assert resp.cycle_minutes == 5
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/integration/test_api_endpoints.py -k cycle_minutes -v`
Expected: FAIL (PlanResponse에 cycle_minutes 없음)

- [ ] **Step 3: 스키마 수정** — `app/schemas/api.py`

```python
    billing_cycle: str = Field(description="결제 주기: YEAR | MONTH | WEEK | DAY | MINUTE.")
    cycle_days: int | None = Field(
        description="DAY 주기일 때의 실제 일수. 그 외 주기에서는 null.")
    cycle_minutes: int | None = Field(
        default=None,
        description="MINUTE 주기일 때의 실제 분(5 이상). 그 외 주기에서는 null. 테스트용·비운영 전용.")
```

`from_model`(:79 부근)에 매핑 추가:

```python
                   billing_cycle=plan.billing_cycle, cycle_days=plan.cycle_days,
                   cycle_minutes=plan.cycle_minutes,
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/integration/test_api_endpoints.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/schemas/api.py tests/integration/test_api_endpoints.py
git commit -m "feat: PlanResponse에 cycle_minutes 노출 + MINUTE 설명"
```

---

### Task 6: 어드민 라우트 — cycle_minutes 파싱 · 라벨 · is_prod 컨텍스트

**Files:**
- Modify: `app/admin/routes/plans.py` (import, 폼 렌더 라우트 226/261, create 라우트 237·287, 목록 라벨 180)

**Interfaces:**
- Consumes: `get_settings`, `Settings`, `create_plan(..., cycle_minutes, environment)`.
- Produces: 템플릿 컨텍스트 `is_prod`(bool); create 라우트가 `cycle_minutes`·`environment` 전달.

- [ ] **Step 1: import 추가** — `app/admin/routes/plans.py` 상단

```python
from app.core.config import Settings
from app.core.deps import get_settings
```

- [ ] **Step 2: 목록 라벨에 분 표시** — `plans_list`(:180 부근)

```python
        cycle = plan.billing_cycle
        if plan.cycle_days:
            cycle += f" {plan.cycle_days}일"
        elif plan.cycle_minutes:
            cycle += f" {plan.cycle_minutes}분"
```

- [ ] **Step 3: 폼 렌더 라우트에 is_prod 전달** — `plans_new`(:226)와 서비스 스코프 new 라우트(:261 인근)

```python
async def plans_new(request: Request, ctx: AdminContext = Depends(require_manager),
                    settings: Settings = Depends(get_settings)):
    # 비운영에서만 MINUTE(분) 옵션 노출 → 운영 실수 과금 방지
    return render(request, "plans/form.html", ctx=ctx, plan=None,
                  action="/admin/plans", next_url="/admin/plans",
                  is_prod=(settings.environment == "prod"))
```

(서비스 스코프 new 라우트도 동일하게 `settings` 주입 + `is_prod` 컨텍스트 추가. 수정(edit) 폼은 주기 변경 불가이므로 분 옵션을 강제로 숨겨도 무방 — `is_prod=True`로 넘기거나 기존대로 둔다. 본 계획은 생성 폼만 분 옵션을 노출한다.)

- [ ] **Step 4: create 라우트 2곳에 cycle_minutes·environment 전달** — `:237`, `:287` 라우트

각 create 라우트에 `settings: Settings = Depends(get_settings)` 주입 후:

```python
    cycle_days_raw = str(form.get("cycle_days", "")).strip()
    cycle_minutes_raw = str(form.get("cycle_minutes", "")).strip()
    try:
        fields = _form_plan_fields(form)
        await plan_service.create_plan(
            db, service_id=ctx.user.service_id,   # (서비스 스코프 라우트는 해당 service_id)
            billing_cycle=str(form.get("billing_cycle", "")),
            cycle_days=int(cycle_days_raw) if cycle_days_raw else None,
            cycle_minutes=int(cycle_minutes_raw) if cycle_minutes_raw else None,
            environment=settings.environment,
            actor_user_id=ctx.user.id, **fields)
```

- [ ] **Step 5: 폼 에러 재렌더 시에도 is_prod 유지** — create 라우트의 `except DomainError` 블록 `render(...)`에 `is_prod=(settings.environment == "prod")` 추가(분 옵션이 사라지지 않게).

- [ ] **Step 6: 수동 확인(어드민 기동)**

Run: `uv run uvicorn app.main:app --port 8000` 후 `/admin/plans/new`에서 결제주기 셀렉트에 "분" 노출(dev) 확인. (자동화 어려우면 Task 7 후 함께 확인)

- [ ] **Step 7: 커밋**

```bash
git add app/admin/routes/plans.py
git commit -m "feat: 어드민 요금제 create에 cycle_minutes·비운영 가드 연결"
```

---

### Task 7: 어드민 템플릿 — 분 옵션·입력칸·목록 표시

**Files:**
- Modify: `app/admin/templates/plans/form.html:27-31`
- Modify: `app/admin/templates/plans/_table.html:7`, `:45`

**Interfaces:**
- Consumes: 템플릿 컨텍스트 `is_prod`, `plan.cycle_minutes`.

- [ ] **Step 1: 폼에 분 옵션 + 분 입력칸** — `app/admin/templates/plans/form.html`

셀렉트 onchange를 DAY·MINUTE 모두 토글하도록 바꾸고, 비운영에서만 MINUTE 옵션을 렌더한다:

```html
  <select id="billing_cycle" name="billing_cycle"
          onchange="document.getElementById('cd').style.display=this.value==='DAY'?'block':'none';
                    document.getElementById('cm').style.display=this.value==='MINUTE'?'block':'none'">
    <option value="MONTH">월</option>
    <option value="YEAR">년</option>
    <option value="WEEK">주</option>
    <option value="DAY">일</option>
    {% if not is_prod %}
    <option value="MINUTE">분 (테스트용)</option>
    {% endif %}
  </select>
  <!-- DAY 일수 입력칸(기존 'cd')은 그대로 두고, MINUTE 분 입력칸 'cm' 추가 -->
  <div id="cm" style="display:none">
    <label for="cycle_minutes">결제 주기(분) — 최소 5분, 테스트용</label>
    <input id="cycle_minutes" name="cycle_minutes" type="number" min="5" step="1"
           value="{{ plan.cycle_minutes or '' }}">
  </div>
```

(기존 `cd`(일수) div의 표시 로직은 유지. 위 onchange가 두 칸을 상호 토글한다.)

- [ ] **Step 2: 목록 필터·표시** — `app/admin/templates/plans/_table.html`

필터 드롭다운(:7)에 분 추가:

```jinja
    ('billing_cycle', [('','전체 주기'),('YEAR','연'),('MONTH','월'),('WEEK','주'),('DAY','일'),('MINUTE','분')], cycle_filter),
```

표시 셀(:45):

```jinja
      <td>{{ plan.billing_cycle }}{% if plan.cycle_days %}({{ plan.cycle_days }}일){% elif plan.cycle_minutes %}({{ plan.cycle_minutes }}분){% endif %}</td>
```

- [ ] **Step 3: 수동 확인**

Run: 어드민 `/admin/plans/new`(dev)에서 "분 (테스트용)" 선택 → 분 입력칸 표시(min 5) → 5 입력·저장 → 목록에 `MINUTE(5분)` 표시. 운영 모드(`APP_ENV=prod`)로 띄우면 "분" 옵션이 사라지고, 강제로 MINUTE를 POST해도 서버가 거부하는지 확인.

- [ ] **Step 4: 전체 테스트 회귀**

Run: `uv run pytest -q`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add app/admin/templates/plans/form.html app/admin/templates/plans/_table.html
git commit -m "feat: 어드민 요금제 폼/목록에 분 단위(비운영) 노출"
```

---

### Task 8: 문서(매뉴얼) 갱신 + 워크로그

**Files:**
- Modify: 결제주기를 설명하는 매뉴얼 md (`docs/manual/dev_manual/08-plans.md` 또는 결제주기 언급 문서; `grep -rl "년/월/주/일\|YEAR.*MONTH.*WEEK.*DAY\|결제 주기" docs/manual docs/user_manual`로 확인)
- Run: 매뉴얼 빌드 스크립트
- Create: `docs/audit/2026-06-23-minute-billing-cycle-worklog.md`

- [ ] **Step 1: 매뉴얼 결제주기 설명 갱신**

대상 문서를 찾는다:
```bash
grep -rln "결제 주기\|YEAR\|MONTH\|WEEK\|DAY\|년/월/주/일" docs/manual docs/user_manual
```
찾은 문서의 결제주기 설명에 다음을 추가: "MINUTE(분) 주기 — 자동연장 테스트용, 비운영 환경 전용, 최소 5분. 스케줄러 기본 스윕 5분이라 그 주기로 갱신됨."

- [ ] **Step 2: 매뉴얼 재빌드**

Run: `uv run --with markdown python docs/user_manual/build.py`
(개발자 매뉴얼이 별도 빌드면 `docs/manual/dev_manual/build_html.py`도 실행)
Expected: 문서 재생성 완료 메시지.

- [ ] **Step 3: 워크로그 작성** — `docs/audit/2026-06-23-minute-billing-cycle-worklog.md`

내용: 목적(자동연장 테스트용), 결정(비운영 전용·최소 5분·새 컬럼), 변경 파일 목록(enums/billing_math/plan/마이그레이션/plans/subscriptions/renewals/api/admin routes·templates), 검증(pytest 전체 통과·어드민 수동 확인), 스케줄러 주의(빠른 관찰 시 scheduler_interval_minutes 낮추기), 설계문서 링크(`docs/superpowers/specs/2026-06-23-minute-billing-cycle-design.md`).

- [ ] **Step 4: 커밋**

```bash
git add docs/
git commit -m "docs: MINUTE 결제주기 매뉴얼 갱신 + 워크로그"
```

---

## Self-Review (작성자 점검 결과)

- **Spec coverage:** enum(T1)·compute_period_end(T1)·컬럼+마이그레이션(T2)·검증/최소5분/비운영가드(T3)·호출부 연결(T4)·API(T5)·어드민 라우트(T6)·템플릿(T7)·스케줄러 주의 문서화(T8)·테스트(각 태스크)·매뉴얼/워크로그(T8) — 스펙 전 항목 커버.
- **Placeholder scan:** 모든 코드 스텝에 실제 코드/명령/기대결과 포함. "적절히 처리" 류 없음.
- **Type consistency:** `compute_period_end(start, cycle, cycle_days=None, cycle_minutes=None)` 시그니처가 T1 정의 → T4 호출과 일치. `create_plan(..., cycle_minutes, environment)` T3 정의 → T6 호출 일치. `PlanResponse.cycle_minutes` T5 정의 → from_model 매핑 일치. `_validate_plan_fields(..., cycle_minutes, environment)` T3 내부 일관.
- **주의:** 픽스처 이름(`db`/`service`/`cipher`)은 각 테스트 파일의 기존 conftest 관례에 맞춰 실제 이름 사용(파일별 상이 가능).
