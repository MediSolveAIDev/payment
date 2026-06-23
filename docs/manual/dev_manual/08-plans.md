# 08. 요금제 관리 (생성·수정·금액 계산·자동결제안함·추가정보)

> 상호참조: 구독 생성 → 04, 서비스 등록·담당자 → 09, DB 테이블 → 02, 인증·CSRF → 03

---

## 1. 한 줄 요약

관리자(SYSTEM_ADMIN 또는 SERVICE_MANAGER)가 어드민 화면에서 요금제를 생성·수정·보관·활성화·삭제하며, 금액 계산(첫 결제 / 정기 결제 / 상시 할인)은 서버의 `billing_math.py`가 전담한다.

---

## 2. 언제 실행되나 (트리거)

어드민 콘솔(`/admin/...`) 화면 조작으로 실행된다. 외부 서비스 API가 직접 호출하는 엔드포인트가 아니다.

- **SERVICE_MANAGER**: 자신이 담당하는 서비스의 요금제만 관리할 수 있다.
- **SYSTEM_ADMIN**: 전체 서비스의 요금제에 접근 가능하다.

---

## 3. 요청 진입점

모든 라우트는 `app/admin/routes/plans.py`에 정의되어 있다.

### 3.1 진입 화면 두 가지

요금제를 관리하는 화면이 두 곳 있다. 혼동하기 쉬우므로 차이를 명확히 파악한다.

| 화면 | URL 패턴 | 특징 |
|------|----------|------|
| **전역 요금제 목록** | `/admin/plans` | 전체(또는 담당) 서비스의 요금제를 한 번에 조회·관리. SERVICE_MANAGER는 본인 주 서비스에 요금제를 추가할 수 있다. |
| **서비스 상세 내 탭** | `/admin/services/{service_id}` | 특정 서비스의 요금제 테이블(`_plans_table.html`)이 서비스 상세 하단에 포함됨. SYSTEM_ADMIN과 담당 SERVICE_MANAGER 모두 접근 가능. |

### 3.2 라우트 목록

| HTTP 메서드·경로 | 핸들러 함수 | 권한 | 설명 |
|----------------|-----------|------|------|
| `GET /plans` | `plans_list` | `require_any` | 전역 목록 페이지 (htmx partial 공용) |
| `GET /plans/export.xlsx` | `plans_export` | `require_any` | 전역 목록 엑셀 다운로드 |
| `GET /plans/new` | `plans_new` | `require_manager` | SERVICE_MANAGER 전용 생성 폼 |
| `POST /plans` | `plans_create` | `require_manager` | SERVICE_MANAGER 주 서비스에 요금제 생성 |
| `GET /services/{service_id}/plans/new` | `service_plan_new` | `require_any` + `_can_manage` | 서비스 상세에서 생성 폼 |
| `POST /services/{service_id}/plans` | `service_plan_create` | `require_any` + `_can_manage` | 서비스 상세에서 요금제 생성 |
| `GET /plans/{plan_id}/edit` | `plans_edit` | `require_any` | 수정 폼 (`next` 파라미터로 저장 후 이동 URL 전달) |
| `POST /plans/{plan_id}` | `plans_update` | `require_any` | 수정 처리 |
| `POST /plans/{plan_id}/archive` | `plans_archive` | `require_any` | 보관(비활성화): ACTIVE → ARCHIVED |
| `POST /plans/{plan_id}/activate` | `plans_activate` | `require_any` | 활성화: ARCHIVED → ACTIVE |
| `POST /plans/{plan_id}/delete` | `plans_delete` | `require_any` | 하드 삭제 (구독 없을 때만 가능) |

> 파일: `app/admin/routes/plans.py:28-31` — `require_manager = require_role(UserRole.SERVICE_MANAGER)`, `require_any = require_role(SYSTEM_ADMIN, SERVICE_MANAGER)`

### 3.3 권한 스코프 확인 (`_can_manage`)

`app/admin/routes/plans.py:37-39`

```python
def _can_manage(ctx: AdminContext, service_id) -> bool:
    return ctx.service_ids is None or service_id in ctx.service_ids
```

- `ctx.service_ids is None` → SYSTEM_ADMIN(전체 접근)
- 목록이 있으면 → SERVICE_MANAGER(담당 서비스만)

`_authorize_plan` (`app/admin/routes/plans.py:50-59`)은 단일 요금제 조회 시 위 스코프를 검사하고, 스코프 외이거나 존재하지 않으면 **403 대신 404**를 반환한다(요금제 존재 여부를 외부에 노출하지 않기 위함).

### 3.4 CSRF 검증

모든 POST 핸들러에서 `await validate_csrf(request, ctx)`를 호출한다 (`app/admin/deps.py:105-110`). 폼의 `<input type="hidden" name="csrf_token">` 값 또는 `X-CSRF-Token` 헤더가 Redis 세션 토큰과 일치해야 한다.

---

## 4. 단계별 처리 흐름

### 4.1 요금제 생성 (예: 서비스 상세에서 생성)

```
브라우저 → POST /admin/services/{service_id}/plans
  │  app/admin/routes/plans.py:280 service_plan_create
  │
  ├─ _can_manage 스코프 검사 (plans.py:285-286)
  ├─ validate_csrf (plans.py:287)
  ├─ _form_plan_fields(form) (plans.py:86-129) ─── 폼 파싱
  │     └─ _collect_extra_info(form) (plans.py:62-83) ─── extra_info 수집
  │
  └─ plan_service.create_plan(db, ...) (app/services/plans.py:106-155)
        ├─ 이름 공백 검증 (plans.py:132-133)
        ├─ _validate_plan_fields (plans.py:34-66) ─── 가격·주기·할인 규칙
        ├─ _validate_recurring_discount (plans.py:82-103) ─── 상시 할인 규칙
        ├─ _validate_trial (plans.py:69-79) ─── 체험 규칙
        ├─ Plan 객체 생성 + db.add (plans.py:139-148)
        ├─ record_audit (audit.py) ─── plan.create 감사 로그
        └─ await db.commit()

성공 → saved_redirect(f"/admin/services/{service_id}", "저장되었습니다")
       (303 리다이렉트 + ?saved=저장되었습니다 파라미터)
실패(DomainError) → 폼 재렌더 (200, error 메시지 표시)
```

### 4.2 폼 파싱: `_form_plan_fields` (`app/admin/routes/plans.py:86-129`)

HTML 폼 값을 서비스 레이어 인자 dict로 변환한다. 주요 변환 규칙:

| 폼 필드 | 변환 규칙 |
|--------|----------|
| `auto_renew_disabled` 체크박스 | 체크 시(`"on"/"true"/"1"`) → `auto_renew=False`; 미체크 → `auto_renew=True` |
| `recurring_discount_type == "NONE"` | `recurring_discount_value`를 `None`으로 강제(값 필드가 숨겨져도 오염 방지) |
| `trial_enabled` 미체크 | `trial_days`를 `None`으로 강제 |
| `extra_key` / `extra_value` | `_collect_extra_info`로 dict 수집 |

> 주의: `_form_plan_fields` 전체가 `try … except DomainError` 블록 **안**에 있어야 한다. `_collect_extra_info`가 `InputValidationError`(DomainError 하위)를 던질 수 있기 때문이다. 이를 `try` 밖에 두면 500이 된다(`app/admin/routes/plans.py:244-255` 참고).

### 4.3 추가정보 수집: `_collect_extra_info` (`app/admin/routes/plans.py:62-83`)

폼이 `extra_key`와 `extra_value`를 병렬 목록으로 전송하면 행 단위로 zip해 dict로 수집한다.

- 키·값 모두 빈 행 → 무시(빈 행 추가 허용 UI)
- 값은 있는데 키가 비면 → `InputValidationError("추가정보 키를 입력하세요(값: ...)")`
- 키 중복 → 마지막 값이 우선

### 4.4 요금제 수정 (`plans_update`, `app/admin/routes/plans.py:322-356`)

생성과 거의 같되, `_UNSET` 센티널(`app/services/plans.py:170`)로 "미전달"과 "명시적 None"을 구분한다.

```python
_UNSET = object()  # 인자가 전달되지 않았음을 표현하는 센티널
```

결제 주기(`billing_cycle`/`cycle_days`)는 **수정 불가**(요청): `update_plan()`은 해당 인자를 받지 않고 항상 기존 값을 유지한다. 수정 폼은 결제 주기를 읽기 전용으로만 표시하며 전송하지 않는다. 주기를 바꾸려면 새 요금제를 생성한다.

```python
if new_billing_cycle != BillingCycle.DAY:
    new_cycle_days = None  # DAY가 아니면 cycle_days 무의미 — 정규화
```

### 4.5 보관(archive)과 활성화(activate)

| 작업 | 함수 | 상태 전이 | 구독 필요 조건 |
|------|------|----------|--------------|
| 보관 | `archive_plan` (`plans.py:293-305`) | `ACTIVE` → `ARCHIVED` | 구독이 있어도 가능 |
| 활성화 | `activate_plan` (`plans.py:308-320`) | `ARCHIVED` → `ACTIVE` | — |

보관된 요금제는 신규 구독을 받지 않지만 기존 구독은 유지된다. 구독이 있는 요금제를 삭제하고 싶을 때 먼저 이 방법을 사용한다.

### 4.6 삭제 (`delete_plan`, `app/services/plans.py:323-339`)

```python
count = await db.scalar(select(func.count()).select_from(Subscription)
                        .where(Subscription.plan_id == plan_id))
if count:
    raise ConflictError("구독이 있는 요금제는 삭제할 수 없습니다. 보관(아카이브)을 사용하세요.")
```

- ACTIVE·EXPIRED 등 상태에 관계없이 구독 레코드가 1건이라도 있으면 삭제 거부
- 라우트에서 DomainError를 잡아 `?error=메시지`를 붙여 리다이렉트 (`plans.py:404-412`)

---

## 5. 사용하는 DB 테이블·컬럼

### 5.1 `plans` (주 쓰기 대상)

> 파일: `app/models/plan.py:16-48`, DB 컬럼 상세: `docs/dev_manual/02-database.md` 3.5절

| 컬럼 | 타입 | 역할 |
|------|------|------|
| `id` | UUID PK | 요금제 식별자 |
| `service_id` | UUID FK(RESTRICT) | 소속 서비스. 구독 있으면 삭제 불가 |
| `name` | String(100) | 표시명 |
| `price` | BigInteger | 정가(KRW 정수, 원 단위) |
| `billing_cycle` | String(10) | `YEAR`/`MONTH`/`WEEK`/`DAY`/`MINUTE` |
| `cycle_days` | Integer(nullable) | DAY 주기일 때 실제 일수; 나머지는 NULL |
| `cycle_minutes` | Integer(nullable) | MINUTE 주기일 때 실제 분수(최소 5); 나머지는 NULL |
| `first_payment_type` | String(20) | 첫구독 할인 유형 (`FirstPaymentType`) |
| `first_payment_value` | BigInteger(nullable) | 첫구독 할인 값 (원 또는 %) |
| `recurring_discount_type` | String(20) | 상시 할인 유형 (`DiscountType`) |
| `recurring_discount_value` | BigInteger(nullable) | 상시 할인 값 (원 또는 %) |
| `trial_enabled` | Boolean | 체험 기능 활성 여부 |
| `trial_days` | Integer(nullable) | 체험 기간(일수) |
| `auto_renew` | Boolean | False이면 첫 주기 후 자동연장 없음 |
| `extra_info` | JSONB | 서비스 측 설명용 key/value |
| `status` | String(20) | `ACTIVE` / `ARCHIVED` |
| `created_at` / `updated_at` | DateTime(tz) | TimestampMixin |

### 5.2 `subscriptions` (삭제 제약 확인용, 읽기만)

`delete_plan`에서 `WHERE plan_id = ?` COUNT 조회로 구독 존재 여부를 확인한다. 0건이어야 삭제 허용.

또한 `plans.service_id`에는 `ondelete="RESTRICT"` FK가 걸려 있어 서비스 삭제 시에도 요금제가 있으면 거부된다(`app/models/plan.py:28`).

### 5.3 `audit_logs` (쓰기)

생성·수정·보관·활성화·삭제마다 `record_audit`으로 기록한다.

| 작업 | `action` 값 | `detail` 주요 필드 |
|------|------------|-----------------|
| 생성 | `plan.create` | `name`, `price`, `trial_days`, `auto_renew` |
| 수정 | `plan.update` | **모든 수정 항목을 변경 전/후로 상세 기록(요청)**: `old/new_name`, `old/new_price`, `old/new_first_payment`(첫결제 할인), `old/new_recurring_discount`(상시 할인), `old/new_trial_enabled·trial_days`(체험), `old/new_auto_renew`(자동갱신), `old/new_extra_info`(추가정보). **할인은 유형+값을 결합해 "정률 N%" / "정액 N,NNN원" / "무료(0원)" / "없음"으로 기록** — 비율인지 값인지 한눈에 구분(요청). 결제 주기는 수정 불가라 제외. detail_summary가 실제 바뀐 항목만 "라벨 전 → 후"로 표시 |
| 보관 | `plan.archive` | (target_id만) |
| 활성화 | `plan.activate` | (target_id만) |
| 삭제 | `plan.delete` | (target_id만) |
| 사용일추가 | `plan.bonus_days` | `plan_name`, `days`(추가 일수), `affected_count`(적용 구독 수) |

---

## 6. 상태 전이

`PlanStatus` 열거형: `app/models/enums.py:62-64`

```
생성(create_plan) → ACTIVE
                        │
                        ▼ archive_plan
                    ARCHIVED ──────────────────────┐
                        │                          │
                        ▼ activate_plan            │
                    ACTIVE (복귀)                   │
                                                   │
                  구독 0건 확인 후 delete_plan → (삭제)
```

- `ACTIVE`: 신규 구독 가능 상태
- `ARCHIVED`: 신규 구독 불가(기존 구독 유지); `list_plans(only_active=True)`에서 제외
- 삭제는 구독 레코드가 0건일 때만 가능(하드 삭제)

---

## 7. 금액 계산 규칙

> 파일: `app/services/billing_math.py`

금액 계산은 **반드시 서버가 수행**한다. 외부에서 넘어온 금액 값은 사용하지 않는다.

### 7.1 두 할인은 독립 적용 (중첩 없음)

| 구분 | 함수 | 적용 할인 | 기준 |
|------|------|----------|------|
| 첫 결제(1회차) | `plan_first_amount` (`billing_math.py:106-109`) | `first_payment_type` / `first_payment_value` | 정가(`plan.price`) |
| 정기 결제(2회차~) | `plan_recurring_amount` (`billing_math.py:100-103`) | `recurring_discount_type` / `recurring_discount_value` | 정가(`plan.price`) |

**첫 결제에 상시 할인은 적용되지 않는다.** 정가를 기준으로 첫구독 할인만 적용한다(요청 005).

```
예) price=10,000원, 첫구독 할인 30%, 상시 할인 20%
  첫 결제: 10,000 − (10,000 × 30%) = 7,000원  ← 상시 할인 무관
  정기 결제: 10,000 − (10,000 × 20%) = 8,000원 ← 첫구독 할인 무관
```

### 7.2 `FirstPaymentType` 열거값 (`app/models/enums.py:40-52`)

| 값 | 첫 결제액 |
|----|---------|
| `NONE` | 정가 그대로 |
| `FREE` | 0원(완전 무료) |
| `DISCOUNT_AMOUNT` | 정가 − first_payment_value(원); 음수는 0으로 클램프 |
| `DISCOUNT_PERCENT` | 정가 − floor(정가 × value / 100); 범위 1~100 |

### 7.3 `DiscountType` 열거값 (`app/models/enums.py:55-59`)

| 값 | 정기 결제액 |
|----|----------|
| `NONE` | 정가 그대로 |
| `DISCOUNT_AMOUNT` | 정가 − recurring_discount_value(원); 음수는 0으로 클램프 |
| `DISCOUNT_PERCENT` | 정가 − floor(정가 × value / 100); 범위 1~100 |

> `FREE`가 없는 이유: 매 회차 0원 정기 결제는 의미 없으므로 설계상 제외. 상시 할인에 FREE를 넣으면 `_validate_recurring_discount`에서 거부된다(`app/services/plans.py:82-103`).

### 7.4 결제 주기별 기간 계산 (`compute_period_end`, `billing_math.py:39-51`)

| `billing_cycle` | 계산 방식 | 비고 |
|----------------|----------|------|
| `YEAR` | `start + relativedelta(years=1)` | 윤년 말일 클램프(2024-02-29 + 1년 = 2025-02-28) |
| `MONTH` | `start + relativedelta(months=1)` | 월말 클램프(1/31 + 1개월 = 2/28 또는 2/29) |
| `WEEK` | `start + timedelta(weeks=1)` | 7일 고정 |
| `DAY` | `start + timedelta(days=cycle_days)` | `cycle_days` 필수 |
| `MINUTE` | `start + timedelta(minutes=cycle_minutes)` | **자동연장 테스트용, 비운영 전용**. 서버 실행 환경 `settings.environment`가 `prod`이면 요금제 생성이 거부된다(검증 계층). update(수정)는 기존 MINUTE 요금제를 막지 않는다. 최소 5분(`cycle_minutes` 필수). 스케줄러 기본 스윕이 5분이므로 그 주기로 갱신됨(값: dev/test/prod). 더 빠른 관찰이 필요하면 테스트 환경에서 `scheduler_interval_minutes`를 낮출 것 |

### 7.5 화면 미리보기 (form.html)

폼 화면에서 JS가 금액을 실시간 미리 보여주지만 **표시 전용**이다. 실제 결제는 서버가 항상 재계산한다. `billing_math.py`를 변경하면 `form.html:138-182`의 JS 미러 코드도 함께 수정해야 한다.

---

## 8. 자동결제안함 (auto_renew=False)

> `app/models/plan.py:45`, 요청 013

체크박스: 폼 필드명 `auto_renew_disabled` (체크 = 자동결제 안함 = `auto_renew=False`).

- DB 기본값: `auto_renew=True` (자동 갱신)
- `auto_renew=False`이면 구독 생성 시 `next_billing_at=None`으로 저장되고, 기간 종료 시 자동으로 `EXPIRED` 처리된다
- **체험과 공존 가능**: `auto_renew=False` + `trial_enabled=True` 모두 설정하면, 체험 기간 종료 → 첫 결제 발생 → 그 주기 종료 시 만료(갱신 없음)
- **비소급**: `auto_renew` 변경은 이미 생성된 구독의 `next_billing_at`에 소급 적용되지 않는다. 신규 구독부터 반영된다(`app/services/plans.py:228-231`)

---

## 9. 체험(Trial) 설정

> `app/services/plans.py:69-79`

| 조건 | 결과 |
|------|------|
| `trial_enabled=True`, `trial_days` 미입력 | `InputValidationError("체험을 사용하려면 체험 일수(1 이상)가 필요합니다")` |
| `trial_enabled=False`, `trial_days` 전달 | `InputValidationError("체험 일수는 체험 활성화 시에만 사용합니다")` |
| `trial_enabled=False`로 저장 | `trial_days`는 `None`으로 강제 저장(값이 남으면 혼란) (`plans.py:145`) |

---

## 10. 추가정보 (extra_info JSONB)

> `app/models/plan.py:47`, 요청 013

- DB 컬럼: `JSONB`, 기본값 `{}`
- 서비스 측이 요금제 설명을 자유롭게 key/value로 추가한다(예: `{"용량": "10GB", "사용자수": "5명"}`)
- 외부 API 응답(`PlanResponse`)에 그대로 노출되므로 고객사 앱이 UI에 활용할 수 있다
- 폼 입력: `extra_key[]` / `extra_value[]` 병렬 목록 → `_collect_extra_info`로 dict 변환
- 수정 시: `update_plan`에 `extra_info=dict`를 넘기면 **교체**(merge가 아님), `None`을 넘기면 `{}`로 초기화(`plans.py:254-257`)

---

## 11. 예외·엣지 케이스

| 상황 | 발생 위치 | 예외 종류 | 사용자에게 표시되는 내용 |
|------|----------|----------|----------------------|
| 이름 공백 | `plans.py:132` | `InputValidationError` | "요금제 이름은 필수입니다" |
| 가격 0 이하 | `plans.py:48` | `InputValidationError` | "가격은 1원 이상이어야 합니다" |
| DAY 주기에 cycle_days 없음 | `plans.py:53` | `InputValidationError` | "DAY 주기는 cycle_days(1 이상)가 필요합니다" |
| MONTH/WEEK/YEAR에 cycle_days 전달 | `plans.py:56` | `InputValidationError` | "cycle_days는 DAY 주기에서만 사용합니다" |
| MINUTE 주기에 cycle_minutes 없음 또는 5 미만 | `plans.py` | `InputValidationError` | "MINUTE 주기는 cycle_minutes(5 이상)가 필요합니다" |
| MINUTE 주기를 `prod` 환경(`settings.environment == "prod"`)에서 생성 시도 | `plans.py` | `InputValidationError` | "MINUTE 주기는 비운영 환경에서만 사용합니다" |
| 첫구독 할인율 > 100 | `plans.py:65` | `InputValidationError` | "할인율은 1~100 사이여야 합니다" |
| 상시 할인에 FREE 시도 | `plans.py:93-99` | `InputValidationError` | "지원하지 않는 상시 할인 유형입니다" |
| 체험 활성화+일수 없음 | `plans.py:76` | `InputValidationError` | "체험을 사용하려면 체험 일수(1 이상)가 필요합니다" |
| 값만 있고 추가정보 키 없음 | `plans.py:81` | `InputValidationError` | "추가정보 키를 입력하세요(값: ...)" |
| 구독이 있는 요금제 삭제 시도 | `plans.py:335` | `ConflictError` | "구독이 있는 요금제는 삭제할 수 없습니다. 보관(아카이브)을 사용하세요." |
| 타 서비스 요금제 접근 | `plans.py:57-58` | `NotFoundError(404)` | 404 페이지(403 대신 404로 존재 여부 숨김) |
| CSRF 토큰 불일치 | `deps.py:108-110` | `PermissionDeniedError` | 403 |

### cycle_days 정규화 엣지 케이스

수정 시 `billing_cycle`을 `MONTH`로 바꾸면서 `cycle_days=10`을 함께 보내면, 서버가 `cycle_days`를 `None`으로 자동 정규화한다(`plans.py:262-263`). 폼이 숨겨진 필드를 그대로 전송해도 안전하다.

```python
# update_plan 내부 (app/services/plans.py:262-263)
if new_billing_cycle != BillingCycle.DAY:
    new_cycle_days = None
```

---

## 12. 관련 테스트

| 테스트 파일 | 테스트 항목 |
|-----------|-----------|
| `tests/unit/test_billing_math.py` | 금액 계산 단위 테스트: `compute_first_amount`, `compute_recurring_amount`, `compute_period_end`, `plan_first_amount`가 상시 할인을 무시하는지, 툴팁 문자열 |
| `tests/integration/test_plans_service.py` | 서비스 레이어 통합 테스트: 생성·수정·보관·활성화·삭제, 주기 변경, 구독 존재 시 삭제 거부, auto_renew+체험 공존, extra_info 저장, `_collect_extra_info` 단위 |
| `tests/e2e/test_admin_services_plans.py` | 라우트 E2E: 관리자·담당자 권한, 서비스 상세에서 생성·수정, 타 서비스 요금제 접근 404, 추가정보 폼 오류 처리 |
| `tests/e2e/test_service_plans.py` | 서비스 상세 탭 E2E: 관리자가 서비스 상세에서 생성·수정, 다중 담당 서비스에서 수정, 삭제 충돌 오류 |

테스트 실행 예:
```bash
# 특정 파일만
pytest tests/integration/test_plans_service.py -v
# billing_math 단위만
pytest tests/unit/test_billing_math.py -v
```

---

## 13. 유지보수 팁

### "금액 계산 규칙을 바꾸고 싶다"
→ `app/services/billing_math.py`만 수정한다. 변경 시 **폼 JS 미러도 함께** 수정해야 한다: `app/admin/templates/plans/form.html:138-182`의 `applyDiscount` 함수가 Python 코드를 JS로 구현하고 있다.

### "새 결제 주기를 추가하고 싶다"
1. `app/models/enums.py:31-37` — `BillingCycle`에 새 값 추가
2. `app/services/billing_math.py:39-51` — `compute_period_end`에 새 주기 분기 추가
3. `app/admin/templates/plans/form.html:20-26` — `<select>` 옵션 추가
4. 마이그레이션은 불필요(String 컬럼에 저장)

### "폼에 새 필드를 추가하고 싶다"
1. `app/admin/templates/plans/form.html` — 폼 HTML 추가
2. `app/admin/routes/plans.py:86-129` `_form_plan_fields` — 폼 파싱에 새 필드 추가
3. `app/services/plans.py` `create_plan` / `update_plan` — 서비스 함수에 파라미터 추가
4. `app/models/plan.py` — 모델 컬럼 추가 후 Alembic 마이그레이션 생성

### "요금제를 삭제하려는데 구독 때문에 안 된다"
→ 먼저 `POST /admin/plans/{plan_id}/archive`로 보관 처리한다. 보관 상태에서는 신규 구독이 막히고 기존 구독은 유지된다. 나중에 모든 구독이 만료된 후 삭제할 수 있다.

### "수정 후 이동 URL이 이상하다 (`open redirect` 방어)"
`_safe_next` (`plans.py:42-47`)가 `next` URL을 검사해 `/admin/`로 시작하지 않으면 fallback(`/admin/plans`)으로 대체한다. 외부 URL로의 리다이렉트는 허용하지 않는다.

### "감사 로그에서 가격 변경 이력을 찾고 싶다"
`audit_logs` 테이블에서 `action = 'plan.update'`인 레코드를 조회한다. `detail` JSONB에 `old_price`/`new_price`를 포함해 **모든 수정 항목의 변경 전/후**(이름·첫결제할인·상시할인·체험·자동갱신·추가정보)가 `old_*`/`new_*`로 함께 기록된다.

### "htmx partial vs 전체 페이지 구분"
`plans_list`에서 `render_list`가 `HX-Request` 헤더를 감지해 htmx 요청이면 `plans/_table.html`만, 일반 요청이면 `plans/list.html` 전체를 렌더한다(`plans.py:193-223`). 서비스 상세 탭의 요금제 테이블은 `services/_plans_table.html`이 별도로 존재하며 htmx `hx-target="#list-svc-plans"`로 부분 갱신된다.
