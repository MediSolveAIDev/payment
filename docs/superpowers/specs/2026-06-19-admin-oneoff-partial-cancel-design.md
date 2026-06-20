# 어드민 일반결제 전액/부분 취소 — 설계

- 날짜: 2026-06-19
- 상태: 승인됨(행위자 분기·게이트 확정)

## 목표

결제 상세(어드민)에서 일반결제(ONE_OFF)에 대해:
1. **전액 취소**: 수수료 상관없이 잔여 금액 전부 환불.
2. **부분 취소**: 취소할 금액 입력 또는 % 입력(→ 취소금액 계산·미리보기) 후 환불. **여러 번 누적** 가능.
3. 전액/부분 취소가 대시보드 **일반매출금액**·**환불금액**과 정산에 정확히 반영.

## 확정된 분기 (사용자 확인)

- **외부 서비스(사용자) 취소**: 기존 그대로 — `cancellation_enabled` 게이트 유지 + 수수료율 적용 전액 취소. (변경 없음)
- **어드민(관리자) 취소**: 항상 허용(게이트 무시) + 수수료 없음 + 전액/부분(금액·%) + 누적.

## 상태 표현 (단순화 결정)

새 enum `PARTIAL_CANCELED`를 추가하지 않는다. 이유: 외부 API 응답·정산·내보내기·라벨·필터·마이그레이션 등 광범위한 변경/위험을 피하고, 요구 동작은 `canceled_amount`만으로 충족된다.

- `canceled_amount` = **누적 환불액**(부분취소 합산). 잔여 환불가능액 = `amount − canceled_amount`.
- 부분취소(잔여>0): `status` **DONE 유지**, `canceled_amount` 누적.
- 전액 환불(잔여=0 도달): `status = CANCELED`.
- 화면에서는 `canceled_amount>0 && status==DONE`이면 "부분취소" 로 표시(파생).

## 서비스 레이어 (`app/services/payments.py`)

- 기존 `cancel_one_off_payment(...)`: 외부/사용자 경로 — 변경 없음(수수료·게이트·단발 CANCELED).
- 신규 `admin_cancel_one_off_payment(db, toss, *, payment, cancel_amount=None, reason, actor_user_id)`:
  - 검증: `kind==ONE_OFF`, `status in (DONE,)`(취소 종료 아님), 잔여 = `amount − coalesce(canceled_amount,0)` > 0.
  - `cancel_amount=None` → 전액(잔여 전부). `cancel_amount=X` → `0 < X ≤ 잔여` 검증 후 부분.
  - 토스 `cancel_payment(payment.toss_payment_key, reason, cancel_amount=환불액)` 호출. (전액이라도 부분취소 이력이 있으면 잔여만 환불하므로 항상 cancel_amount 전달; 단 한 번도 취소 안 했고 전액이면 None으로 전송해도 무방 — 잔여==amount일 때만 None)
  - 성공: `canceled_amount = (기존 + 환불액)`, `cancel_fee` 변경 없음(0/None 유지 — 어드민 무수수료), `canceled_at=utcnow()`, 잔여 0이면 `status=CANCELED`.
  - 감사로그 `payment.canceled`(actor USER) — detail에 refund/누적/잔여 기록.
  - 토스 실패 시 상태·금액 보존 + `payment.cancel_failed` 감사 후 예외.

## 집계 (`dashboard.py`, `settlement.py`)

- 순매출식: `DONE → amount − coalesce(canceled_amount,0)`, `CANCELED → amount − coalesce(canceled_amount, amount)`, else 0. (DONE에 부분환불 반영)
- 환불액: `DONE → coalesce(canceled_amount,0)`, `CANCELED → coalesce(canceled_amount, amount)` 합계(status in DONE,CANCELED). (부분환불도 환불에 포함)
- `_REVENUE_STATUSES`는 그대로 (DONE, CANCELED). 정산 `_SETTLED_STATUSES`도 그대로 — `refund_sum`은 이미 `coalesce(canceled_amount,0)` 이므로 DONE 부분환불 자동 반영.

## UI (`payments/detail.html`)

- 취소 카드: `status==DONE && 잔여>0`이면 노출(부분취소 후에도 DONE이므로 재노출).
  - **전액 취소** 버튼: 잔여 전부 환불(확인 모달).
  - **부분 취소**: 금액 입력 + % 입력(둘 중) → JS가 `floor(잔여*%/100)`로 취소금액 미리보기 → 제출. 서버가 잔여 한도 재검증.
  - 누적 환불액·잔여 환불가능액 표시.
- 취소·환불 내역: `canceled_amount>0`이면 누적 환불액·잔여·취소시각 표시. 상태 표시는 부분이면 "부분취소".
- 목록/단건탭/정산/엑셀에서 `status=='CANCELED'`로만 환불을 보이던 곳을 `canceled_amount>0`로 확장.

## 라우트 (`app/admin/routes/payments.py`)

- `POST /admin/payments/{id}/cancel`: 폼 `cancel_amount`(빈값=전액) 받아 `admin_cancel_one_off_payment` 호출. CSRF·스코프 검증.
- `payment_detail`: 잔여 환불가능액 계산해 템플릿에 전달.

## 테스트

- 어드민 전액 취소(수수료 0, canceled_amount=amount, CANCELED).
- 부분취소 1회(DONE 유지, canceled_amount=X), 누적 2회→전액 도달 시 CANCELED.
- 잔여 초과 cancel_amount 거부, 토스 실패 시 보존.
- 부분취소 후 대시보드 일반매출=amount−환불, 환불금액=누적, 정산 net 일치.
- 외부 사용자 취소(수수료·게이트) 회귀 — 기존 테스트 유지.

## 비범위

- `PARTIAL_CANCELED` enum/마이그레이션 — 도입 안 함.
- 외부 API 사용자 취소의 부분취소/누적 — 범위 아님(기존 단발 유지).
