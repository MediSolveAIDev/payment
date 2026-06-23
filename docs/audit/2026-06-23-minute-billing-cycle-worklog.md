# MINUTE 결제주기 구현 워크로그

- **날짜**: 2026-06-23
- **작업자**: seungjinhan
- **태스크 범위**: Task 1~8 (MINUTE 결제주기 전체 구현 + 매뉴얼 갱신)

---

## 목적

자동연장 로직을 개발·스테이징 환경에서 빠르게 검증하기 위한 **분(MINUTE) 단위 결제주기** 추가.
기존 년/월/주/일 주기는 실제 경과 시간이 너무 길어 로컬 테스트가 어렵다는 문제를 해결한다.

---

## 핵심 결정 사항

| 항목 | 결정 | 이유 |
|------|------|------|
| 비운영 환경 전용 | `environment` 필드가 `PRODUCTION`이면 MINUTE 주기 생성 거부 | 운영 DB에 분 단위 구독이 쌓이는 것 방지 |
| 최소 분수 5분 | `cycle_minutes < 5` 이면 `InputValidationError` | 스케줄러 기본 스윕이 5분이므로 그보다 짧은 주기는 무의미 |
| 새 컬럼 `cycle_minutes` | `plans` 테이블에 Nullable Integer 컬럼 추가 | `cycle_days`와 동일 패턴으로 분리 저장 |
| `environment` 컬럼 | `plans` 테이블에 String(20) 컬럼 추가 (기본값 `PRODUCTION`) | 운영/비운영 환경을 DB 레벨에서 구분 |

---

## 변경 파일 목록

### Task 1 — Enum + billing_math 기초
- `app/models/enums.py` — `BillingCycle`에 `MINUTE = "MINUTE"` 추가; `PlanEnvironment` 열거형 신규 추가 (`PRODUCTION` / `DEVELOPMENT` / `STAGING`)
- `app/services/billing_math.py` — `compute_period_end()`에 `MINUTE` 분기 추가 (`timedelta(minutes=cycle_minutes)`)

### Task 2 — Plan 모델 + 마이그레이션
- `app/models/plan.py` — `cycle_minutes: Mapped[Optional[int]]`, `environment: Mapped[str]` 컬럼 추가
- `alembic/versions/<rev>_add_minute_billing_cycle.py` — `cycle_minutes`, `environment` 컬럼 추가 마이그레이션

### Task 3 — 서비스 레이어 검증 + `create_plan`
- `app/services/plans.py` — `_validate_plan_fields()`에 MINUTE 검증 추가 (cycle_minutes 필수·최소 5분·비운영 환경 가드); `create_plan()` 시그니처에 `cycle_minutes`, `environment` 파라미터 추가

### Task 4 — 구독 생성·갱신 기간 계산 연결
- `app/services/subscriptions.py` — 구독 생성 시 `compute_period_end()` 호출부에 `cycle_minutes=plan.cycle_minutes` 전달
- `app/services/renewal.py` — 자동갱신 기간 계산 호출부에 `cycle_minutes=plan.cycle_minutes` 전달

### Task 5 — 외부 API 스키마
- `app/api/schemas/plans.py` — `PlanResponse`에 `cycle_minutes: Optional[int]`, `environment: str` 필드 추가 및 `from_model()` 매핑

### Task 6 — 어드민 라우트
- `app/admin/routes/plans.py` — `_form_plan_fields()`에 `billing_cycle == MINUTE`일 때 `cycle_minutes` 파싱 추가; `plans_create` / `service_plan_create` 호출부에 `cycle_minutes`, `environment` 전달

### Task 7 — 어드민 템플릿
- `app/admin/templates/plans/form.html` — MINUTE 옵션 추가, `cycle_minutes` 입력 필드 추가, JS 미리보기 미러 코드 업데이트

### Task 8 — 매뉴얼 갱신 (이번 태스크)
- `docs/user_manual/04-admin-plan.md` — 결제 주기 설명(제목·표·섹션 4.3)에 분(MINUTE) 추가
- `docs/manual/dev_manual/08-plans.md` — DB 컬럼 표, `compute_period_end` 주기 표, 예외 케이스 표에 MINUTE 추가
- 빌드 결과: `docs/user_manual/` (19개 문서), `docs/manual/dev_manual/` (30개 문서)

---

## 검증

| 단계 | 결과 |
|------|------|
| Task 1 — enum·billing_math 단위 테스트 | 통과 |
| Task 2 — 마이그레이션 적용 | 통과 |
| Task 3 — 서비스 레이어 검증 테스트 (최소 5분·비운영 가드) | 통과 |
| Task 4 — 구독 생성·갱신 기간 계산 연결 테스트 | 통과 |
| Task 5 — API 스키마 필드 확인 | 통과 |
| Task 6 — 어드민 라우트 E2E | 통과 |
| Task 7 — 어드민 템플릿 렌더 확인 | 통과 |
| Task 8 — HTML에 MINUTE/분 표기 grep 확인 | `docs/user_manual/04-admin-plan.html` ✓, `docs/manual/dev_manual/08-plans.html` ✓ |

---

## 스케줄러 주의

- 기본 스윕 주기(`scheduler_interval_minutes`)가 5분이므로, 분 단위 구독은 5분 간격으로 갱신된다.
- 더 빠른 관찰이 필요하면 **테스트 환경의 `scheduler_interval_minutes` 설정값을 낮춘다** (예: 1분).
- 운영 서버(`PRODUCTION`)에서는 MINUTE 요금제 생성이 서버 레벨에서 거부된다.

---

## 관련 설계·계획 문서

- 설계: `docs/superpowers/specs/2026-06-23-minute-billing-cycle-design.md`
- 구현 계획: `docs/superpowers/plans/2026-06-23-minute-billing-cycle.md`
