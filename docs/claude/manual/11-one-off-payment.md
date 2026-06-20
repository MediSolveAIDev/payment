# 11. 단건(일반) 결제

> 구독 없이 **1회성으로 결제**하는 흐름. 구독 결제(문서 04)와 같은 "결제 3원칙"(PENDING 선커밋 /
> 타임아웃=결과 불명 / 멱등 order_id)을 따르되, **빌링키를 보관하지 않고**
> 발급→결제→즉시 삭제한다.
>
> 선행: [00-overview.md](00-overview.md), [08-api-auth.md](08-api-auth.md)(HMAC 서명),
> [07-payment-reconcile.md](07-payment-reconcile.md)(PENDING 정산 스윕).

---

## 0. 한눈에 보기

- **호출 주체**: 외부 서비스(서버). 구독 생성과 동일하게 HMAC 서명 + IP 화이트리스트.
- **엔드포인트**: `POST /api/v1/payments`
- **인증**: `payment_rate_limit` — 구독 생성·카드변경과 동일한 결제 전용 추가 throttle(문서 08).
- **구독과의 핵심 차이**: plan 없음, 금액은 **요청값**(HMAC 본문 서명으로 위변조 차단), 빌링키 미보관.

| 단계 | 코드 |
|---|---|
| HTTP 진입 + 인증/레이트리밋 | `app/api/v1/payments.py` `create_payment`, `api/deps.payment_rate_limit` |
| 요청 본문 검증(스키마) | `app/schemas/api.py` `OneOffPaymentRequest` |
| 도메인 로직 | `app/services/payments.py` `create_one_off_payment` |
| 토스 호출 #1 | `app/toss/client.py` `issue_billing_key` |
| 토스 호출 #2 | `app/services/subscriptions.py` `resolve_charge`(재사용) |
| 빌링키 정리 | `app/services/subscriptions.py` `safe_delete_billing_key`(재사용) |
| 응답 직렬화 | `app/schemas/api.py` `PaymentResponse` |
| DB 모델 | `app/models/payment.py` `Payment`, `app/models/enums.py` `PaymentKind.ONE_OFF` |
| 마이그레이션 | `alembic/versions/c3d4e5f6a7b8_payment_one_off.py` |

---

## 1. 요청/응답 구조

### 요청 — `OneOffPaymentRequest` (`schemas/api.py`)
```python
external_user_id : str   # 외부 서비스의 사용자 식별자(1~255자)
order_id         : str   # 외부 제공 주문번호(6~64자, [A-Za-z0-9-_=.] 패턴)
order_name       : str   # 결제 상품명(1~100자, 토스 화면·영수증에 표시)
amount           : int   # 결제 금액(gt=0, 요청값 — HMAC 서명으로 보호)
auth_key         : str   # 토스 빌링 인증키(프론트 SDK에서 받음)
customer_key     : str   # 토스 customerKey(2~300자)
```

구독 결제(`SubscriptionCreateRequest`)와의 **금액 처리 차이**: 구독은 요청에 금액 필드가 없고
서버가 plan에서 계산한다. 단건 결제는 **요청에 `amount`가 있다**. 대신 HMAC 서명이 본문 전체를
커버해 위변조를 차단한다(문서 08). 이 서비스가 HMAC 서명을 제공하지 않는 환경에서는 단건 결제
금액 필드를 노출하지 않는 것이 좋다.

### 응답 — `PaymentResponse` (`schemas/api.py`)
```python
order_id, amount, status, kind,          # kind == "ONE_OFF"
payment_type,                            # == "ONE_OFF"
failure_code, failure_message,           # 실패 시 기록
requested_at, approved_at               # 성공 시 approved_at 설정됨
```

---

## 2. HTTP 진입 — 라우트와 인증

```python
# api/v1/payments.py:31
@router.post("/payments", status_code=201)
async def create_payment(
    payload: OneOffPaymentRequest,
    service: Service = Depends(payment_rate_limit),   # ← 인증 + 결제 레이트리밋
    db=..., toss=..., cipher=...):
    payment = await payment_service.create_one_off_payment(
        db, toss, cipher,
        service=service,
        external_user_id=payload.external_user_id,
        order_id=payload.order_id,
        order_name=payload.order_name,
        amount=payload.amount,
        auth_key=payload.auth_key,
        customer_key=payload.customer_key,
    )
    return PaymentResponse.model_validate(payment)
```

라우트의 역할:
1. `Depends(payment_rate_limit)` — API키 해시 대조 → IP 화이트리스트 → 레이트리밋 →
   타임스탬프 → **HMAC 서명(본문 포함)** → nonce 1회용 → `Service` 확정.
2. 검증된 `service`와 파싱된 `payload`로 도메인 함수 호출 → `PaymentResponse`로 반환.

`cipher`가 인자로 들어오지만 도메인 함수 내에서 실제로 쓰이지 않는다(빌링키를 저장하지 않으므로).
구독 결제 함수와 인터페이스를 맞추고, 향후 빌링키 보관 옵션을 대비해 시그니처에 유지한다.

---

## 3. 도메인 로직 — `create_one_off_payment` (`services/payments.py:25`)

크게 **(A) 입력 검증 → (B) 멱등 확인 → (C) PENDING 선커밋 → (D) 빌링키 발급 → (E) 결제 실행 →
(F) 확정·빌링키 삭제** 순서로 읽는다.

### (A) 입력 검증

```python
if not ORDER_ID_RE.fullmatch(order_id or ""):        # [A-Za-z0-9-_=.]{6,64}
    raise InputValidationError
if not CUSTOMER_KEY_RE.fullmatch(customer_key or ""): # [A-Za-z0-9-_=.@]{2,300}
    raise InputValidationError
if not external_user_id or len(external_user_id) > 255:
    raise InputValidationError
if amount <= 0:
    raise InputValidationError("금액은 1원 이상이어야 합니다")
```

`ORDER_ID_RE`는 `payments.py`에 정의, `CUSTOMER_KEY_RE`는 `subscriptions.py`에서 임포트.

### (B) 멱등 확인 — 같은 `order_id`가 이미 있으면 재결제 없음

```python
existing = await db.scalar(select(Payment).where(Payment.order_id == order_id))
if existing is not None:
    if existing.service_id != service.id:
        raise ConflictError("이미 사용된 주문번호입니다")   # 타 서비스의 order_id → 충돌
    return existing                                         # 같은 서비스 → 기존 행 반환
```

- **같은 서비스 + 같은 `order_id`**: 기존 `Payment`를 그대로 반환. 토스 결제를 다시 호출하지 않는다.
  외부 서버가 네트워크 오류로 응답을 못 받아 재시도해도 이중 결제가 발생하지 않는다.
- **다른 서비스 + 같은 `order_id`**: 409 `ConflictError`. `Payment.order_id`가 DB 전체에서
  unique이기 때문에 서비스 A가 사용한 주문번호를 서비스 B가 재사용하면 충돌한다.

### (C) PENDING 선커밋 — 결제 전에 기록을 내구성 있게 확보

```python
now = utcnow()
payment = Payment(
    subscription_id=None,           # ★ 단건: 구독과 무관
    service_id=service.id,          # ★ 서비스 직접 연결
    external_user_id=external_user_id,
    order_id=order_id,
    amount=amount,
    payment_type=PaymentType.ONE_OFF,
    kind=PaymentKind.ONE_OFF,       # ★ 단건 구분자
    status=PaymentStatus.PENDING,
    idempotency_key=order_id,       # order_id가 곧 멱등 키
    requested_at=now,
)
db.add(payment)
try:
    await db.flush()
except IntegrityError:
    # 동시 요청 경쟁 — order_id unique 인덱스에 걸림
    await db.rollback()
    again = await db.scalar(select(Payment).where(Payment.order_id == order_id))
    if again is not None:
        if again.service_id != service.id:
            raise ConflictError(...) from None
        return again    # 먼저 커밋한 쪽이 이겼음 — 그 결과 반환
    raise

await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                   action="payment.one_off", target_type="payment",
                   target_id=str(payment.id),
                   detail={"external_user_id": external_user_id, "amount": amount})
await db.commit()   # ★ 토스 호출 전에 먼저 커밋
```

선커밋의 의미: 이 커밋 이후에 서버가 죽거나 토스가 타임아웃나도 **PENDING 기록이 남는다**.
갱신 배치의 PENDING 정산 스윕(문서 07)이 나중에 `order_id`로 토스에 재조회해 DONE/FAILED를 확정한다.

구독 결제와 달리 `idempotency_key`가 `order_id`와 동일하다(구독은 `f"first-{sub.id}"`). 단건은
외부에서 제공한 `order_id`가 결정적 멱등 키 역할을 하므로 별도 키를 만들지 않는다.

### (D) 빌링키 발급 (토스 호출 #1)

```python
try:
    bk = await toss.issue_billing_key(auth_key, customer_key)
except TossError as exc:
    payment.status = PaymentStatus.FAILED
    payment.failure_code = exc.code
    payment.failure_message = exc.message
    await record_audit(..., action="payment.one_off_failed", ...)
    await db.commit()
    raise PaymentFailedError(f"빌링키 발급 실패: {exc.message}", code=exc.code) from exc
```

발급 실패 시 **PENDING → FAILED**로 확정 후 4xx를 반환한다. 빌링키가 없으니 삭제할 키도 없다.
감사 action: `payment.one_off_failed`.

### (E) 결제 실행 (토스 호출 #2) — 세 가지 결말

`resolve_charge`(`subscriptions.py:70`)는 구독 결제와 완전히 동일한 함수를 재사용한다.

```python
try:
    result = await resolve_charge(
        toss,
        billing_key=bk.billing_key,
        customer_key=customer_key,
        amount=amount,
        order_id=order_id,
        order_name=order_name,
        idempotency_key=order_id,
    )
except TossTimeoutError as exc: ...
except TossError as exc: ...
```

`resolve_charge`의 내부 정책: 타임아웃 시 `toss.get_payment_by_order_id(order_id)`로 1회 재조회.
재조회 결과가 DONE이면 성공으로 수렴, 아니면 `TossTimeoutError`를 다시 올린다.

**① 성공(DONE)**
```python
payment.status = PaymentStatus.DONE
payment.toss_payment_key = result.payment_key
payment.approved_at = utcnow()
payment.raw_response = result.raw
await db.commit()
# 단건: 카드 미보관 — 성공 후 빌링키 즉시 삭제
await safe_delete_billing_key(toss, bk.billing_key)
return payment
```
결제 확정 후 빌링키를 즉시 삭제한다. 구독 결제는 빌링키를 계속 보관하지만(갱신 결제에 사용),
단건 결제는 더 이상 쓸 일이 없으므로 **바로 삭제**한다.

**② 확정 실패 — `TossError`(카드 거절 등)**
```python
payment.status = PaymentStatus.FAILED
payment.failure_code = exc.code
payment.failure_message = exc.message
await record_audit(..., action="payment.one_off_failed", ...)
await db.commit()
await safe_delete_billing_key(toss, bk.billing_key)   # 실패 후에도 키 삭제
raise PaymentFailedError(f"결제 실패: {exc.message}", code=exc.code) from exc
```
결제 실패 → FAILED 확정, 빌링키 삭제(best-effort), 4xx 반환.
감사 action: `payment.one_off_failed`.

**③ 결과 불명 — `TossTimeoutError`(절대 실패로 단정하지 않음)**
```python
await record_audit(..., action="payment.one_off_unresolved", ...)
await db.commit()
await safe_delete_billing_key(toss, bk.billing_key)   # best-effort 삭제
raise PaymentFailedError(PENDING_GRACE_MESSAGE, code="PAYMENT_UNRESOLVED", http_status=503)
```
- 결제는 **PENDING 유지** — "모름" 상태를 유지한다.
- 빌링키는 **best-effort 삭제** — 단건이므로 보관할 필요 없음. 삭제 실패해도 PENDING은 유지.
- 외부에는 **503 + "잠시 후 조회" 안내**.
- **갱신 배치의 PENDING 정산 스윕**(문서 07)이 `order_id`로 토스에 재조회해 추후 확정한다.
  스윕은 `outerjoin(Subscription, Payment.subscription_id == Subscription.id)`을 사용하므로
  `subscription_id=NULL`인 단건 결제도 포함된다.
감사 action: `payment.one_off_unresolved`.

---

## 4. 데이터 모델 — `Payment` (`models/payment.py`)

단건 결제로 생성된 `Payment` 행의 핵심 컬럼:

| 컬럼 | 단건 결제 값 | 의미 |
|---|---|---|
| `subscription_id` | `NULL` | 구독과 무관 |
| `service_id` | `service.id` | 어느 서비스의 결제인지 |
| `external_user_id` | 요청값 | 해당 서비스의 사용자 |
| `kind` | `"ONE_OFF"` | 단건/구독 구분자 |
| `payment_type` | `"ONE_OFF"` | 결제 유형 |
| `order_id` | 외부 제공값(unique) | 멱등 키 + 토스 주문번호 |
| `idempotency_key` | `order_id`와 동일 | 토스 멱등 호출 키 |
| `status` | `PENDING` → `DONE`/`FAILED` | 결제 상태 |
| `toss_payment_key` | 성공 시 설정 | 토스 결제 키(환불 등에 사용) |
| `approved_at` | 성공 시 설정 | 토스 승인 시각 |

### 마이그레이션 `c3d4e5f6a7b8` (`alembic/versions/c3d4e5f6a7b8_payment_one_off.py`)

단건 결제 기능을 위해 `payments` 테이블에 추가된 컬럼들:

```sql
ALTER TABLE payments
  ADD COLUMN kind           VARCHAR(20) NOT NULL DEFAULT 'SUBSCRIPTION',
  ADD COLUMN service_id     UUID        NOT NULL,   -- 구독 경유 없이 직접 서비스 참조
  ADD COLUMN external_user_id VARCHAR(255) NULL;
ALTER TABLE payments
  ALTER COLUMN subscription_id DROP NOT NULL;       -- 단건: subscription_id = NULL 허용
```

기존 데이터 백필: `UPDATE payments p SET service_id = s.service_id, external_user_id = s.external_user_id FROM subscriptions s WHERE p.subscription_id = s.id`
인덱스: `ix_payments_service_id`, `ix_payments_kind`

---

## 5. 구독 결제 vs 단건 결제 비교

| 항목 | 구독 결제 (문서 04) | 단건 결제 (이 문서) |
|---|---|---|
| 엔드포인트 | `POST /api/v1/subscriptions` | `POST /api/v1/payments` |
| 금액 출처 | 서버가 `plan`에서 계산 | **요청값** (`amount` 필드, HMAC 서명 보호) |
| `plan_id` 필요 | O | X |
| `Payment.kind` | `SUBSCRIPTION` | `ONE_OFF` |
| `Payment.subscription_id` | 구독 id | `NULL` |
| 빌링키 보관 | O (갱신 결제에 재사용) | X (발급 → 결제 → 즉시 삭제) |
| 결제 실패 시 상태 처리 | 구독 `EXPIRED` 처리 | `Payment.status=FAILED`만 (구독 없음) |
| 멱등 키 | `f"first-{sub.id}"` | `order_id`(외부 제공) |
| 정산 스윕 대상 | O (FIRST 타입이므로) | O (`subscription_id=NULL`이어도 outerjoin으로 포함됨) |

---

## 6. 전체 시퀀스

```
[외부 서버] POST /api/v1/payments  (HMAC 헤더 + payload: order_id, amount, auth_key, ...)
   │
   ▼ payment_rate_limit → authenticate_service
   │   API키 해시 / IP / 레이트리밋 / 타임스탬프 / HMAC 서명 / nonce  → Service 확정
   ▼ create_one_off_payment (도메인)
   ├─(A) 입력 검증(order_id 형식 / customer_key 형식 / external_user_id / amount > 0)
   ├─(B) 멱등 확인: order_id 이미 있으면?
   │      └ 같은 서비스 → 기존 Payment 반환(재결제 없음)
   │      └ 다른 서비스 → 409 ConflictError
   ├─(C) Payment(PENDING, kind=ONE_OFF, subscription_id=NULL) INSERT → flush → commit
   │     ★ (동시 경쟁 시 IntegrityError → rollback → 멱등 재확인)
   │     감사 action: payment.one_off
   ├─(D) 토스 issue_billing_key
   │      └ TossError → Payment=FAILED, 감사 one_off_failed, commit → 4xx
   └─(E) resolve_charge(billing_key, amount, order_id):
          ├ DONE        → Payment=DONE(toss_payment_key/approved_at), commit
          │              → safe_delete_billing_key (성공 후 즉시 삭제) → 201
          ├ TossError   → Payment=FAILED, 감사 one_off_failed, commit
          │              → safe_delete_billing_key → 4xx
          └ Timeout     → PENDING 유지, 감사 one_off_unresolved, commit
                        → safe_delete_billing_key (best-effort) → 503
                        (정산 스윕이 order_id로 토스 재조회 후 추후 확정)
   ▼
[외부 서버] 201 + PaymentResponse(order_id, amount, status="DONE", kind="ONE_OFF", ...)
```

---

## 7. 예외 · 엣지 케이스

| 상황 | 처리 | 위치 |
|---|---|---|
| `order_id` 형식 불량 | 422 InputValidationError | (A) |
| `amount` 0 이하 | 422 InputValidationError | (A) |
| 같은 서비스 + 같은 `order_id` 재시도 | 기존 Payment 반환(재결제 없음) | (B) |
| 다른 서비스 + 같은 `order_id` | 409 ConflictError | (B) |
| 동시 요청 → `order_id` 충돌(IntegrityError) | rollback → 멱등 재확인 → 기존 반환 or 409 | (C) |
| 빌링키 발급 실패 | FAILED 확정 + 4xx | (D) |
| 카드 거절 등 결제 실패 | FAILED 확정 + 빌링키 삭제 + 4xx | (E) |
| 토스 타임아웃(결과 불명) | PENDING 유지 + best-effort 빌링키 삭제 + 503 | (E) |
| 결제 직후 서버 다운 | PENDING 기록 보존 → 정산 스윕이 추후 확정 | (C) 선커밋 |
| 타임아웃 PENDING이 방치됨 | 10분 후 정산 스윕이 order_id 재조회로 확정 | 문서 07 |
| 빌링키 삭제 실패 | 경고 로그만 남기고 PENDING 유지(고아 키가 토스에 잔존 가능) | `safe_delete_billing_key` |

**결제 3원칙** (이 시스템 전체에 반복):
1. **PENDING 선커밋** — 토스 호출 전에 DB에 기록해 크래시·타임아웃에도 추적 가능.
2. **타임아웃 ≠ 실패** — 결과 불명으로 다뤄 이중결제/누락 방지.
3. **멱등 `order_id`** — 같은 주문 번호 재시도는 기존 결과 반환(재결제 없음).

---

## 7-2. 단건 결제 취소 — `cancel_one_off_payment`

> 마이그레이션 `d4e5f6a7b8c9` 신설. `app/services/payments.py`의 `cancel_one_off_payment`.

### 0) 개요

| 항목 | 내용 |
|---|---|
| 외부 API | `POST /api/v1/payments/{order_id}/cancel` (`payment_rate_limit`) |
| Admin | `POST /admin/payments/{payment_id}/cancel` (`require_any` + CSRF) |
| 샘플 | `shop/views.py` `oneoff_cancel_view` → `POST /pay/cancel` |
| 도메인 | `app/services/payments.py` `cancel_one_off_payment` |
| 테스트 | `tests/integration/test_payment_cancel.py` |

### 1) 취소 정책 — `Service.cancellation_enabled` / `cancellation_fee_percent`

- `cancellation_enabled` (Boolean, 기본 `True`) — 서비스별 단건 결제 취소 허용 여부.
  `False`이면 `cancel_one_off_payment`가 즉시 `PaymentFailedError(code="CANCEL_DISABLED")`를 발생시키고 취소가 거부된다.
- `cancellation_fee_percent` (Integer 0~100, 기본 0) — 취소 시 차감하는 수수료율(%).

### 2) 수수료 계산 및 부분취소

```python
fee    = payment.amount * service.cancellation_fee_percent // 100  # 정수 내림
refund = payment.amount - fee
```

- **수수료 0** (`fee_percent=0` 또는 계산 결과 0): `refund == payment.amount` → 전액취소.
  토스 `cancel_payment(key, reason, cancel_amount=None)` 호출(cancelAmount 생략).
- **수수료 있음**: `refund < payment.amount` → 부분취소.
  토스 `cancel_payment(key, reason, cancel_amount=refund)` 호출(환불액만 지정).

### 3) 상태 전이 및 기록 컬럼

취소 성공 시: `Payment.status: DONE → CANCELED`

| 컬럼 | 설명 |
|---|---|
| `canceled_amount` | 실제 환불액 = `refund` (수수료 공제 후) |
| `cancel_fee` | 차감 수수료 = `fee` |
| `canceled_at` | 취소 완료 시각(UTC, `utcnow()`) |

취소 실패(TossError) 시: `Payment.status`는 **DONE 유지** → 재시도 가능.

### 4) 거부 조건

| 조건 | 예외 |
|---|---|
| `order_id`가 없거나 타 서비스 결제 | `NotFoundError` |
| `kind != ONE_OFF` 또는 `status != DONE` | `ConflictError("취소할 수 없는 결제입니다")` |
| `service.cancellation_enabled == False` | `PaymentFailedError(code="CANCEL_DISABLED")` |

### 5) 행위자와 감사로그

`cancel_one_off_payment`는 `actor_user_id` 인자를 선택적으로 받는다:

- **외부 API 호출** (`actor_user_id=None`): `actor_type="SERVICE"`, `actor_service_id=service.id`
- **Admin 호출** (`actor_user_id=ctx.user.id`): `actor_type="USER"`, `actor_user_id=관리자 ID`

감사 액션:
- 성공: `payment.canceled` → `detail={"refund": ..., "fee": ...}`
- 실패(토스 오류): `payment.cancel_failed` → `detail={"code": ...}`
- 라벨(`audit_labels.py`): `"결제 취소"` / `"결제 취소 실패"`

### 6) 외부 API — `POST /api/v1/payments/{order_id}/cancel`

```python
# app/api/v1/payments.py
@router.post("/payments/{order_id}/cancel")
async def cancel_payment(order_id: str, payload: OneOffCancelRequest,
                         service: Service = Depends(payment_rate_limit), ...)
```

- 인증: `payment_rate_limit` (토스 취소 API 호출 수반 — 결제성 throttle 대상).
- 요청 본문 스키마 (`app/schemas/api.py` `OneOffCancelRequest`):
  ```python
  reason: str = Field(default="사용자 취소", max_length=200)
  ```
- 응답: `PaymentResponse`(status=CANCELED + canceled_amount/cancel_fee 포함).

### 7) Admin 취소 — `POST /admin/payments/{payment_id}/cancel`

```python
# app/admin/routes/payments.py
@router.post("/payments/{payment_id}/cancel")
async def payment_cancel(payment_id: uuid.UUID, ...)
```

1. CSRF 검증 (`validate_csrf`).
2. 결제 조회 + 서비스 스코프 확인.
3. `cancel_one_off_payment(actor_user_id=ctx.user.id)` 호출 — 관리자 행위자 기록.
4. 성공 시 `/admin/payments/{payment_id}` 303 리다이렉트.

결제 상세 화면(`payments/detail.html`)에서:
- `kind=ONE_OFF` + `status=DONE` + `service.cancellation_enabled=True`: 취소 버튼(확인 다이얼로그, 수수료 % 표시).
- `cancellation_enabled=False`: "취소 불가(서비스 정책)" 배지.
- `status=CANCELED`: 환불액·수수료 표시(`canceled_amount`, `cancel_fee`).

### 8) 샘플 서비스 — `sample_service/shop`

- `payment_client.cancel_one_off_payment(order_id)` → `POST /api/v1/payments/{order_id}/cancel`.
- `oneoff_cancel_view`: POST 처리 후 `/pay`로 리다이렉트. `PaymentAPIError(code="CANCEL_DISABLED")` 시 오류 메시지 표시.
- `result.html`의 `payment.status == 'DONE'` 영역에 취소 버튼.

---

## 8. 관련 테스트 — `tests/integration/test_one_off_payment.py`

| 테스트 함수 | 검증 내용 |
|---|---|
| `test_one_off_success_deletes_billing_key` | 성공 후 `status=DONE`, `kind=ONE_OFF`, `subscription_id=None`, 빌링키 삭제 확인 |
| `test_one_off_idempotent_same_order_id` | 동일 `order_id` 재시도 시 기존 Payment 반환, 토스 재결제 없음 |
| `test_one_off_other_service_order_id_conflicts` | 다른 서비스의 `order_id` 재사용 → 409 |
| `test_one_off_card_declined_failed` | 카드 거절(TossError) → `status=FAILED` |
| `test_one_off_timeout_pending` | TossTimeoutError → `status=PENDING` 유지 + 빌링키 삭제 호출 확인 |
| `test_reconcile_confirms_one_off` | 타임아웃 PENDING 후 정산 스윕 실행 → `status=DONE` 확정(단건도 스윕 처리됨) |

### 취소 테스트 — `tests/integration/test_payment_cancel.py`

| 테스트 함수 | 검증 내용 |
|---|---|
| `test_cancel_full_refund_no_fee` | 수수료 0%: `status=CANCELED`, `canceled_amount=amount`, `cancel_fee=0`, 전액취소(cancelAmount=None) |
| `test_cancel_partial_with_fee` | 수수료 10%: `cancel_fee=1000`, `canceled_amount=9000`, 부분취소(cancelAmount=9000) |
| `test_cancel_disabled` | `cancellation_enabled=False`: `PaymentFailedError(CANCEL_DISABLED)` |
| `test_cancel_rejects_non_done_or_other_service` | 타 서비스 결제 → `NotFoundError` |

---

## 9. 유지보수 체크리스트

1. **결제 3원칙 순서를 절대 바꾸지 말 것.** "PENDING 선커밋 → 토스 호출 → 결과 확정" 순서와
   "타임아웃=PENDING 유지"를 깨면 이중결제·유실 위험.
2. **단건에 빌링키를 보관하는 옵션을 추가할 경우**: `cipher`는 이미 인터페이스에 있으므로
   암호화 저장 로직만 추가하면 되지만, 삭제 시점(성공/실패 분기)을 명시적으로 재검토할 것.
3. **새 금액 검증 규칙**: 현재는 `amount > 0`만 검증한다. 상한(최대 결제 금액)이나 허용 통화를
   추가하려면 `services/payments.py` (A)에서 처리하고, 스키마의 `gt=0`도 함께 조정.
4. **정산 스윕 대상**: 단건 결제는 `subscription_id=NULL`이므로 `_reconcile_pending_payments`의
   `outerjoin`이 NULL 허용(`outerjoin`)으로 처리됨. 소유권 경계(`_DUE_STATUSES`) 체크에서
   `stuck_sub is None`이면 해당 없이 스윕 대상이 되므로 단건 PENDING은 항상 스윕이 확정한다.
5. **`safe_delete_billing_key` 실패**: 로그만 남기고 False 반환. 단건은 키를 DB에 저장하지 않으므로
   운영 도구로 토스에 직접 삭제 요청해야 한다. 고아 키가 토스에 잔존할 수 있음을 인지.
6. **`GET /payments/{external_user_id}`** (같은 파일 `payments.py:15`)는 구독 경유로만 조회한다
   (`join(Subscription, ...)`). 단건 결제는 여기서 조회되지 않는다. 단건 조회 API가 필요하면
   `Payment.service_id + external_user_id` 기반 조회를 별도로 추가해야 한다.
