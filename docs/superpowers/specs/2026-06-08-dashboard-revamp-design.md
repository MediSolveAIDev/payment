# 대시보드 개편 설계 (요청 008 §2~5)

날짜: 2026-06-08
상태: 승인됨
요청: docs/requests/008.md 2~5번 (1번 구동/자동결제 항목은 범위 외)

## 결정 사항

- **월 선택 기능 제외** — 현재(이번 달) 기준에 집중 (사용자 결정)
- 이번달 = 이번달 1일 00:00 UTC ~ 현재
- 취소 구분 집계는 **감사로그 기반** (마이그레이션 불필요):
  - 사용자취소 = 해당 월 `subscription.cancel` + `subscription.force_cancel` 감사 건수
  - 결제만료 = 해당 월 `subscription.suspended` 감사 건수 (재시도 소진으로 정지)
- 매출/미결제 상세 착지점은 **기존 결제 리스트 화면(`/admin/payments`)에 월(month) 필터만 추가**
  (구현 전 확인 결과 라우트·템플릿·사이드바 메뉴가 이미 존재 — 신규 화면 불필요)
- 12개월 전체 구독수는 월말 스냅샷 **근사**: `created_at ≤ 월말 AND current_period_end > 월말`
- 추가 정보 4종 채택: 만료 임박 구독, 체험 구독 현황, 이번달 결제 성공률, ARPU

## 1. 통계 카드 (2줄 × 4개, 클릭 시 상세 이동)

| # | 카드 | 계산 | 델타 | 클릭 이동 |
|---|---|---|---|---|
| 1 | 전체 구독 | 열린 구독 수 = status IN (TRIAL, ACTIVE, PAST_DUE, SUSPENDED) + (CANCELED AND current_period_end > now) | 활성 비율 % | `/admin/subscriptions` |
| 2 | 신규 구독 | 이번달 created_at 건수 | 전월 대비 % | `/admin/subscriptions?sort=created_at&dir=desc` |
| 3 | 이번달 매출 | 이번달 approved_at DONE 결제 합 | 전월 대비 % | `/admin/payments?status=DONE&month=YYYY-MM` |
| 4 | 이번달 미결제 | 이번달 requested_at FAILED 결제 건수 | — ("주의"/"안정") | `/admin/payments?status=FAILED&month=YYYY-MM` |
| 5 | 구독 취소 | 사용자취소 n · 결제만료 m (감사로그 기반, 합계가 메인 값) | — (구분 표기) | `/admin/subscriptions?status=CANCELED` |
| 6 | 결제 성공률 | 이번달 DONE ÷ (DONE+FAILED) % (분모 0이면 "—") | — | `/admin/payments?month=YYYY-MM` |
| 7 | ARPU | 이번달 매출 ÷ 현재 ACTIVE 수 (0이면 "—") | — | 이동 없음 (계산 지표) |
| 8 | 체험 구독 | 현재 TRIAL 수 | — | `/admin/subscriptions?status=TRIAL` |

- 카드 전체를 `<a>` 링크로 감싼다(기존 `.stat` 스타일 유지, ARPU만 비링크).
- 기존 카드 4개(활성/매출/신규/미수)는 위 8개로 대체.

## 2. 기존 결제 리스트(`/admin/payments`)에 월 필터 추가

- 라우트는 `app/admin/routes/subscriptions.py`의 `payments_list`에 이미 존재 (검색 q,
  status 필터, 정렬, 스코프 모두 구현됨). 사이드바 메뉴도 존재.
- 추가할 것: `month` 필터 (`filter_keys`에 추가) — `YYYY-MM` → [월초, 익월초) UTC 범위로
  `Payment.requested_at` 제한. 잘못된 형식은 무시(전체).
  (status=DONE과 함께 쓰면 approved_at 기준이 더 정확하나 단순화를 위해 requested_at 단일 기준)
- 템플릿(`payments/list.html`) 툴바에 month 입력(`<input type="month" name="month">`) 추가.

## 3. 12개월 차트 3종

- **매출 추이**: 기존 area 차트 유지 (`charts.area`)
- **신규 vs 취소**: 월별 신규(created_at 건수) / 취소(감사로그 cancel+force_cancel+suspended 건수) 2시리즈 — 기존 `charts.bars` 매크로 재사용(done/failed → new/canceled 의미만 변경, 라벨 파라미터화 필요 시 매크로 확장)
- **전체 구독수 추이**: 월말 스냅샷 근사 area 차트
- 기존 "최근 6개월 결제" 차트는 제거(신규/취소 차트로 대체, 결제 건전성은 성공률 카드가 표현)

## 4. 토탈 정보 (SYSTEM_ADMIN 전용)

- 서비스별 테이블: 서비스명 · 현재 열린 구독수 · 누적 구독수(전체 기간 생성) · 누적 매출(DONE 합)
- 행 클릭 → `/admin/services/{id}`
- 기존 "서비스별 매출 Top 5" hbar는 이 테이블로 대체(전체 서비스, 누적 매출 내림차순)
- 테이블 위에 누적 합계 한 줄: "누적 구독 N건 · 누적 매출 M원"

## 5. 우측 레일 3섹션

1. **최근 결제** (기존 유지, 8건)
2. **미수 구독**: PAST_DUE·SUSPENDED 구독 최대 5건 (사용자ID, 상태 배지, 다음결제일) → 구독 상세 링크. 0건이면 "미수 구독이 없습니다"
3. **만료 임박**: 열린 구독 중 `current_period_end`가 7일 이내 최대 5건 (사용자ID, 만료일) → 구독 상세 링크

## 6. 구독상태 도넛

- 유지. 범례(라벨) 클릭 시 `/admin/subscriptions?status=<상태>` 이동.

## 스코프 규칙

- 모든 집계는 기존 `_scoped` 패턴으로 service_scope 제한 (SERVICE_MANAGER = 담당 서비스만)
- 토탈 정보 테이블은 SYSTEM_ADMIN 전용 (기존 service_ranking과 동일 조건)

## 코드 구조

- `app/services/dashboard.py`: `DashboardData` 확장. 빌더가 커지므로 내부를 책임별 함수로 분리
  (`_month_cards`, `_twelve_month_series`, `_service_totals`, `_rails`) — `build_dashboard`가 조합
- `app/admin/routes/payments.py` + 템플릿 2개 신규
- `app/admin/templates/dashboard.html` 개편, `_charts.html` 매크로는 가능한 재사용

## 테스트

- 집계 통합 테스트(`tests/integration/test_dashboard.py` 신설 — 현재 대시보드 집계 테스트 없음):
  취소 구분(감사 기반), 열린 구독 수(CANCELED-기간내 포함), 스냅샷 근사, 성공률/ARPU 0-나누기,
  만료 임박/미수 레일 쿼리, 스코프 제한
- 결제 리스트 e2e: 렌더, 상태/월 필터, 검색, SERVICE_MANAGER 스코프
- 대시보드 e2e: 카드 8개 렌더+링크 href, 레일 3섹션, 토탈 테이블(admin만), 도넛 범례 링크

## 변경하지 않는 것

- 외부 API(/api/v1), 알림, 스케줄러, 모델/마이그레이션
- 기존 구독/서비스/감사 리스트 화면
- 요청 008 §1(자동결제/다음결제예정일/결제정보 hiding)은 이 설계 범위 외 — 별도 요청으로 처리
