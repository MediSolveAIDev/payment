# 요청 012 설계 — 구독 1건(실패 시 결제) + 일반결제 취소(서비스별 정책·수수료)

날짜: 2026-06-09
상태: 승인됨
요청: docs/requests/012.md

## 결정 사항
- 구독 1건 규칙은 이미 구현됨(추가 구독 차단·일반결제 무관 허용). 보완: **수동 구독결제(manual_charge)를 SUSPENDED + PAST_DUE 둘 다 허용**.
- 일반결제 취소 정책은 **Service 모델**에 저장(서비스별). `cancellation_enabled`(기본 True), `cancellation_fee_percent`(기본 0, 0~100).
- 취소 수수료는 **공제 후 환불**: 환불액 = `amount − (amount × fee% // 100)`(토스 부분취소). 수수료는 서비스가 차감.
- 취소 창구: 외부 API(`POST /api/v1/payments/{order_id}/cancel`) + Admin 결제상세 버튼 + 샘플 shop.
- Payment에 취소 기록 필드 추가(`canceled_amount`·`cancel_fee`·`canceled_at`). 환불 집계는 `canceled_amount` 기준으로 정확화.

## 1. 구독 1건 — 실패 시 구독결제 허용
- `app/services/subscriptions.py` `manual_charge_subscription`: 허용 상태를 `SUSPENDED`만 → `(SUSPENDED, PAST_DUE)`로 확장. 그 외 상태는 기존대로 거부(ConflictError/InputValidationError — 현재 메시지 유지). 성공 시 ACTIVE 복귀·기간 전진 로직 변경 없음.
- 외부 `POST /api/v1/subscriptions/{external_user_id}/pay`(api/v1/subscriptions.py `manual_pay`)는 이 함수를 호출 → 자동 반영.
- 구독 1건 인덱스(`uq_subscriptions_one_per_user`)·`create_subscription` 중복 차단은 변경 없음(확인용 주석만).

## 2. Service 취소 정책 (모델 + 마이그레이션 + Admin)
- `app/models/service.py`: 컬럼 추가
  - `cancellation_enabled: Mapped[bool]` = mapped_column(default=True, server_default="true")
  - `cancellation_fee_percent: Mapped[int]` = mapped_column(default=0, server_default="0")  # 0~100
- 마이그레이션 `d4e5f6a7b8c9_service_cancel_policy`(down=현재 head): 두 컬럼 add(server_default). downgrade drop.
- `app/services/registry.py`: `register_service`·정책 수정 경로에서 두 값 설정/검증(0~100 범위; 범위 밖이면 InputValidationError). 신규 `update_cancel_policy(db, service_id, *, enabled, fee_percent)` 또는 기존 update 함수 확장(구현 시 단순한 쪽 선택, 검증 포함).
- Admin 서비스 등록/수정 화면(`app/admin/routes/services.py` + `services/new.html`·`detail.html` 또는 편집 폼): "취소 허용" 체크박스 + "취소 수수료(%)" 입력 추가. 폼 파싱·검증.

## 3. 일반결제 취소 — 토스/도메인/모델
### 토스 클라이언트 (`app/toss/client.py`)
- `Protocol`과 `HttpTossClient`에 `cancel_payment(payment_key: str, reason: str, *, cancel_amount: int | None = None)` 추가.
  - `POST /v1/payments/{payment_key}/cancel`, body `{"cancelReason": reason}` (+ `"cancelAmount": cancel_amount` 부분취소 시).
  - 응답을 ChargeResult 유사로 파싱하거나 raw dict 반환(구현 시 기존 charge 응답 처리 패턴 따름). 타임아웃/오류는 기존 TossError/TossTimeoutError 매핑.
- `app/toss/fake.py`: `cancel_payment` 구현(호출 기록 + 실패 주입 훅), 취소된 order 상태 반영.

### Payment 모델 (`app/models/payment.py`) + 마이그레이션
- 추가: `canceled_amount: Mapped[int | None]`(실제 환불액), `cancel_fee: Mapped[int | None]`(차감 수수료), `canceled_at: Mapped[datetime | None]`.
- 동일 마이그레이션 또는 별도(`...payment_cancel_fields`): 세 컬럼 add(nullable). 기존 행 영향 없음.

### 서비스 (`app/services/payments.py`)
- `cancel_one_off_payment(db, toss, *, service, order_id, reason) -> Payment`:
  1. `Payment where order_id==order_id` 조회. 없거나 `service_id != service.id` → NotFoundError("결제를 찾을 수 없습니다").
  2. `kind != ONE_OFF or status != DONE` → ConflictError("취소할 수 없는 결제입니다").
  3. `not service.cancellation_enabled` → PaymentFailedError("취소가 허용되지 않는 서비스입니다", code="CANCEL_DISABLED").
  4. `fee = payment.amount * service.cancellation_fee_percent // 100`; `refund = payment.amount - fee`.
  5. 토스 취소: `cancel_amount = refund`(refund < amount면 부분취소, == amount면 전액). `refund <= 0`(수수료 100%)면 토스 호출 생략하고 환불 0으로 기록(엣지).
  6. 성공 → `status=CANCELED`, `canceled_amount=refund`, `cancel_fee=fee`, `canceled_at=utcnow()`, raw 갱신. 감사 `payment.canceled`(detail: refund/fee). commit.
  7. TossError → PaymentFailedError(상태 DONE 유지, 감사 `payment.cancel_failed`). 타임아웃은 동일하게 실패 처리(상태 미변경) — 정합성 스윕 대상 아님(취소는 멱등 재시도 가능).
- 감사 라벨(`app/admin/audit_labels.py`): `payment.canceled`("결제 취소"), `payment.cancel_failed`("결제 취소 실패").

### 환불 집계 정확화 (`app/services/dashboard.py`)
- `_refund_between`: `func.sum(Payment.amount)` → `func.sum(func.coalesce(Payment.canceled_amount, Payment.amount))`(부분환불 반영, 레거시/웹훅 null은 amount).
- `_service_revenue`의 refund 서브쿼리도 동일하게 `coalesce(canceled_amount, amount)`.
- 웹훅 CANCELED 경로(`app/services/webhooks.py` `_handle_payment_status_changed`): 외부 전액취소 동기화 시 `canceled_amount = payment.amount`, `canceled_at = utcnow()` 기록(있으면 유지).

## 4. 창구
### 외부 API (`app/api/v1/payments.py`)
- `POST /payments/{order_id}/cancel` — `Depends(payment_rate_limit)`, body `OneOffCancelRequest(reason: str = "사용자 취소")`(스키마 추가, reason 선택·기본값). `cancel_one_off_payment` 호출 → `PaymentResponse`. (라우트 순서: `{order_id}/cancel`는 `{external_user_id}` GET과 메서드/경로 구분되어 충돌 없음. 정적·동적 등록 순서 점검.)

### Admin (`app/admin/routes/payments.py` + `payments/detail.html`)
- `POST /payments/{payment_id}/cancel`(require_any + 스코프, validate_csrf). 결제상세에서 호출.
- `payments/detail.html`: ONE_OFF·DONE이고 `service.cancellation_enabled`면 **취소 버튼**(수수료율 안내); 아니면 "취소 불가" 배지/문구. payment_detail 라우트가 service.cancellation_enabled/fee를 템플릿에 전달.

### 샘플 shop (`sample_service`)
- `payment_client`에 `cancel_one_off_payment(order_id, reason)` → `POST /api/v1/payments/{order_id}/cancel`.
- 결제 결과/내역 화면에 취소 버튼(취소불가면 안내 문구). 뷰 스모크 테스트.
- (샘플은 별도 git 저장소 — 거기서 커밋.)

## 5. 테스트
- 통합(`test_one_off_payment.py` 확장 또는 신규 `test_payment_cancel.py`): 취소 성공(수수료 0=전액, 수수료>0=부분환불+canceled_amount/cancel_fee), CANCEL_DISABLED 거부, 비-DONE/ONE_OFF 아님/타서비스 거부, 환불 집계 canceled_amount 반영. `manual_charge` PAST_DUE 허용(test_subscriptions/manage).
- e2e: 서비스 폼 취소정책 저장, admin 결제상세 취소/취소불가 노출, 외부 API 취소(`test_api_endpoints`/신규).
- 샘플: shop 뷰 스모크(취소 버튼/안내).

## 6. 매뉴얼 갱신 (이 작업에 포함)
- `11-one-off-payment.md`: "단건 결제 취소"(정책·수수료·API·상태 전이) 섹션 추가.
- `01-service-registry.md`: 서비스 취소 정책 설정 추가.
- `06-subscription-manage.md`: manual_charge PAST_DUE 허용 반영.
- `09-dashboard-settlement.md`: 환불=canceled_amount 집계로 갱신.
- `08-api-auth.md`/`00-overview.md`: 취소 API 엔드포인트 추가.

## 변경하지 않는 것
- 구독 1건 유니크 인덱스, create_subscription 중복 차단, 구독 결제 흐름, 첫결제/할인.
