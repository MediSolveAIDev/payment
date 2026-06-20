# 구조 리팩터 2차 S5·S6·S7 구현 계획 (동작 보존)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** service_options 중복 제거(S5)·정산 건별 쿼리 중복 제거(S6)·`DiscountType` enum 분리(S7)를 동작 변경 없이 수행한다.

**Architecture:** 모두 순수 추출/정리(URL·응답·집계 결과·DB 스키마 무변경). 기존 417 테스트가 동작 보존을 검증한다. S7은 Python 레이어 enum만 분리(저장 문자열 동일, 마이그레이션 없음).

**Tech Stack:** FastAPI, SQLAlchemy 2 async, pytest

**근거 레포트:** 직전 구조 분석 레포트(S5~S7).

## 파일 구조
- `app/admin/filters.py` — `service_options(db, scope, *, include_all=True)` 추가 (S5)
- `app/admin/routes/{subscriptions,payments,plans,settlement}.py` — service_options 호출로 교체 (S5)
- `app/admin/routes/settlement.py` — `_build_settlement_payment_query` 추출 (S6)
- `app/models/enums.py` — `DiscountType` 추가 (S7)
- `app/models/plan.py`·`app/services/billing_math.py`·`app/services/plans.py` — recurring 할인에 `DiscountType` 사용 (S7)

---

### Task 1 (S5): `service_options` 공용 헬퍼

**Files:**
- Modify: `app/admin/filters.py`, `app/admin/routes/subscriptions.py`, `app/admin/routes/payments.py`, `app/admin/routes/plans.py`, `app/admin/routes/settlement.py`

현재 4곳에서 `select(Service.id, Service.name).order_by(Service.name)` + scope 필터 + `[("","전체 서비스")]` prefix 패턴 반복:
- `subscriptions.py:88-92`(prefix 있음), `payments.py:104-107`(prefix 있음), `plans.py:121-124`(prefix 있음), `settlement.py:90-92`(**prefix 없음**).

- [ ] **Step 1: 헬퍼 추가** — `app/admin/filters.py`에 추가(`Service` import 필요):
```python
from app.models import Plan, Service


async def service_options(db: AsyncSession, scope: list[uuid.UUID] | None,
                          *, include_all: bool = True) -> list[tuple[str, str]]:
    """서비스 드롭다운 옵션. 스코프 내, 이름순. include_all이면 맨 앞에 '전체 서비스'."""
    q = select(Service.id, Service.name).order_by(Service.name)
    if scope is not None:
        q = q.where(Service.id.in_(scope))
    opts = [(str(sid), name) for sid, name in (await db.execute(q)).all()]
    return ([("", "전체 서비스")] + opts) if include_all else opts
```
(기존 `plan_name_options`의 import 블록에 `Service` 추가.)

- [ ] **Step 2: 4개 라우트 교체**
  - `subscriptions.py`: `svc_q...` 3줄 → `service_options = await service_options(db, scope)`. (변수명 충돌 주의: 결과 변수명이 `service_options`이고 함수명도 동일 → 함수는 `from app.admin.filters import service_options`로 import하되 결과를 `service_options`에 담으면 이후 호출 불가. **결과 변수명을 그대로 쓰되 import 별칭 없이** 한 번만 호출하므로 문제 없음 — 호출 후 재호출 안 함. 명확성을 위해 `service_options = await service_options(...)`는 같은 이름 재바인딩이라 동작하나 가독성 나쁨 → import는 `service_options`로 하고 호출 결과는 그대로 `service_options` 변수에 저장(파이썬상 호출 시점엔 함수가 바인딩돼 있어 OK). 안전하게: render kwargs가 `service_options=...`를 기대하므로 지역 변수명은 유지. 함수 호출은 한 번뿐이라 재바인딩 무해.)
  - `payments.py`: 동일.
  - `plans.py`: 동일(이미 `plan_name_options` import 중이니 같은 줄에 `service_options` 추가).
  - `settlement.py`: `service_options = await service_options(db, scope, include_all=False)` (prefix 없음 유지). settlement는 `ctx.service_ids`를 `scope`로 이미 보유.
  import: 각 파일에서 `from app.admin.filters import plan_name_options, service_options`(plans/subscriptions/payments/settlement 모두 plan_name_options 이미 import).

  > 변수명 재바인딩이 꺼림칙하면 import를 `from app.admin.filters import service_options as build_service_options` 별칭으로 하고 `service_options = await build_service_options(...)`로 명확화해도 됨. 구현자가 택일.

- [ ] **Step 3: 통과 확인** — Run: `uv run pytest tests/e2e -q` → 전체 PASS(구독·결제·요금제·정산 서비스 드롭다운 렌더·필터 회귀).
  잔여: `grep -rn "select(Service.id, Service.name)" app/admin/routes` → 0건(filters.py로 일원화).

- [ ] **Step 4: 커밋**
```bash
git add app/admin/filters.py app/admin/routes/subscriptions.py app/admin/routes/payments.py app/admin/routes/plans.py app/admin/routes/settlement.py
git commit -m "refactor(S5): service_options 드롭다운 빌더 공용화

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2 (S6): 정산 건별 쿼리 중복 제거

**Files:**
- Modify: `app/admin/routes/settlement.py`

`settlement_view`(76-89)와 `settlement_export`(119-131)에 동일한 결제 건별 base 쿼리(outerjoin + plan_name join + 기간 필터)가 중복.

- [ ] **Step 1: 헬퍼 추출** — `app/admin/routes/settlement.py`에 추가(`_settlement_context` 근처):
```python
def _settlement_payment_query(selected: Service, plan_name: str | None, start, end):
    """서비스별 모드 결제 건별 base 쿼리(정렬 미적용). view/export 공용."""
    base = (select(Payment, Subscription)
            .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
            .where(Payment.status == PaymentStatus.DONE,
                   Payment.service_id == selected.id))
    if plan_name:
        base = (base.join(Plan, Subscription.plan_id == Plan.id)
                .where(Plan.name == plan_name))
    if start:
        base = base.where(Payment.approved_at >= start)
    if end:
        base = base.where(Payment.approved_at < end)
    return base
```

- [ ] **Step 2: settlement_view 교체** — 76-89의 base 구성 블록을:
```python
    pay_page = None
    if selected:
        base = _settlement_payment_query(selected, plan_name, start, end)
        count_q = select(func.count()).select_from(base.order_by(None).subquery())
        items_q = base.order_by(pp.order_by(_SETTLE_SORT))
        pay_page = await paginate(db, items_q, count_q, pp)
```

- [ ] **Step 3: settlement_export 교체** — 119-131의 base 구성 블록을:
```python
    if selected:   # 서비스별 모드 — 결제 건별
        base = _settlement_payment_query(selected, plan_name, start, end)
        rows = []
        for p, _sub in (await db.execute(base.order_by(pp.order_by(_SETTLE_SORT)))).all():
            kind_ko = "구독" if p.kind == PaymentKind.SUBSCRIPTION else "일반"
            rows.append([kst_format(p.approved_at, "%Y-%m-%d %H:%M"),
                         p.external_user_id or "-", p.order_id, p.payment_type,
                         kind_ko, p.amount])
        return xlsx_response(f"settlement-{selected.name}",
                             ["승인시각", "사용자", "주문번호", "유형", "종류", "금액"],
                             rows, sheet_title="정산")
```

- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/e2e/test_settlement_page.py tests/e2e/test_list_export.py -q` → PASS(서비스별 모드 목록·요금제 필터·export 회귀). 이어서 `uv run pytest -q`.

- [ ] **Step 5: 커밋**
```bash
git add app/admin/routes/settlement.py
git commit -m "refactor(S6): 정산 결제 건별 쿼리를 _settlement_payment_query로 공용화

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3 (S7): `DiscountType` enum 분리 (상시 할인)

**Files:**
- Modify: `app/models/enums.py`, `app/models/plan.py`, `app/services/billing_math.py`, `app/services/plans.py`, `app/models/__init__.py`
- Test: `tests/integration/test_plans.py`(또는 billing_math 테스트 위치)

`recurring_discount_type`(상시 할인)이 `FirstPaymentType`(첫 결제 유형) enum을 재사용 중. 상시 할인 유효값은 `NONE/DISCOUNT_AMOUNT/DISCOUNT_PERCENT`(FREE 금지)인데 `FirstPaymentType`엔 FREE가 있어 의미 혼동. **DB 저장 문자열은 동일**하므로 마이그레이션 없이 Python enum만 분리.

- [ ] **Step 1: enum 추가** — `app/models/enums.py`에 `FirstPaymentType` 아래:
```python
class DiscountType(StrEnum):
    """상시(정기) 결제 할인 유형. 첫 결제(FirstPaymentType)와 달리 FREE 없음."""
    NONE = "NONE"
    DISCOUNT_AMOUNT = "DISCOUNT_AMOUNT"
    DISCOUNT_PERCENT = "DISCOUNT_PERCENT"
```
`app/models/__init__.py`의 enums import 블록 + `__all__`에 `DiscountType` 추가.

- [ ] **Step 2: 모델 기본값** — `app/models/plan.py`의 `recurring_discount_type` 컬럼 default를 `DiscountType.NONE`으로(문자열 "NONE" 동일, server_default도 동일 문자열). import에 `DiscountType` 추가. `first_payment_type`은 `FirstPaymentType` 유지.
```python
    recurring_discount_type: Mapped[str] = mapped_column(
        String(20), default=DiscountType.NONE, server_default=DiscountType.NONE)
```

- [ ] **Step 3: billing_math 교체** — `app/services/billing_math.py`에서 **상시 할인** 비교에 쓰인 `FirstPaymentType`을 `DiscountType`으로:
  - import에 `DiscountType` 추가.
  - `compute_recurring_amount`(52/54/56줄)의 `FirstPaymentType.NONE/DISCOUNT_AMOUNT/DISCOUNT_PERCENT` → `DiscountType.*`.
  - `recurring_amount_breakdown`(82-89줄, `t = plan.recurring_discount_type`)의 `FirstPaymentType.DISCOUNT_AMOUNT/PERCENT` → `DiscountType.*`.
  - **첫 결제** 관련 비교(`compute_first_amount`의 30-36줄, `first_amount_breakdown`의 97-103줄 FREE 포함)는 `FirstPaymentType` 그대로 둔다. (StrEnum 문자열 동등이라 동작 무변경.)

- [ ] **Step 4: plans 검증 교체** — `app/services/plans.py`에서 **상시 할인 검증**(`_validate_recurring_discount`, 50/54-55/59줄)과 update의 `new_rdt == FirstPaymentType.NONE`(130줄)을 `DiscountType`으로:
  - import에 `DiscountType` 추가.
  - 50: `if discount_type == DiscountType.NONE:`
  - 54-55: `if discount_type not in (DiscountType.DISCOUNT_AMOUNT, DiscountType.DISCOUNT_PERCENT):`
  - 59: `if discount_type == DiscountType.DISCOUNT_PERCENT and discount_value > 100:`
  - 130: `None if new_rdt == DiscountType.NONE else ...`
  (첫 결제 검증 28-36줄, 121줄의 FirstPaymentType은 그대로.)

- [ ] **Step 5: 동등성 테스트** — `tests/integration/test_plans.py`(없으면 적절한 위치)에 DiscountType 값이 기존 문자열과 동일·검증 동작 보존을 확인:
```python
async def test_recurring_discount_type_enum_values(db, cipher):
    from app.models.enums import DiscountType, FirstPaymentType
    # 저장 문자열 동일성(마이그레이션 불필요 근거)
    assert DiscountType.NONE == "NONE"
    assert DiscountType.DISCOUNT_AMOUNT == "DISCOUNT_AMOUNT"
    assert DiscountType.DISCOUNT_PERCENT == "DISCOUNT_PERCENT"
    assert DiscountType.DISCOUNT_AMOUNT == FirstPaymentType.DISCOUNT_AMOUNT  # StrEnum 문자열 동등
    # FREE는 상시 할인에 없음
    assert not hasattr(DiscountType, "FREE")
```
(기존 요금제 생성/금액 계산 테스트가 회귀 보호.)

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/integration/test_plans.py tests/unit -q`(billing_math 단위 테스트 포함) → PASS. 이어서 `uv run pytest -q` 전체.
  잔여: `grep -rn "recurring_discount_type" app | grep "FirstPaymentType"` → 0건(상시 할인은 DiscountType).

- [ ] **Step 7: 커밋**
```bash
git add app/models/enums.py app/models/plan.py app/models/__init__.py app/services/billing_math.py app/services/plans.py tests/integration/test_plans.py
git commit -m "refactor(S7): 상시 할인에 DiscountType enum 분리(FirstPaymentType 재사용 제거)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 전체 검증 + 최종 리뷰
- [ ] **Step 1: 전체 테스트** — Run: `uv run pytest -q` → 전체 PASS(417+).
- [ ] **Step 2: 잔여 확인**
  - `grep -rn "select(Service.id, Service.name)" app/admin/routes` → 0(S5).
  - settlement.py에 결제 건별 base 구성이 `_settlement_payment_query` 1곳만(S6).
  - `grep -rn "recurring_discount_type" app | grep FirstPaymentType` → 0(S7).
- [ ] **Step 3: 최종 코드리뷰** — 동작 보존(결과·DB 문자열 동일) 중심.

## 변경하지 않는 것
- DB 스키마/마이그레이션(S7은 Python enum만), URL·응답·집계 결과, 첫 결제(FirstPaymentType) 로직.
