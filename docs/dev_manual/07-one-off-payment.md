# 07. 단건(일반) 결제 + 취소

> **상호참조**: 인증 공통 → [03. 인증과 보안 공통](03-auth-and-security.md) |
> 테이블 구조 → [02. 데이터베이스](02-database.md) |
> 서비스 취소정책 → [09. 서비스 등록·키 발급/회전·취소정책·담당자](09-services-registry.md) |
> 외부 API 전체 레퍼런스 → [15. 외부 API 레퍼런스](15-external-api-and-sample.md)

---

## 1. 한 줄 요약

구독과 **완전히 무관한 1회성** 결제를 생성하고, 필요하면 취소(환불)하는 기능입니다.
빌링키를 발급해 즉시 청구하고, **성공·실패·타임아웃을 막론하고 빌링키를 삭제**합니다(카드 정보를 서버에 보관하지 않음).

---

## 2. 언제 실행되나

| 트리거 | 설명 |
|--------|------|
| **외부 서비스 API** `POST /api/v1/payments` | 사내 서비스가 사용자의 단건 결제를 요청할 때 |
| **외부 서비스 API** `POST /api/v1/payments/{order_id}/cancel` | 사내 서비스가 결제 취소를 요청할 때 |
| **어드민 콘솔** `POST /admin/payments/{payment_id}/cancel` | 운영자가 어드민 화면에서 결제를 직접 취소할 때 |

---

## 3. 요청 진입점

### 3-1. 결제 생성

**`POST /api/v1/payments`**

- 라우트: `app/api/v1/payments.py:44` — `create_payment()`
- 인증 의존성: `payment_rate_limit`(= `authenticate_service` + 결제 전용 추가 한도)
  → `app/api/deps.py:141`

#### 요청 헤더 (HMAC 인증)

```
x-service-key: <API 키 평문>
x-timestamp:   <Unix timestamp(초)>
x-nonce:       <1회용 임의 문자열>
x-signature:   HMAC-SHA256(secret, "METHOD\nPATH\ntimestamp\nnonce\nSHA256(body)")
```

인증 6단계 상세는 `app/api/deps.py:77` `authenticate_service()` 참조.
HMAC 서명에 **요청 본문 전체가 포함**되므로 중간자가 `amount`를 변조하면 서명 검증에서 즉시 거부됩니다(`app/api/v1/payments.py:4–6` 주석 참조).

#### 요청 본문 (`OneOffPaymentRequest`, `app/schemas/api.py:114`)

| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| `external_user_id` | string | 1–255자 | 결제 대상 외부 사용자 ID |
| `order_id` | string | 6–64자, `[A-Za-z0-9\-_=.]` 패턴 | 주문 ID. **서비스 내 고유**(감사 Phase 2 — 타 서비스와 중복 허용, 선점 불가). 같은 order_id 재시도는 기존 결제 반환(멱등). 토스에는 서버 생성 전역 고유 `toss_order_id`가 전달됨 |
| `order_name` | string | 1–100자 | 결제창에 표시되는 주문명. **`payments.order_name`에 저장되어 결제 상세 화면에 "상품명"으로 표시**된다 |
| `amount` | int | gt=0, le=100,000,000 | 결제 금액(원). 구독과 달리 **클라이언트가 직접 지정**. 1억원 초과는 거부(감사 Phase 2 — 보안 L-3) |
| `auth_key` | string | 1–300자 | 토스 결제창에서 발급받은 authKey |
| `customer_key` | string | 2–300자, `[A-Za-z0-9\-_=.@]` | 토스 customerKey |

> **왜 금액을 클라이언트가 지정하나?** 구독 결제는 서버가 `Plan`에서 금액을 계산하지만, 단건은 기준 Plan이 없습니다. 대신 HMAC 서명이 본문 전체를 보호하므로 중간 변조는 서명 오류로 차단됩니다(`app/schemas/api.py:117` 주석).

#### 응답 (`PaymentResponse`, `app/schemas/api.py`)

```json
{
  "order_id": "order-abc-001",
  "amount": 15000,
  "status": "DONE",
  "kind": "ONE_OFF",
  "payment_type": "ONE_OFF",
  "failure_code": null,
  "failure_message": null,
  "requested_at": "2026-06-10T03:00:00Z",
  "approved_at": "2026-06-10T03:00:01Z",
  "cancelable": true,
  "cancel_fee_percent": 10,
  "cancel_fee": 1500,
  "cancel_refund_amount": 13500
}
```

`toss_payment_key`·`raw_response` 등 내부 필드는 **응답에 포함하지 않습니다**. 취소 수수료 필드(`cancelable`·`cancel_fee_percent`·`cancel_fee`·`cancel_refund_amount`)는 서비스가 취소 화면에 수수료/환불 예정액을 안내하도록 함께 반환합니다. 취소 가능 결제(DONE)는 예상액, 이미 취소된 결제(CANCELED)는 실제 차감/환불액입니다(`PaymentResponse.from_model`).

---

### 3-2. 결제 취소

**`POST /api/v1/payments/{order_id}/cancel`**

- 라우트: `app/api/v1/payments.py:73` — `cancel_payment()`

#### 요청 본문 (`OneOffCancelRequest`, `app/schemas/api.py:130`)

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `reason` | string | `"사용자 취소"` | 취소 사유. 토스 `cancelReason`으로 전달. 최대 200자 |

---

### 3-3. 결제 내역 조회

**`GET /api/v1/payments/{external_user_id}`**

- 라우트: `app/api/v1/payments.py` — `list_payments()`

> **단건 결제도 이 API에 포함됩니다.**
>
> `Payment.service_id` + `Payment.external_user_id`로 필터하므로 **구독 정기결제와 단건(ONE_OFF) 결제가 한 응답에 모두** 반환됩니다(이전에는 Subscription INNER JOIN으로 단건이 제외됐으나, 취소 가능한 단건 결제의 수수료 안내를 위해 포함하도록 변경). 범위 격리는 `Payment.service_id`로 보장합니다.
>
> 각 결제에는 취소 수수료 안내 필드(`cancelable`, `cancel_fee_percent`, `cancel_fee`, `cancel_refund_amount`)가 함께 반환되어, 서비스가 취소 화면에 "지금 취소 시 수수료/환불액"을 노출할 수 있습니다.

---

## 4. 단계별 처리 흐름

### 4-1. 결제 생성 (`create_one_off_payment`)

위치: `app/services/payments.py:26`

```
외부 서비스 HTTP 요청
    │
    ▼
[1] 입력 검증 (payments.py:53–60)
    - order_id 패턴: ^[A-Za-z0-9\-_=.]{6,64}$
    - customer_key 패턴: ^[A-Za-z0-9\-_=.@]{2,300}$
    - external_user_id: 1–255자
    - amount: 양수
    │
    ▼
[2] 멱등성 검사 (payments.py:63–67)
    - SELECT Payment WHERE order_id = ?
    - 같은 서비스의 order_id가 이미 존재 → 재결제 없이 기존 Payment 반환
    - 다른 서비스의 order_id → ConflictError(409)
    │
    ▼
[3] PENDING 선커밋 (payments.py:70–101)
    - Payment(status=PENDING, subscription_id=NULL, kind=ONE_OFF) INSERT
    - db.flush() → IntegrityError 시 롤백 후 재조회(race condition 대비)
    - record_audit("payment.one_off", ...) → db.commit()
    │
    ▼
[4] 빌링키 발급 (payments.py:104–116)
    - toss.issue_billing_key(auth_key, customer_key)
    - TossError → FAILED 확정 + audit("payment.one_off_failed") + 빌링키 삭제 없음(발급 실패했으니 없음)
    │
    ▼
[5] 결제 실행 resolve_charge (payment_utils.py:38)
    - toss.charge(billing_key, customer_key, amount, order_id, ...)
    - TossTimeoutError → PENDING 유지(절대 FAILED 처리하지 않음) + audit("payment.one_off_unresolved") + 빌링키 best-effort 삭제
    - TossError(카드 거절 등) → FAILED 확정 + audit("payment.one_off_failed") + 빌링키 삭제
    │
    ▼
[6] 결제 확정 (payments.py:156–162)
    - status=DONE, toss_payment_key, approved_at, raw_response 기록
    - db.commit()
    - 빌링키 즉시 삭제 safe_delete_billing_key() — 단건은 카드 미보관
```

#### 결제 3원칙 (`app/services/payments.py:40–44`)

1. **PENDING 선커밋**: 네트워크 장애 전에 레코드를 내구성 있게 확보합니다.
2. **타임아웃 = PENDING 유지**: 결과 불명 상태에서 절대 FAILED 처리하지 않습니다. 이중 결제가 발생할 수 있기 때문입니다.
3. **멱등 order_id**: 같은 `order_id`로 재시도해도 기존 결제를 그대로 반환하고 재결제하지 않습니다.

#### 타임아웃 처리 상세 (`app/services/payment_utils.py:38–57`)

`resolve_charge()`는 `toss.charge()` 타임아웃 시 `toss.get_payment_by_order_id(order_id)`로 재조회를 시도합니다. 토스에서 DONE이 확인되면 성공 반환, 미확인이면 `TossTimeoutError`를 다시 던집니다. 상위 `create_one_off_payment()`는 이를 받아 PENDING 유지 후 빌링키를 best-effort 삭제합니다.

PENDING으로 남은 결제는 이후 **정산 스윕(renewals.process_due)**이 재확인해 DONE으로 확정합니다(`test_one_off_payment.py:74`의 `test_reconcile_confirms_one_off` 참조).

---

### 4-2. 결제 취소 (`cancel_one_off_payment`)

위치: `app/services/payments.py:166`

```
외부 서비스 HTTP 요청 (또는 어드민)
    │
    ▼
[1] 결제 조회 (payments.py:182–184)
    - SELECT Payment WHERE order_id = ?
    - payment.service_id ≠ 호출 서비스 → NotFoundError(404)
      (타 서비스 결제 접근 차단)
    │
    ▼
[2] 취소 가능 상태 확인 (payments.py:187–188)
    - kind ≠ ONE_OFF 또는 status ≠ DONE → ConflictError(409)
    │
    ▼
[3] 서비스 취소 정책 확인 (payments.py:191–192)
    - service.cancellation_enabled = False → PaymentFailedError(code="CANCEL_DISABLED")
    │
    ▼
[4] 환불액 계산 (compute_cancel_fee, app/services/billing_math.py)
    - fee = amount × cancellation_fee_percent // 100  (정수 내림)
    - refund = amount - fee
    - 결제 조회 응답(PaymentResponse)·어드민 화면도 같은 함수를 공유
    │
    ▼
[5] 토스 취소 API 호출 (payments.py:198–217)
    - refund > 0 인 경우에만 toss.cancel_payment() 호출
    - 전액취소(fee=0): cancelAmount 생략 → 토스 전액 환불
    - 부분취소(fee>0): cancelAmount=refund → 토스 부분 환불
    - refund = 0(100% 수수료): toss.cancel_payment() 자체를 생략
    - TossError → status DONE 유지 + audit("payment.cancel_failed") + PaymentFailedError
    │
    ▼
[6] 취소 확정 (payments.py:220–233)
    - status=CANCELED
    - canceled_amount=refund, cancel_fee=fee, canceled_at=now
    - audit("payment.canceled", detail={refund, fee})
    - db.commit()
```

---

### 4-3. 어드민 결제 취소 분기

어드민에서 취소할 때는 `app/admin/routes/payments.py:112` `payment_cancel()`이 호출됩니다.

```python
# app/admin/routes/payments.py:133–135
await payment_service.cancel_one_off_payment(
    db, toss, service=service, order_id=payment.order_id, reason="관리자 취소",
    actor_user_id=ctx.user.id)   # ← 관리자 UUID를 전달
```

`actor_user_id`가 있으면 감사 로그에 `actor_type="USER"`로 기록되어 "어떤 관리자가 취소했는지"가 남습니다. 외부 서비스 API 호출은 `actor_user_id=None`이므로 `actor_type="SERVICE"`로 기록됩니다(`app/services/payments.py:207–215`).

---

## 5. 사용하는 DB 테이블·컬럼

### `payments` 테이블 (`app/models/payment.py`)

| 컬럼 | 단건 결제 시 값 | 설명 |
|------|----------------|------|
| `id` | UUID | PK |
| `subscription_id` | **NULL** | 단건은 구독 없음 |
| `service_id` | 호출 서비스 UUID | 서비스 격리에 사용 |
| `external_user_id` | 요청 값 | 추적용 |
| `kind` | `"ONE_OFF"` | 구독 결제와 구분 |
| `payment_type` | `"ONE_OFF"` | FIRST/RENEWAL/RETRY 아님 |
| `order_id` | 요청 값 (UNIQUE) | 멱등 키 |
| `amount` | 요청 값 | 청구 금액(원) |
| `status` | `PENDING`→`DONE`/`FAILED`/`CANCELED` | 처리 상태 |
| `toss_payment_key` | 토스 paymentKey | DONE 후 채워짐; 취소에 사용 |
| `failure_code` | 토스 에러 코드 | FAILED일 때만 |
| `failure_message` | 토스 에러 메시지 | FAILED일 때만 |
| `idempotency_key` | `order_id`와 동일 | 토스 API 멱등성 |
| `requested_at` | 생성 시각(UTC) | 항상 채워짐 |
| `approved_at` | 토스 승인 시각(UTC) | DONE 후 채워짐 |
| `raw_response` | 토스 응답 원문(JSONB) | DONE 후 채워짐 |
| `canceled_amount` | 환불액(원) | CANCELED 후 채워짐 |
| `cancel_fee` | 수수료(원) | CANCELED 후 채워짐 |
| `canceled_at` | 취소 시각(UTC) | CANCELED 후 채워짐 |

### `audit_logs` 테이블 (`app/models/audit_log.py`)

| action 값 | 발생 시점 | actor_type |
|-----------|-----------|------------|
| `payment.one_off` | PENDING 선커밋 직후 (정상 흐름 시작) | SERVICE |
| `payment.one_off_failed` | 빌링키 발급 실패 또는 카드 거절 | SERVICE |
| `payment.one_off_unresolved` | 타임아웃(결과 불명) | SERVICE |
| `payment.canceled` | 취소 성공 확정 | SERVICE 또는 USER(어드민) |
| `payment.cancel_failed` | 토스 취소 API 실패 | SERVICE 또는 USER(어드민) |

`detail` JSONB에는 `external_user_id`, `amount`, 실패 시 `code`, 취소 시 `refund`·`fee` 등이 저장됩니다.

### `services` 테이블 (취소 정책 관련, `app/models/service.py:30–33`)

| 컬럼 | 기본값 | 설명 |
|------|--------|------|
| `cancellation_enabled` | `true` | False이면 취소 전면 차단 |
| `cancellation_fee_percent` | `0` | 취소 수수료율(0~100 %) |

---

## 6. 상태 전이

```
요청 수신
    │
    ▼
[PENDING]  ← DB에 선커밋. 이 시점에서 장애가 나도 레코드는 남는다.
    │
    ├─── 빌링키 발급 실패 → [FAILED]
    │
    ├─── 카드 거절 등 결제 실패 → [FAILED]
    │
    ├─── 타임아웃(결과 불명) → [PENDING 유지] (정산 스윕이 나중에 확정)
    │
    └─── 결제 성공 → [DONE]
              │
              └─── 취소 성공 → [CANCELED]
                   (FAILED·PENDING·이미 취소된 결제는 취소 불가)
```

FAILED 상태는 최종 상태입니다. 재시도가 필요하면 **새 `order_id`로 새 요청**을 보내야 합니다.

---

## 7. 예외·엣지 케이스 / 에러 응답

| 상황 | 발생 위치 | 에러 클래스 | HTTP | 클라이언트가 받는 `code` |
|------|-----------|------------|------|--------------------------|
| `order_id` 형식 불일치 | `payments.py:53` | `InputValidationError` | 422 | `VALIDATION_ERROR` |
| `customer_key` 형식 불일치 | `payments.py:55` | `InputValidationError` | 422 | `VALIDATION_ERROR` |
| `amount` ≤ 0 | `payments.py:59` | `InputValidationError` | 422 | `VALIDATION_ERROR` |
| 다른 서비스가 이미 쓴 `order_id` | `payments.py:66` | `ConflictError` | 409 | `CONFLICT` |
| 빌링키 발급 실패(토스 오류) | `payments.py:107` | `PaymentFailedError` | 402 | 토스 코드 원문 |
| 카드 거절 등 결제 실패 | `payments.py:153` | `PaymentFailedError` | 402 | 토스 코드 원문 |
| 타임아웃(결과 불명) | `payments.py:139` | `PaymentFailedError` | 503 | `PAYMENT_UNRESOLVED` |
| 취소: 결제 없음 또는 타 서비스 | `payments.py:184` | `NotFoundError` | 404 | `NOT_FOUND` |
| 취소: DONE이 아닌 상태 | `payments.py:188` | `ConflictError` | 409 | `CONFLICT` |
| 취소: 서비스 정책 차단 | `payments.py:192` | `PaymentFailedError` | 402 | `CANCEL_DISABLED` |
| 취소: 토스 취소 API 실패 | `payments.py:217` | `PaymentFailedError` | 402 | 토스 코드 원문 |
| 결제 API 처리율 초과 | `deps.py:154` | `RateLimitedError` | 429 | `RATE_LIMITED` |

### 특수 케이스 상세

**같은 `order_id` 재요청(멱등)**
같은 서비스에서 동일한 `order_id`를 다시 보내면 기존 Payment를 그대로 반환하고 토스 API를 재호출하지 않습니다(`payments.py:63–67`). 네트워크 재시도 시 이중 결제가 발생하지 않습니다.

**타임아웃 후 503 응답**
클라이언트가 503을 받으면 즉시 재시도하면 안 됩니다. 잠시 후 `GET /api/v1/payments/{external_user_id}`로 상태를 조회(단건 결제도 응답에 포함됨)하거나, 어드민에서 해당 `order_id`를 검색해 결과를 확인해야 합니다.

**100% 수수료(환불액=0)**
`cancellation_fee_percent=100`이면 `refund=0`이 되어 토스 취소 API 호출을 **완전히 생략**합니다(`payments.py:199`). 그럼에도 `status=CANCELED`로 변경되고 `canceled_amount=0`, `cancel_fee=amount`가 기록됩니다(`test_payment_cancel.py:99`).

**수수료가 있는 부분취소**
`cancellation_fee_percent=10`, `amount=10_000`이면 `fee=1_000`, `refund=9_000`이 되고 토스 `cancel_payment(cancel_amount=9_000)`을 호출합니다. 토스는 9,000원만 환불하고 1,000원은 차감합니다(`test_payment_cancel.py:40`).

---

## 8. 어드민 결제 목록·상세

### 결제 목록 (`GET /admin/payments`)

위치: `app/admin/routes/payments.py:164`

- Subscription을 **OUTER JOIN** 합니다(`payments.py:59`). INNER JOIN이면 단건 결제(`subscription_id=NULL`)가 목록에서 사라지기 때문입니다.
- Plan도 Subscription을 통해 OUTER JOIN하므로 단건 결제는 요금제명이 비어있습니다.
- `kind` 필터로 `ONE_OFF`만 또는 `SUBSCRIPTION`만 볼 수 있습니다.
- `plan_name` 필터를 선택하면 구독 결제만 나옵니다(단건은 Plan이 없어 자동 제외).
- SYSTEM_ADMIN은 전체, SERVICE_MANAGER는 자신이 담당한 서비스만 조회됩니다.

### 결제 상세 (`GET /admin/payments/{payment_id}`)

위치: `app/admin/routes/payments.py:140`

단건 결제는 `subscription_id`가 없으므로 구독 정보(`sub`)가 `None`으로 전달됩니다(`payments.py:158–160`). 템플릿에서 `sub`가 None인 경우를 분기 처리합니다.

### 실패 코드 툴팁

결제 내역에 `failure_code`가 있으면 어드민 화면이 한글 설명을 툴팁으로 보여줍니다.
매핑 위치: `app/admin/payment_error_labels.py:12` `PAYMENT_ERROR_LABELS` 딕셔너리.

주요 단건 결제 관련 코드:

| 코드 | 설명 |
|------|------|
| `REJECT_CARD_COMPANY` | 카드사에서 결제 승인을 거절했습니다 |
| `REJECT_CARD_PAYMENT` | 한도 초과 또는 잔액 부족 |
| `INVALID_STOPPED_CARD` | 정지된 카드 |
| `BELOW_MINIMUM_AMOUNT` | 최소 결제금액 미만(신용카드 100원, 계좌 200원) |
| `EXCEED_MAX_AMOUNT` | 거래금액 한도 초과 |
| `PROVIDER_ERROR` | 일시적 오류, 잠시 후 재시도 필요 |
| `CANCEL_DISABLED` | 이 서비스는 결제 취소가 허용되지 않음 |
| `PAYMENT_UNRESOLVED` | 타임아웃으로 결과 불명. 정산 스윕이 재확인 |

코드가 매핑에 없으면 `payment_error_meaning()`이 빈 문자열을 반환하고, 화면은 `failure_message` 원문으로 폴백합니다(`app/admin/payment_error_labels.py:51–55`).

### 엑셀 내보내기 (`GET /admin/payments/export.xlsx`)

위치: `app/admin/routes/payments.py:89`

현재 필터/검색 조건을 그대로 적용해 전체 행을 xlsx로 다운로드합니다. 페이지네이션을 무시하고 쿼리를 직접 실행합니다. `kind` 컬럼은 `"구독"` / `"일반"`으로 한글 변환됩니다(`payments.py:103`).

---

## 9. 관련 테스트

### `tests/integration/test_one_off_payment.py`

| 테스트 함수 | 검증 내용 |
|------------|-----------|
| `test_one_off_success_deletes_billing_key` (line 30) | 성공 후 `status=DONE`, `kind=ONE_OFF`, `subscription_id=None`, 빌링키 삭제 호출 확인 |
| `test_one_off_idempotent_same_order_id` (line 39) | 같은 `order_id` 재시도 시 재결제 없이 기존 Payment 반환 |
| `test_one_off_other_service_order_id_conflicts` (line 47) | 다른 서비스의 `order_id` 충돌 시 `ConflictError` |
| `test_one_off_card_declined_failed` (line 55) | 카드 거절 시 `status=FAILED` 확정 |
| `test_one_off_timeout_pending` (line 64) | 타임아웃 시 `status=PENDING` 유지 + 빌링키 삭제 |
| `test_reconcile_confirms_one_off` (line 74) | PENDING 결제를 정산 스윕이 DONE으로 확정 |

### `tests/integration/test_payment_cancel.py`

| 테스트 함수 | 검증 내용 |
|------------|-----------|
| `test_cancel_full_refund_no_fee` (line 30) | 수수료 0% → 전액취소, `canceled_amount=amount`, 토스 `cancelAmount=None` |
| `test_cancel_partial_with_fee` (line 40) | 수수료 10% → `cancel_fee=1000`, `canceled_amount=9000`, 토스 `cancelAmount=9000` |
| `test_cancel_disabled` (line 50) | `cancellation_enabled=False` → `PaymentFailedError` |
| `test_cancel_rejects_non_done_or_other_service` (line 59) | 타 서비스 결제 취소 → `NotFoundError` |
| `test_cancel_toss_error_keeps_done` (line 68) | 토스 취소 실패 → `status=DONE` 유지 + `payment.cancel_failed` 감사 로그 |
| `test_cancel_rejects_non_done` (line 88) | `status=FAILED` 결제 취소 → `ConflictError` |
| `test_cancel_full_fee_no_refund` (line 99) | 수수료 100% → 토스 취소 생략, `canceled_amount=0`, `status=CANCELED` |

#### 테스트 실행 방법

```bash
# 단건 결제 테스트만
pytest tests/integration/test_one_off_payment.py -v

# 취소 테스트만
pytest tests/integration/test_payment_cancel.py -v

# 둘 다
pytest tests/integration/test_one_off_payment.py tests/integration/test_payment_cancel.py -v
```

테스트는 `FakeTossClient`(`app/toss/fake.py`)로 실제 토스 HTTP 호출 없이 실행됩니다.
`fake.fail_charge_with = TossError(...)` 등으로 실패 시나리오를 주입합니다.

---

## 10. 유지보수 팁

### 환불 수수료 정책 변경

서비스별 수수료율은 어드민 서비스 상세 → 취소 정책에서 설정합니다.
코드 기준으로는 `Service.cancellation_fee_percent`(`app/models/service.py:33`)입니다.

수수료 계산 공식이 바뀌면 `app/services/billing_math.py`의 `compute_cancel_fee()` 한 곳만 수정하면 됩니다. 실제 취소 처리(`cancel_one_off_payment`)와 결제 조회 응답(`PaymentResponse.from_model`), 어드민 화면이 모두 이 함수를 공유하므로 값 불일치가 생기지 않습니다.

```python
# app/services/billing_math.py
def compute_cancel_fee(amount: int, fee_percent: int) -> tuple[int, int]:
    fee = amount * fee_percent // 100
    return fee, amount - fee
```

정수 나눗셈(`//`)으로 소수점을 내림합니다. 예: 10,001원 × 10% = 1,000원(나머지 버림).

### 단건 결제가 결제 내역 API(`GET /api/v1/payments/{external_user_id}`)에 안 나오는 이유

`app/api/v1/payments.py:34–40`의 쿼리가 `Subscription`을 INNER JOIN하기 때문입니다.
단건 결제는 `subscription_id=NULL`이라 JOIN에서 제외됩니다.
이는 설계 의도입니다. 외부 서비스는 자신의 구독 결제 내역만 조회할 수 있고, 단건 결제는 어드민 전용입니다.

외부 서비스에도 단건 결제 조회가 필요하다면 `order_id`로 조회하는 별도 엔드포인트를 추가해야 합니다.

### 실패 코드 의미 추가

새 토스 에러 코드가 나타나면 `app/admin/payment_error_labels.py:12` `PAYMENT_ERROR_LABELS` 딕셔너리에 코드-설명 쌍을 추가합니다. 추가하지 않아도 동작에는 영향 없고, 어드민 툴팁에 설명이 안 보일 뿐입니다.

### PENDING 결제가 오래 남아있을 때

타임아웃으로 인한 PENDING 결제는 `app/services/renewals.py`의 `process_due()`(정산 스윕)가 `requested_at`이 10분 이상 지난 결제를 대상으로 토스 재조회해 DONE/FAILED로 확정합니다. 스케줄러가 5분 간격으로 실행합니다(`app/core/config.py:55`).

수동 확인이 필요하면 어드민 결제 목록에서 `status=PENDING`, `kind=ONE_OFF`로 필터링합니다.

### 빌링키 삭제 실패 시

`safe_delete_billing_key()`(`app/services/payment_utils.py:24`)는 삭제 실패를 예외 없이 삼키고 `False`를 반환합니다. 로그(`payment.utils`)에 경고가 남습니다. 토스에 고아 빌링키가 잔존할 수 있으므로 로그 모니터링 후 수동으로 토스 콘솔에서 삭제해야 합니다.

### 처리율 제한

일반 API: 분당 120회(`rate_limit_per_minute`, `app/core/config.py:47`).
결제 API: 분당 20회 추가 제한(`rate_limit_payment_per_minute`, `app/core/config.py:49`).
두 제한 모두 Redis 슬라이딩 윈도우 카운터로 계산하며, 서비스 단위로 독립 적용됩니다(`app/api/deps.py:141–155`).
