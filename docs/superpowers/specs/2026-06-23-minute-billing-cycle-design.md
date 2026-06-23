# MINUTE 결제주기 추가 (자동연장 테스트용) — 설계

- 날짜: 2026-06-23
- 작성자: seungjinhan (oasis@medisolveai.com)
- 상태: 승인됨 → 구현 계획 대기

## 목적

요금제 결제주기에 **분(MINUTE) 단위**를 추가한다. 주 용도는 **자동연장 흐름을 몇 분 만에 검증**하는 테스트다(연/월/일은 갱신을 눈으로 보기까지 너무 오래 걸림). 실운영 과금용이 아니므로 **비운영 환경(APP_ENV ≠ prod)에서만** 노출한다.

## 결정사항 (확정)

1. **용도**: 자동연장 테스트용.
2. **노출 범위**: `APP_ENV`(=`settings.environment`)가 `prod`가 아닐 때만 선택 가능. 운영에선 거부(실수 과금 방지).
3. **최소값**: `cycle_minutes ≥ 5`. 스케줄러 기본 스윕 주기(5분)와 맞춤 — 5분보다 짧으면 스윕에서 의미가 없음.
4. **데이터 모델**: `cycle_days` 재사용 대신 **새 컬럼 `cycle_minutes` 추가**(평행 필드). `cycle_days`는 API·어드민에서 "일수"로 노출되므로 분을 섞으면 의미가 깨짐.

## 비범위 (YAGNI)

- "단위 + 개수(`cycle_count`)" 단일 필드 통합 리팩터링 — 기존 `cycle_days` 데이터 마이그레이션 필요. 이번 테스트용 범위를 넘으므로 하지 않음. `cycle_days`(일)·`cycle_minutes`(분)는 별개 nullable 컬럼으로 공존.
- 스케줄러 기본 주기 변경 안 함. 빠른 관찰이 필요하면 테스트 환경에서 `scheduler_interval_minutes`를 낮춘다(운영 기본값 유지).

## 변경 대상 (touchpoints)

### 1. enum — `app/models/enums.py`
`BillingCycle`에 `MINUTE = "MINUTE"` 추가. docstring에 "MINUTE 선택 시 Plan.cycle_minutes(5 이상) 지정, 비운영 전용" 명시.

### 2. 데이터 모델 + 마이그레이션 — `app/models/plan.py`, `alembic/versions/`
- `cycle_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)` 추가(주석: MINUTE 주기일 때 실제 분; 그 외 NULL).
- alembic 마이그레이션 1개: `plans.cycle_minutes` nullable Integer 컬럼 add(다운그레이드는 drop).

### 3. 기간 계산 — `app/services/billing_math.py`
- `compute_period_end(start, cycle, cycle_days=None, cycle_minutes=None)`로 시그니처 확장.
- 분기 추가: `if cycle == BillingCycle.MINUTE: cycle_minutes 없거나 <5면 InputValidationError; return start + timedelta(minutes=cycle_minutes)`.
- 호출부 2곳에 `cycle_minutes=plan.cycle_minutes` 전달:
  - `app/services/subscriptions.py:232`, `:440`
  - `app/services/renewals.py:124`

### 4. 검증 — `app/services/plans.py` `_validate_plan_fields`
- 시그니처에 `cycle_minutes: int | None` 추가.
- 규칙:
  - `MINUTE`이면 `cycle_minutes ≥ 5` 필수, **그리고** `cycle_days`는 전달 금지.
  - `DAY`이면 기존대로 `cycle_days ≥ 1`, `cycle_minutes` 전달 금지.
  - 그 외 주기는 `cycle_days`·`cycle_minutes` 둘 다 전달 금지.
  - **비운영 가드**: `settings.environment == "prod"`이고 `billing_cycle == MINUTE`이면 거부("MINUTE 주기는 비운영 환경에서만 사용합니다"). 검증 계층에서 막아 API·어드민 양쪽 차단.
- 생성/수정 서비스가 `cycle_minutes`를 받아 저장하도록 연결.

### 5. API 스키마 — `app/schemas/api.py`
- `billing_cycle` 설명에 `| MINUTE` 추가.
- `cycle_minutes: int | None` 필드 추가(설명: "MINUTE 주기일 때의 실제 분. 그 외 주기에서는 null. 비운영 전용").
- PlanResponse 매핑에 `cycle_minutes=plan.cycle_minutes` 연결.

### 6. 어드민 UI (htmx)
- `app/admin/routes/plans.py`:
  - 폼 파싱: `cycle_minutes` 읽기(`int` 또는 None), 생성/수정 호출에 전달(`:242`, `:250` 인근).
  - 목록 라벨(`:180`): `MINUTE`이면 `MINUTE {cycle_minutes}분` 표시.
  - 템플릿에 `is_prod`(=`settings.environment == "prod"`) 컨텍스트 전달.
- `app/admin/templates/plans/form.html`:
  - `is_prod`가 아닐 때만 `<option value="MINUTE">분</option>` 렌더.
  - 분 개수 입력칸(`id` 토글: `billing_cycle === 'MINUTE'`일 때 표시, `min="5"`). DAY 일수칸과 동일한 onchange 토글 방식.
- `app/admin/templates/plans/_table.html`:
  - 필터 드롭다운에 `('MINUTE','분')` 추가(비운영에서만 노출).
  - 표시 셀: `cycle_minutes` 있으면 `(N분)`.

### 7. 스케줄러 — 변경 없음 (`app/scheduler/runner.py`, `app/core/config.py`)
주의만 문서화: 기본 `scheduler_interval_minutes=5`라 MINUTE(≥5) 구독은 다음 스윕 tick(최대 5분 뒤)에 갱신됨. 즉시 관찰하려면 테스트 환경에서 해당 값을 낮춘다.

## 테스트

- `compute_period_end(MINUTE, cycle_minutes=5)` → +5분 검증; `cycle_minutes`=None/4면 오류.
- `_validate_plan_fields`:
  - MINUTE + cycle_minutes<5 거부 / MINUTE + cycle_days 전달 거부 / 비-MINUTE + cycle_minutes 전달 거부.
  - 비운영에선 MINUTE 허용, `environment=prod`면 거부.
- 회귀: 기존 YEAR/MONTH/WEEK/DAY 경로 영향 없음.

## 문서

- `docs/manual/dev_manual` 및 user_manual의 결제주기 설명(년/월/주/일 → +분, 비운영 전용·최소 5분) 갱신 후 manual 재빌드.
- `docs/audit/`에 작업 워크로그.

## 동작 흐름 (변경 후)

요금제 생성(비운영) → `billing_cycle=MINUTE, cycle_minutes=5` 저장 → 구독 생성 시 `current_period_end = now + 5분`, `next_billing_at = current_period_end` → 스윕(≤5분 주기)이 `next_billing_at <= now` 구독을 잡아 자동 재결제 → `compute_period_end(MINUTE)`로 다음 5분 갱신. 운영에서는 MINUTE 선택 자체가 거부됨.
