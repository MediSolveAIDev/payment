# 어드민 일반결제 전액/부분 취소(수수료 없음) 워크로그

- 날짜: 2026-06-20
- 작업자: seungjinhan
- 설계: `docs/superpowers/specs/2026-06-19-admin-oneoff-partial-cancel-design.md`

## 요청

결제 상세(어드민)에서 일반결제: 전액 취소는 수수료 상관없이 전액 환불, 부분취소는
취소 금액/% 입력 → 취소금액 계산 후 환불. 전액·부분 취소가 일반매출·환불금액에 반영.

## 확정 분기 (사용자 확인)

- 외부 서비스(사용자) 취소: 기존 그대로 — `cancellation_enabled` 게이트 유지 + 수수료율 적용.
- 어드민(관리자) 취소: 항상 허용(게이트 무시) + 수수료 없음 + 전액/부분(금액·%) + 누적.

## 상태 표현 단순화

새 enum `PARTIAL_CANCELED`는 도입하지 않음(외부 API·정산·내보내기·라벨·마이그레이션 광범위 변경 회피).
`canceled_amount`를 누적 환불액으로 사용하고, 부분취소는 `status=DONE` 유지, 전액 환불 시 `CANCELED`.
화면은 `canceled_amount>0 && DONE`이면 "부분취소"로 표시.

## 변경 내용

### 서비스 (`app/services/payments.py`)
- 신규 `admin_cancel_one_off_payment(db, toss, *, payment, cancel_amount=None, reason, actor_user_id)`:
  전액(None)/부분(X) 환불, `0<X≤잔여` 검증, 토스 부분취소, `canceled_amount` 누적, 잔여 0이면 CANCELED. 무수수료.
- `cancel_one_off_payment`(외부): 이미 부분취소된 결제(`canceled_amount>0`)는 외부 취소 차단(이중환불 방지) 가드 추가.

### 집계 (`dashboard.py`)
- `_revenue_expr`: DONE도 `amount − coalesce(canceled_amount,0)`(부분취소 반영).
- `_refund_between` + 서비스별 환불 서브쿼리: DONE/CANCELED 모두 환불액 합산.
- 정산(`settlement.py`)은 기존 `coalesce(canceled_amount,0)` 합산이라 부분환불 자동 반영(변경 없음).

### 라우트/템플릿
- `payments.py` cancel 라우트: `cancel_amount` 폼 파싱(빈값=전액) → `admin_cancel_one_off_payment`. `payment_detail`: 누적 환불액·잔여 전달.
- `payments/detail.html`: 전액/부분 취소 카드(금액·% 입력 + JS 미리보기), 환불 내역(누적·잔여·부분/전액 뱃지), 상태 셀 "부분취소" 병기.
- 목록/단건탭/정산 화면·route·엑셀: `status=='CANCELED'`로만 환불 표시하던 곳을 `canceled_amount>0`로 확장.
- `schemas/api.py`: 외부 API `cancelable`에 `not canceled_amount` 추가.

### 테스트
- `test_payment_cancel.py`(+8): 어드민 전액(무수수료)/부분/누적→CANCELED/잔여초과 거부/게이트 무시/토스 실패 보존/외부 취소 차단/정산 반영.
- `test_payment_cancel_admin.py`(신규 e2e): 부분→전액 취소 라우트 흐름.
- `test_admin_operations.py`: 기존 수수료/게이트 기반 취소 테스트를 신규 동작(항상 허용·무수수료 전액)으로 갱신.

### 문서
- `admin/06-payments.md`, `11-dashboard.md`, `10-settlement.md` 갱신 + HTML 재빌드.

## 검증

- `uv run pytest` → **590 passed**.
- 마이그레이션 불필요(스키마 변경 없음 — 기존 canceled_amount/cancel_fee 사용).
