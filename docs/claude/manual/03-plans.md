# 03. 요금제 생성 · 금액 계산(할인 / 주기)

> 요금제(Plan)는 "얼마를, 어떤 주기로, 어떤 할인으로 받을지"의 정의다.
> 이 문서의 **금액 계산 규칙**(`billing_math.py`)은 이후 구독 생성(04)·자동 갱신(05)에서
> 실제 청구 금액의 근거가 되므로 가장 중요하다.
>
> 선행: [00-overview.md](00-overview.md), [02-admin-auth.md](02-admin-auth.md)의 권한/스코프.

---

## 0. 한눈에 보기

요금제는 **특정 서비스에 속한다**(Service 1—N Plan). 관리 주체:
- **`SERVICE_MANAGER`** — 자기 서비스의 요금제 생성/수정/보관/삭제(`/admin/plans`).
- **`SYSTEM_ADMIN` 또는 해당 담당자** — 서비스 상세 화면에서 그 서비스의 요금제 생성.

| 하는 일 | HTTP | URL | 라우트 | 서비스 계층 |
|---|---|---|---|---|
| 목록(필터) | GET | `/admin/plans` | `plans_list` | (직접 쿼리) |
| 생성 폼 | GET | `/admin/plans/new` | `plans_new` | — |
| **생성(담당자 주서비스)** | POST | `/admin/plans` | `plans_create` | `plan_service.create_plan` |
| 생성 폼(서비스 지정) | GET | `/admin/services/{sid}/plans/new` | `service_plan_new` | — |
| **생성(서비스 지정)** | POST | `/admin/services/{sid}/plans` | `service_plan_create` | `plan_service.create_plan` |
| 수정 폼 | GET | `/admin/plans/{id}/edit` | `plans_edit` | — |
| **수정** | POST | `/admin/plans/{id}` | `plans_update` | `plan_service.update_plan` |
| 보관(아카이브) | POST | `/admin/plans/{id}/archive` | `plans_archive` | `plan_service.archive_plan` |
| 삭제 | POST | `/admin/plans/{id}/delete` | `plans_delete` | `plan_service.delete_plan` |

관련 파일:
- 라우트: `app/admin/routes/plans.py`
- 서비스 계층: `app/services/plans.py`(CRUD/검증), **`app/services/billing_math.py`(금액·기간 계산)**
- 모델: `app/models/plan.py`
- **공용 필터 헬퍼**: `app/admin/filters.py` — `plan_name_options(db, scope, service_filter)` (아래 설명)
- 외부 API(읽기 전용): `GET /api/v1/plans` — 외부 서비스가 노출할 요금제 조회(문서 08).

### 요금제명 드롭다운 — `plan_name_options` (`app/admin/filters.py`)

요금제 목록(`/admin/plans`)의 "요금제명" 필터 드롭다운 옵션 빌드는
공용 헬퍼 `plan_name_options(db, scope, service_filter)`로 추출되어 있다:

```python
async def plan_name_options(db, scope, service_filter) -> list[tuple[str, str]]:
    q = select(Plan.name).distinct().order_by(Plan.name)
    if scope is not None:       # SERVICE_MANAGER 스코프 제한
        q = q.where(Plan.service_id.in_(scope))
    if service_filter:          # 서비스 선택 시 그 서비스 요금제만
        q = q.where(Plan.service_id == uuid.UUID(service_filter))
    return [("", "전체 요금제")] + [(n, n) for n in ...]
```

- 서비스 선택이 없으면 스코프 내 전체 요금제 이름을 중복 없이 보여준다.
- **서비스를 선택하면 그 서비스의 요금제 이름만** 드롭다운에 나타난다(연동 필터).
- 같은 헬퍼가 **구독 목록·결제 목록·정산 화면**에서도 동일하게 사용된다(상세는 문서 09).

---

## 1. 데이터 모델 — `Plan` (`app/models/plan.py`)

| 컬럼 | 의미 |
|---|---|
| `service_id` (FK RESTRICT) | 소속 서비스. 구독 있는 서비스는 삭제 불가의 한 축 |
| `name` | 요금제 이름 |
| `price` (BigInteger, KRW) | **정가**. 모든 계산의 기준 |
| `currency` | 통화(기본 KRW) |
| `billing_cycle` | `YEAR`/`MONTH`/`WEEK`/`DAY` |
| `cycle_days` | **DAY 주기에서만** 사용(원하는 일수). 그 외 주기는 반드시 NULL |
| `first_payment_type` | 첫 구독 할인 유형: `NONE`/`FREE`/`DISCOUNT_AMOUNT`/`DISCOUNT_PERCENT` |
| `first_payment_value` | 첫구독 할인 값(원 또는 %). NONE/FREE면 NULL |
| `recurring_discount_type` | **상시 할인** 유형: `NONE`/`DISCOUNT_AMOUNT`/`DISCOUNT_PERCENT`(FREE 불가) |
| `recurring_discount_value` | 상시 할인 값. NONE이면 NULL |
| `trial_enabled` / `trial_days` | 체험 사용 여부 / 체험 일수(체험 켜지면 1 이상) |
| `auto_renew` | `True`(기본) / `False` — False이면 (체험 후) 첫 주기 종료 후 자동연장 없이 만료(배치가 EXPIRED 처리, 문서 05). **`trial_enabled`와 공존 가능**: 체험이면 체험 만료 시 첫 결제 후 그 주기 종료 시 만료, 체험이 없으면 생성 시 결제한 첫 주기 종료 시 만료. |
| `extra_info` | JSONB `dict`(기본 `{}`) — 서비스 측 요금제 설명용 key/value. `PlanResponse` 외부 API 응답에 포함. |
| `status` | `ACTIVE` / `ARCHIVED`(보관 — 신규 구독에서 숨김) |

**핵심 개념(초급자용): 할인이 두 종류다.**
- **첫구독 할인**(`first_payment_*`) — 가입 첫 결제에만 적용.
- **상시 할인**(`recurring_discount_*`) — 2회차부터 매 정기 결제에 적용.
- 이 둘은 **독립적**이며 첫 결제에서 상시 할인은 **적용되지 않는다**(아래 2절).

---

## 2. 금액·기간 계산 (`billing_math.py`) — 가장 중요

이 모듈은 **순수 계산 함수**다(DB·외부호출 없음). 그래서 단독으로 테스트하기 쉽고,
구독/갱신/표시 어디서든 같은 결과를 보장한다. **금액은 항상 서버가 계산**하며 외부 입력을 믿지 않는다.

### 2-1. 첫 결제 금액 — `compute_first_amount(price, type, value)`

```python
NONE             → price                       # 정가
FREE             → 0                            # 무료
DISCOUNT_AMOUNT  → max(0, price - value)        # 원 차감(음수 방지)
DISCOUNT_PERCENT → price - (price * value)//100 # 율 차감(정수 내림)
```

**중요 규칙(요청 005): 첫 결제는 `price`(정가) 기준이며 상시 할인과 무관하다.**

예시(정가 10,000원):
| first_payment_type | value | 첫 결제액 |
|---|---|---|
| NONE | — | 10,000 |
| FREE | — | 0 |
| DISCOUNT_AMOUNT | 3,000 | 7,000 |
| DISCOUNT_PERCENT | 30 | 7,000 |
| DISCOUNT_PERCENT | 33 | 6,700 (`10000 - 3300`) |

### 2-2. 정기 결제 금액 — `compute_recurring_amount(price, type, value)`

```python
NONE             → price
DISCOUNT_AMOUNT  → max(0, price - value)
DISCOUNT_PERCENT → price - (price * value)//100
```

2회차부터(그리고 체험 만료 후 첫 자동결제) 적용되는 **상시 할인가**.
계산식은 첫결제와 같지만 **입력 할인이 다르다**(상시 할인 필드를 씀). FREE는 허용 안 함(정기 결제가 0원일 수는 없음).

### 2-3. 두 할인의 조합 (헷갈리기 쉬운 핵심)

정가 10,000원, 첫구독 50% 할인, 상시 10% 할인인 요금제라면:
- **가입 첫 결제** = `plan_first_amount` = 10,000 × 50% = **5,000원** (상시 할인 무시)
- **2회차 이후 / 체험 만료 후** = `plan_recurring_amount` = 10,000 − 10% = **9,000원**

즉 첫 결제와 정기 결제는 **서로 다른 함수**로 계산된다:
- `plan_first_amount(plan)` → `compute_first_amount(price, first_*)`
- `plan_recurring_amount(plan)` → `compute_recurring_amount(price, recurring_*)`

이 두 헬퍼가 구독 생성(04)·갱신(05)·화면 표시 전부에서 단일 진실 공급원이다.

### 2-4. 기간 종료일 — `compute_period_end(start, cycle, cycle_days)`

```python
YEAR  → start + 1년     (relativedelta, 월말/윤년 클램프)
MONTH → start + 1개월   (relativedelta, 예: 1/31 + 1달 = 2/28)
WEEK  → start + 7일
DAY   → start + cycle_days일   (cycle_days 없으면 에러)
```

`relativedelta`를 쓰는 이유: `1/31`에 한 달을 더하면 `2/31`은 없으므로 **말일로 보정**(2/28)된다.
단순 `timedelta(days=30)`이었다면 월마다 결제일이 밀렸을 것.

### 2-5. 표시용 계산내역 — `*_breakdown(plan)`
`first_amount_breakdown`/`recurring_amount_breakdown`은 "정가 10,000원 − 첫구독 할인 30% = 7,000원"
같은 **사람이 읽는 설명 문자열**을 만들어 요금제 표의 툴팁으로 보여준다(계산 로직과 분리된 표시 전용).

---

## 3. 요금제 생성 흐름

### 3-1. 두 개의 진입점, 하나의 로직

생성 경로가 둘이지만 **둘 다 `plan_service.create_plan`을 호출**한다. 차이는 "어느 서비스에"뿐:

- **`POST /admin/plans`** (`plans_create`, `require_manager`=SERVICE_MANAGER 전용)
  → 담당자 **본인의 주 서비스**(`ctx.user.service_id`)에 생성.
- **`POST /admin/services/{sid}/plans`** (`service_plan_create`, `require_any`)
  → 경로의 서비스에 생성. 단 `_can_manage(ctx, sid)`로 **권한 확인**(시스템관리자거나 그 서비스 담당자).

### 3-2. 폼 → 파라미터 변환: `_form_plan_fields` (`routes/plans.py:46`)

라우트는 폼 문자열을 `create_plan`이 받을 타입으로 변환한다:
```python
def _form_plan_fields(form):
    opt_int(key)        # 빈 문자열 → None, 아니면 int
    trial_enabled = form.get("trial_enabled") in ("on","true","1")  # 체크박스 → bool
    rec_type = form.get("recurring_discount_type", "NONE")
    return {
      "name", "price"(없으면 0), "first_payment_type", "first_payment_value",
      "recurring_discount_type", "recurring_discount_value"(타입 NONE이면 None),
      "trial_enabled", "trial_days"(체험 꺼지면 None),
    }
```
`billing_cycle`/`cycle_days`는 라우트에서 따로 파싱해 `create_plan`에 전달.

### 3-3. 핵심 로직 — `create_plan` (`services/plans.py:62`)

```python
async def create_plan(db, *, service_id, name, price, billing_cycle, cycle_days=None,
                      first_payment_type="NONE", first_payment_value=None,
                      recurring_discount_type="NONE", recurring_discount_value=None,
                      trial_enabled=False, trial_days=None,
                      auto_renew=True,        # 자동결제 여부(요청 013): False=첫 주기 후 만료
                      extra_info=None,        # 추가정보(요청 013): 서비스 측 설명 key/value
                      actor_user_id=None):
    if not name.strip(): raise InputValidationError("요금제 이름은 필수입니다")
    _validate_plan_fields(...)            # 가격/주기/cycle_days/첫결제 검증
    _validate_recurring_discount(...)     # 상시 할인 검증
    _validate_trial(...)                  # 체험 검증
    # (auto_renew와 trial은 공존 가능 — 별도 배타 검증 없음, 요청)
    plan = Plan(... trial_days=trial_days if trial_enabled else None,
                auto_renew=auto_renew, extra_info=extra_info if extra_info is not None else {})
    db.add(plan)
    await record_audit("plan.create", detail={name, price, trial_days, auto_renew})
    await db.commit()
    return plan
```

라우트는 `DomainError`를 잡아 **폼을 다시 렌더**(에러 표시)하거나, 성공 시 목록/서비스 상세로
리다이렉트한다. 즉 검증 규칙은 전부 서비스 계층에 있고 라우트는 얇다(01·02와 동일 패턴).

### 3-4. 검증 규칙 상세

**`_validate_plan_fields`** — 가격·주기·첫결제:
- `price ≤ 0` → 에러("1원 이상").
- `billing_cycle`이 enum에 없으면 에러.
- **DAY 주기**면 `cycle_days ≥ 1` 필수. **그 외 주기**면 `cycle_days`는 반드시 NULL.
- 첫결제 타입이 `NONE`/`FREE`면 `first_payment_value`는 NULL이어야 함.
  할인 타입이면 값 ≥ 1, `DISCOUNT_PERCENT`는 1~100.

**`_validate_recurring_discount`** — 상시 할인:
- `NONE`이면 값은 NULL. 할인 타입이면 값 ≥ 1, PERCENT는 1~100.
- **FREE 불가**(정기 결제는 무료가 될 수 없음).

**`_validate_trial`** — 체험:
- `trial_enabled`면 `trial_days ≥ 1` 필수. 꺼져 있으면 `trial_days`는 NULL이어야 함.

**자동결제 안함(`auto_renew=False`) + 체험 — 공존 가능(요청):**
- 별도 배타 검증 없음. 체험을 제공하면 체험 만료 시 첫 결제가 일어나고, 그 주기 종료 후
  자동 갱신 없이 만료된다. 체험이 없으면 첫 주기(생성 시 결제) 종료 후 만료.
- 구독 흐름: 체험이면 생성 시 `next_billing_at=체험 만료일` 유지(첫 결제 예약) →
  첫 결제 성공 후 `_advance_period`가 `next_billing_at=None`으로 만료 예약. 체험이 아니면
  생성 시 `next_billing_at=None`(첫 주기 후 만료). 둘 다 `_expire_non_renewing`이 EXPIRED 처리(문서 05).

---

## 4. 수정 · 보관 · 삭제

### 4-1. 수정 — `update_plan` + `_UNSET` 센티널 패턴 (`plans.py:113`)

부분 수정을 안전하게 하려고 **"미지정"과 "명시적 None"을 구분**하는 센티널을 쓴다:
```python
_UNSET = object()   # 호출자가 아예 안 넘긴 경우
# 파라미터 기본값이 _UNSET. 넘어온 값이 _UNSET이면 "기존 값 유지", None이면 "명시적으로 비움"
```
왜 필요한가: 그냥 `None`을 기본값으로 쓰면 "값을 None으로 바꿔달라"는 요청과 "이 필드는
건드리지 마라"를 구분할 수 없다. 그래서 별도 센티널 객체로 둘을 구분한다.

추가 편의 규칙:
- 첫결제 **타입만** 바꾸고 값은 안 넘기면: NONE/FREE면 값 자동 제거, 할인 타입이면 기존 값 유지.
- 상시 할인/체험도 같은 식으로 일관 처리.
- 변경 후 **세 검증 함수를 다시 호출**(불완전한 조합 방지).
- **결제 주기(billing_cycle/cycle_days)도 수정 가능**(요청 014). 변경 시 진행 중인 구독의
  현재 주기에는 영향이 없고, **다음 갱신부터** 새 주기로 기간이 계산된다(가격 변경과 동일 정책).
  DAY로 바꾸면 cycle_days 필수, 그 외 주기로 바꾸면 cycle_days는 None으로 정규화된다.
- **가격 변경은 다음 정기 결제부터 즉시 반영**되므로 감사로그에 `old_price`/`new_price`를 남긴다.

### 4-2. 보관 — `archive_plan`
`status = ARCHIVED`로만 바꾼다(삭제 아님). 보관된 요금제는 신규 구독 대상에서 빠지지만,
기존 구독·이력은 보존된다. 구독이 있어 삭제 못 할 때의 안전한 대안.

### 4-3. 삭제 — `delete_plan`
**구독이 1건이라도 있으면 삭제 불가**(`ConflictError` → "보관을 사용하세요"). 없을 때만 실제 삭제.
(서비스 삭제(문서 01)와 같은 "이력 보호" 원칙. 모델의 FK도 이중 방어.)

### 4-4. 권한·소속 검증 — `_get_plan`
수정/보관/삭제는 `_get_plan(db, plan_id, service_id)`로 **요금제가 그 서비스 소속인지** 확인한다.
라우트의 `_authorize_plan`이 먼저 `_can_manage`로 권한을 보고, 다른 서비스의 요금제면 404.
→ 담당자가 남의 서비스 요금제를 건드릴 수 없다.

---

## 4-5. 외부 API 요금제 조회 — `PlanResponse` (`app/schemas/api.py`)

`GET /api/v1/plans`가 반환하는 응답 스키마. `auto_renew`와 `extra_info`를 외부 서비스에
노출한다:

```python
class PlanResponse(BaseModel):
    id: uuid.UUID
    name: str
    price: int           # 정가(원)
    amount: int          # 상시 할인 적용 후 실제 정기 결제 금액(요청 003)
    billing_cycle: str
    cycle_days: int | None
    first_payment_type: str
    first_payment_value: int | None
    trial_enabled: bool
    trial_days: int | None
    auto_renew: bool     # 자동갱신 여부(요청 013): False=첫 주기 후 만료
    extra_info: dict     # 서비스 측 추가 정보 key/value(요청 013)
    ...

    @classmethod
    def from_model(cls, plan: Plan) -> "PlanResponse":
        return cls(..., auto_renew=plan.auto_renew, extra_info=plan.extra_info or {})
```

`amount` = `plan_recurring_amount(plan)`, `auto_renew`/`extra_info`는 모델에서 직접 매핑.
`extra_info`가 DB에서 None이면 `{}` 빈 dict로 반환한다.

### 어드민 폼의 `extra_info` 파싱 — `_parse_extra_info` (`admin/routes/plans.py`)

요금제 폼 textarea에서 `키: 값` 또는 `키=값` 형식으로 한 줄씩 입력받아 dict로 변환한다:

```python
def _parse_extra_info(text: str) -> dict:
    """textarea의 'key: value'(또는 'key=value') 줄들을 dict로. 빈 줄 무시, 형식 오류는 거부."""
    result = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line: continue
        sep = ":" if ":" in line else ("=" if "=" in line else None)
        if sep is None:
            raise InputValidationError(f"추가정보 형식 오류(키: 값): {line}")
        k, v = line.split(sep, 1)
        if not k.strip():
            raise InputValidationError(f"추가정보 키가 비었습니다: {line}")
        result[k.strip()] = v.strip()
    return result
```

빈 textarea → 빈 dict `{}`. 형식 오류(구분자 없음/빈 키)는 폼 에러로 표시된다.

---

## 5. 이 금액들이 실제로 쓰이는 곳 (앞으로의 연결)

요금제 자체는 "정의"일 뿐, 돈이 빠지는 건 구독/갱신이다. 미리 연결고리만:

- **구독 생성(문서 04)** — `create_subscription`:
  - 첫 구독이면 `plan_first_amount(plan)`(첫구독 할인가)로 첫 결제.
  - 재구독(혜택 소진 후)이면 `plan_recurring_amount(plan)`로 결제.
  - 체험(trial)이면 가입 시 **결제 0원**, 빌링키만 등록.
- **자동 갱신/체험 만료(문서 05)** — `renewals._renew_one`:
  - 매 정기 결제는 `plan_recurring_amount(plan)`(상시 할인가).
  - 기간 전진은 `compute_period_end(...)`.

즉 03의 두 헬퍼(`plan_first_amount`/`plan_recurring_amount`)와 `compute_period_end`가
결제 흐름 전체의 금액·날짜를 결정한다.

---

## 6. 예외 · 엣지 케이스

| 상황 | 처리 | 위치 |
|---|---|---|
| 가격 ≤ 0 | `InputValidationError` | `_validate_plan_fields` |
| DAY인데 cycle_days 없음 / 비-DAY인데 cycle_days 있음 | 검증 에러 | `_validate_plan_fields` |
| 할인율 > 100 | 검증 에러 | `_validate_plan_fields`/`_validate_recurring_discount` |
| 상시 할인을 FREE로 | 거부(정기 0원 불가) | `_validate_recurring_discount` |
| 체험 켜고 일수 없음 | 검증 에러 | `_validate_trial` |
| `auto_renew=False` + `trial_enabled=True` | 공존 허용(요청) — 체험 후 첫 결제, 그 주기 후 만료 | create_subscription / `_advance_period` |
| 할인 금액이 정가보다 큼 | `max(0, ...)`로 0원 클램프(음수 청구 방지) | `compute_*_amount` |
| 다른 서비스의 요금제 수정/삭제 | 404 | `_get_plan`/`_authorize_plan` |
| 구독 있는 요금제 삭제 | `ConflictError` → 보관 권장 | `delete_plan` |
| 월말 결제일(1/31 등) | `relativedelta`로 말일 보정 | `compute_period_end` |

---

## 7. 관련 테스트

- **`tests/unit/test_billing_math.py`** — 첫결제/정기/기간 계산의 경계·할인 조합(순수 함수라 단위 테스트가 핵심).
- `tests/integration/test_plans_service.py` — create/update(_UNSET 동작)/archive/delete, 검증 에러, 구독 있는 삭제 차단.
- `tests/e2e/test_admin_services_plans.py`, `test_service_plans.py` — 담당자 생성/수정/보관, 권한(타 서비스 차단),
  목록 필터(서비스/요금제/주기/상태 — 문서 09 연계), 금액·툴팁 표시.

---

## 8. 마이그레이션

- `alembic/versions/f6a7b8c9d0e1_plan_autorenew_extra.py`
  - `down_revision`: `e5f6a7b8c9d0`(GlobalSettings 마이그레이션 다음)
  - `upgrade()`: `plans` 테이블에 `auto_renew`(Boolean, default true), `extra_info`(JSONB, default `{}`) 추가.
  - `downgrade()`: 두 컬럼 삭제.

---

## 9. 유지보수 체크리스트

1. **새 할인 유형 추가**(예: 첫 N개월 할인):
   - `FirstPaymentType`(또는 새 enum) 값 추가 → `compute_first_amount`/`compute_recurring_amount`에
     계산 분기 추가 → `_validate_*`에 검증 추가 → **`test_billing_math.py`에 케이스 먼저**.
   - 표시가 필요하면 `*_breakdown`과 `_table.html`도.
2. **금액 규칙 변경은 반드시 `billing_math.py`에서**. 라우트/구독/갱신에 금액 계산을 흩뿌리지 말 것
   (단일 진실 공급원 유지 → 04·05가 자동으로 같은 규칙을 따름).
3. **새 주기 추가**: `BillingCycle` 값 + `compute_period_end` 분기 + `_validate_plan_fields`.
4. **부분 수정 필드 추가**: `update_plan`에서 `_UNSET` 센티널 패턴을 그대로 따라
   "미지정 vs 명시적 None"을 구분할 것.
5. **검증 규칙 추가**: 서비스 계층 `_validate_*`에만. 라우트는 `DomainError`를 폼 에러로
   자동 변환하므로 수정 불필요.
6. **`auto_renew=False` 요금제 추가 시**: 구독 생성(04), 갱신 배치의 `non_renewing_due` 경로(05),
   `PlanResponse` 노출(08)이 이미 지원한다. 별도 코드 수정 불필요.
7. **`extra_info` 스키마 제약 추가 시**: 현재 자유 형식 dict. 특정 키를 필수로 만들거나
   검증이 필요하면 `_parse_extra_info`(라우트)와 `create_plan`/`update_plan`(서비스)에 추가.
