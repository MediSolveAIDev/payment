# 04. 구독 생성

> 상호참조: 갱신·만료·재시도 → 05, 취소·재개·수동결제·카드변경 → 06, 인증 → 03, DB 테이블 → 02, 외부 API 레퍼런스 → 15

---

## 1. 한 줄 요약

외부 서비스가 **사전 등록된 카드**(카드 보관함, `POST /api/v1/cards`)를 기반으로 구독을 생성한다.  
서버가 `cards` 테이블에서 빌링키를 조회해 첫 결제를 처리하고 구독 레코드를 생성한다.  
**Task 7 변경**: `auth_key`·`customer_key`는 구독 생성 요청에서 제거됐다. 빌링키 발급은 카드 등록 시점(`POST /api/v1/cards`)에만 이루어진다.

---

## 2. 언제 실행되나 (트리거)

외부 서비스(사내 다른 앱)가 자신의 사용자에게 구독을 등록할 때 직접 호출한다.  
**호출 전제**: 외부 서비스가 먼저 `POST /api/v1/cards`로 카드를 등록해야 한다 — 카드 미등록 시 404 오류. 카드 등록은 토스 결제창을 통해 `authKey + customerKey`를 받아서 처리한다(`16-card-vault.md` 참조).

---

## 3. 요청 진입점

| 항목 | 내용 |
|------|------|
| HTTP | `POST /api/v1/subscriptions` |
| 라우터 파일 | `app/api/v1/subscriptions.py:41` |
| 핸들러 함수 | `create_subscription` (`app/api/v1/subscriptions.py:42`) |
| 인증 의존성 | `payment_rate_limit` (`app/api/deps.py:141`) |
| 응답 HTTP 상태 | `201 Created` |

### 요청 바디 스키마

`app/schemas/api.py:17` — `SubscriptionCreateRequest`

| 필드 | 타입 | 설명 |
|------|------|------|
| `external_user_id` | `str` (1–255자) | 외부 서비스 측 사용자 식별자. 이 값과 `service_id`로 중복 구독을 막는다. |
| `plan_id` | `uuid.UUID` | 구독할 요금제 ID. |
| `trial` | `bool` (기본 `False`) | `True`이면 체험 구독으로 시작. 요금제가 체험을 제공하지 않으면 422 오류. |

> **Task 7 변경**: `auth_key`·`customer_key` 필드가 제거됐다. 빌링키는 `cards` 테이블에 사전 등록된 카드에서 서버가 자동으로 가져온다. 구독 요청 전에 반드시 `POST /api/v1/cards`로 카드를 먼저 등록해야 한다.

> **보안 원칙**: 결제 금액 필드는 없다. 금액은 항상 서버가 요금제에서 계산한다(`app/schemas/api.py:6`). 클라이언트가 금액을 전달하면 조작 가능성이 생기므로 설계상 차단한다.

---

## 4. 단계별 처리 흐름

```
POST /api/v1/subscriptions
       │
       ▼
[의존성 체인] payment_rate_limit
  app/api/deps.py:141
       │
       ▼
[핸들러] create_subscription
  app/api/v1/subscriptions.py:42
       │
       ▼
[서비스] create_subscription
  app/services/subscriptions.py:126
```

### 4-1. 인증: payment_rate_limit

`app/api/deps.py:141`

`payment_rate_limit`은 `authenticate_service`를 내부에서 호출(`app/api/deps.py:143`)한 뒤, 결제 전용 처리율 제한을 추가로 적용한다.

`authenticate_service`(`app/api/deps.py:77`)가 수행하는 6단계:

1. 킬스위치 게이트 — `GlobalSettings.server_disabled=True`이면 503 즉시 차단 (`app/api/deps.py:86`)
2. 헤더 `x-service-key` SHA-256 해시로 `services` 테이블 조회 (`app/api/deps.py:95`)
3. IP 화이트리스트 검사 — `service.allowed_ips`에 없으면 403 (`app/api/deps.py:102`)
4. Redis 분당 요청 수 제한(일반) (`app/api/deps.py:106`)
5. 타임스탬프 윈도우 검사(재전송 방어 1차) (`app/api/deps.py:114`)
6. HMAC-SHA256 서명 검증 + nonce 1회용 소비(재전송 방어 2차) (`app/api/deps.py:126`)

이후 `payment_rate_limit`이 결제 전용 Redis 키로 분당 결제 요청 수를 별도 제한한다 (`app/api/deps.py:148`).

인증에 성공하면 `Service` 객체가 핸들러로 전달된다.

### 4-2. 핸들러 → 서비스 호출

`app/api/v1/subscriptions.py:56`

핸들러는 인증을 통과한 `service` 객체와 요청 바디 필드를 꺼내 `subscription_service.create_subscription`으로 넘긴다. 비즈니스 로직은 전부 서비스 레이어에 있다.

```python
# app/api/v1/subscriptions.py (Task 7 이후)
sub = await subscription_service.create_subscription(
    db, toss, cipher, service=service, plan_id=payload.plan_id,
    external_user_id=payload.external_user_id,
    trial=payload.trial)
# auth_key·customer_key는 더 이상 전달하지 않는다 — cards 테이블에서 서버가 조회
```

### 4-3. 서비스 레이어: create_subscription

`app/services/subscriptions.py`

#### 단계 A. 입력 검증

`app/services/subscriptions.py`

`_validate_external_user_id`가 검사한다(Task 7 이후):

- `external_user_id`: 빈 문자열, 공백만 있는 문자열, 255자 초과 → `InputValidationError` 422

> Task 7 변경: `customer_key` 검증이 제거됐다. `customer_key`는 카드 등록 시(`register_or_replace_card`) 이미 검증된다.

#### 단계 B. 요금제 조회 및 유효성 검사

`app/services/subscriptions.py:160-162`

`plans` 테이블에서 `plan_id`로 조회한다. 아래 조건 중 하나라도 해당하면 `NotFoundError` 404:

- `plan`이 없음
- `plan.service_id != service.id` (다른 서비스의 요금제)
- `plan.status != ACTIVE` (보관됨 — ARCHIVED이면 신규 구독 불가)

#### 단계 C. 체험 가능 여부 확인

`app/services/subscriptions.py:164-165`

`trial=True`인데 `plan.trial_enabled=False`이거나 `plan.trial_days`가 1 미만이면 `InputValidationError` 422.

#### 단계 D. 기존 '열린' 구독 중복 확인

`app/services/subscriptions.py:167-169`

`get_open_subscription`(`app/services/subscriptions.py:62`)이 `service_id + external_user_id`로 EXPIRED를 제외한 상태(`TRIAL, ACTIVE, PAST_DUE, SUSPENDED, CANCELED`)의 구독을 조회한다.

이미 있으면 `ConflictError` 409. 이 시점에는 빌링키 발급 전이므로 발급 비용이 없다.

#### 단계 E. 첫구독 여부 판정

`app/services/subscriptions.py:171-173`

`_is_first_subscription`(`app/services/subscriptions.py:90`)이 아래 기준으로 판정한다:

- **첫구독** = 과거에 혜택을 소진한 구독이 없을 때
- **혜택을 소진한 구독** = (a) DONE 결제가 있는 구독, 또는 (b) 결제 시도가 아예 없는 구독(0원 무료 첫구독)
- 신규 가입 첫 결제 실패는 구독·결제 행을 남기지 않으므로(감사로그만) 애초에 조회되지 않는다 → 재시도 시 첫구독 혜택 유지

#### 단계 F. 결제 금액 결정

`app/services/subscriptions.py:175-176`

```
trial=True         → amount = 0            (체험 기간 중 결제 없음)
비체험 + 첫구독    → amount = plan_first_amount(plan)      (정가 + 첫구독 할인)
비체험 + 재구독    → amount = plan_recurring_amount(plan)  (상시 할인가)
```

`plan_first_amount`(`app/services/billing_math.py:106`): `plan.price`에 `first_payment_type/first_payment_value`만 적용한다. 상시 할인과 무관하다(요청 005 규칙).

`plan_recurring_amount`(`app/services/billing_math.py:100`): `plan.price`에 `recurring_discount_type/recurring_discount_value`를 적용한다.

**금액 계산 상세** (`app/services/billing_math.py`):

| first_payment_type | 첫 결제 금액 |
|---|---|
| `NONE` | `plan.price` (정가) |
| `FREE` | 0원 |
| `DISCOUNT_AMOUNT` | `max(0, plan.price - first_payment_value)` |
| `DISCOUNT_PERCENT` | `plan.price - (plan.price * value) // 100` |

| recurring_discount_type | 정기 결제 금액 |
|---|---|
| `NONE` | `plan.price` (정가) |
| `DISCOUNT_AMOUNT` | `max(0, plan.price - discount_value)` |
| `DISCOUNT_PERCENT` | `plan.price - (plan.price * value) // 100` |

#### 단계 G. 등록 카드 조회

`app/services/subscriptions.py` (Task 7 신규 단계)

구독 생성 전, `cards` 테이블에서 사용자의 등록 카드를 조회한다.

```python
card = await get_card(db, service_id=service.id, external_user_id=external_user_id)
if card is None:
    raise NotFoundError("등록된 카드가 없습니다. 먼저 카드를 등록하세요")
```

- 카드 미등록 → `NotFoundError` 404 즉시 반환. 이 시점엔 DB에 아무것도 쓰지 않는다.
- 카드가 있으면 `card.billing_key_encrypted`와 `card.customer_key`를 이후 결제에 사용한다.
- **Task 7 이전과의 차이**: 이전에는 `toss.issue_billing_key(auth_key, customer_key)`를 호출해 빌링키를 새로 발급했으나, 이제는 기존에 등록된 카드의 빌링키를 재사용한다.

#### 단계 H. Subscription 레코드 생성 + flush

`app/services/subscriptions.py`

```python
sub = Subscription(
    service_id=service.id,
    plan_id=plan.id,
    external_user_id=external_user_id,
    card_id=card.id,     # cards 테이블 FK — 빌링키는 cards에서 조회
    status=status,       # TRIAL 또는 ACTIVE
    current_period_start=now,
    current_period_end=period_end,
    next_billing_at=period_end,
)
```

> **Task 7 변경**: `customer_key`, `billing_key_encrypted`, `billing_key_hash`, `card_info` 컬럼이 구독 생성자에서 제거됐다(Task 2/3에서 subscriptions 테이블에서 이미 제거). 대신 `card_id` FK로 `cards` 테이블을 참조한다.

**기간 계산**:
- 체험: `period_end = now + timedelta(days=plan.trial_days)` (`app/services/subscriptions.py:186`)
- 비체험: `period_end = compute_period_end(now, plan.billing_cycle, plan.cycle_days)` (`app/services/billing_math.py:39`)
  - YEAR → `relativedelta(years=1)`, MONTH → `relativedelta(months=1)`, WEEK → `timedelta(weeks=1)`, DAY → `timedelta(days=cycle_days)`

**auto_renew=False 처리** (`app/services/subscriptions.py:203-204`):
- 체험이 아니고 `plan.auto_renew=False`이면 `next_billing_at=None`으로 설정. 기간 종료 시 배치가 EXPIRED로 처리하며 자동 갱신은 없다.
- 체험인 경우는 `next_billing_at`을 유지한다(체험 만료 시 첫 결제가 예약돼야 하므로).

`db.flush()` — commit하지 않고 DB에 SQL을 보내 **부분 유니크 인덱스**를 검사한다:

```sql
-- app/models/subscription.py:48-54 (Index "uq_subscriptions_one_per_user")
CREATE UNIQUE INDEX uq_subscriptions_one_per_user
  ON subscriptions (service_id, external_user_id)
  WHERE status IN ('TRIAL','ACTIVE','PAST_DUE','SUSPENDED','CANCELED');
```

동시에 같은 `service_id + external_user_id`로 두 요청이 들어왔을 때 단계 D의 SELECT가 둘 다 '없음'을 반환하더라도, flush 단계에서 DB 인덱스가 최종 중재한다. 패자는 `IntegrityError` → 롤백 후 `ConflictError` 409를 반환한다. **Task 7 변경**: 이전에는 패자의 빌링키를 `safe_delete_billing_key`로 삭제했으나, 이제는 카드가 영속적(구독과 독립)이므로 삭제하지 않는다.

#### 단계 I. PENDING 결제 레코드 생성 + 1차 커밋

`app/services/subscriptions.py:214-231`

`amount > 0`인 경우(비체험 + 결제 금액 있음) PENDING 상태 결제 레코드를 DB에 추가한다:

```python
payment = Payment(
    subscription_id=sub.id,
    order_id=new_order_id("f"),          # "f" + uuid4().hex (FIRST 결제 식별용 접두사)
    amount=amount,
    payment_type=PaymentType.FIRST,
    status=PaymentStatus.PENDING,
    idempotency_key=f"first-{sub.id}",   # 멱등성 키: sub.id로 고정 → 재시도해도 같은 키
    kind=PaymentKind.SUBSCRIPTION,
    service_id=service.id,
    external_user_id=external_user_id,
    requested_at=now,
)
```

감사 로그 기록 (`app/services/subscriptions.py:224`):

```python
await record_audit(db, actor_type="SERVICE", actor_service_id=service.id,
                   action="subscription.create",
                   detail={"external_user_id": ..., "plan_id": ..., "amount": ...,
                           "is_first": ..., "trial": ...})
```

`record_audit`(`app/services/audit.py:15`)은 `db.add`만 호출하고 commit하지 않는다. 이후 commit에서 구독+결제+감사 로그가 원자적으로 저장된다.

**1차 커밋** (`app/services/subscriptions.py:231`):

```python
await db.commit()   # 1차: PENDING 결제 + 구독 슬롯을 DB에 내구성 있게 선점
```

이 커밋이 끝나면 구독 슬롯이 DB에 확실히 잡힌다. 이후 토스 API 호출 중에 서버가 죽더라도 구독과 PENDING 결제가 남아 있어 배치 정산(`05` 참조)이 처리할 수 있다.

**commit이 2회인 이유**:  
결제 전 1차 commit으로 슬롯을 내구성 있게 점유하고, 결제 결과 확정 후 2차 commit으로 최종 상태를 기록한다.  
1차 commit 없이 결제 → DB 저장을 단번에 하면, 결제 승인 후 DB 장애 시 "과금은 됐는데 구독은 없는" 상황이 발생할 수 있다.

#### 단계 J. 토스 결제 실행 (amount > 0인 경우)

`app/services/subscriptions.py`

`resolve_charge`(`app/services/payment_utils.py:38`)가 실제 청구를 담당한다.  
**Task 7 변경**: 빌링키·customerKey를 카드에서 복호화해 전달한다.

```python
# cards 테이블에서 빌링키·customerKey 추출
billing_key = cipher.decrypt(card.billing_key_encrypted)
customer_key = card.customer_key

result = await resolve_charge(
    toss, billing_key=billing_key, customer_key=customer_key,
    amount=amount, order_id=payment.order_id, order_name=plan.name,
    idempotency_key=payment.idempotency_key)
```

`resolve_charge` 내부 (`app/services/payment_utils.py:38`):
1. `toss.charge(...)` 호출 (`app/toss/client.py:109`) → `POST /v1/billing/{billingKey}`
2. 정상 응답 → `ChargeResult` 반환
3. `TossTimeoutError` 발생 → `toss.get_payment_by_order_id(order_id)` 재조회
   - 재조회 결과 `DONE` → `ChargeResult` 반환 (타임아웃이었지만 실제론 성공)
   - 재조회 결과 없음 또는 실패 → `TossTimeoutError` 재발생

`HttpTossClient`의 read timeout은 65초(`app/toss/client.py:56`). 토스 자동결제 명세상 최대 60초를 허용하기 때문이다.

결과에 따른 처리 (`app/services/subscriptions.py:239-273`):

**[경우 1] TossTimeoutError** → 결과 불명 (이중결제 방지 원칙):

```python
# app/services/subscriptions.py:239-250
# 절대 '실패 확정' 처리 금지 — 결제됐을 수 있음
# payment.status = PENDING 그대로 유지
# sub.status = ACTIVE 그대로 유지 (슬롯 점유)
# billing_key 삭제 금지
await record_audit(..., action="subscription.first_payment_unresolved", ...)
await db.commit()
raise PaymentFailedError(..., code="PAYMENT_UNRESOLVED", http_status=503)
```

외부 서비스는 503을 받고, 이후 구독 조회(`GET /api/v1/subscriptions/{id}`)로 상태를 확인해야 한다. PENDING 결제는 배치 정산(05)이 추후 처리한다.

**[경우 2] TossError** → 결제 확정 실패 (카드 거절 등):

```python
# app/services/subscriptions.py (TossError 분기, Task 7 이후)
# 카드는 영속적 — 빌링키 삭제 없음(구독 실패와 카드는 독립)
# 신규 가입 실패는 구독·결제 테이블에 흔적을 남기지 않는다(요청):
# 1차 커밋으로 선점했던 행을 삭제하고 감사로그만 남긴다(결제 → 구독 순, RESTRICT FK).
await db.delete(payment)
await db.delete(sub)
await record_audit(db, action="subscription.first_payment_failed", ...,
                   detail={"code": exc.code, "billing_key_deleted": False,
                           "card_id": str(card.id),   # 카드 추적용 ID
                           "persisted": False})
await db.commit()                          # 2차 커밋(삭제 확정 + 감사 기록)
raise PaymentFailedError(...)
```

> **Task 7 변경**: 이전에는 `safe_delete_billing_key`로 빌링키를 삭제했으나, 카드는 구독과 독립적으로 영속되므로 더 이상 삭제하지 않는다. 실패한 구독 생성 이후에도 같은 카드로 재시도가 가능하다.

**[경우 3] 성공 (ChargeResult 반환)**:

```python
# app/services/subscriptions.py:269-273
payment.status = PaymentStatus.DONE
payment.toss_payment_key = result.payment_key
payment.approved_at = utcnow()
payment.raw_response = result.raw
await db.commit()   # 2차 커밋
```

#### 단계 K. 응답 반환

`app/api/v1/subscriptions.py:61`

`_to_response`가 `Subscription + Plan`을 `SubscriptionResponse`로 변환한다.  
`access_allowed`는 `app/models/enums.py:89`의 `access_allowed(status)`가 판정한다.

---

## 5. DB 테이블·컬럼

### 쓰기

#### subscriptions (`app/models/subscription.py`)

| 컬럼 | 값 / 비고 |
|------|-----------|
| `id` | UUID v4 자동 생성 |
| `service_id` | 인증된 서비스 ID |
| `plan_id` | 요청된 요금제 ID |
| `external_user_id` | 요청 바디의 `external_user_id` |
| `card_id` | `cards.id` FK — 등록 카드 참조 (nullable) |
| `status` | TRIAL 또는 ACTIVE (초기값) |
| `current_period_start` | 생성 시각(UTC) |
| `current_period_end` | billing_cycle 기반 계산 종료일 |
| `next_billing_at` | 다음 자동결제 예정 시각 (auto_renew=False면 NULL) |
| `retry_count` | 초기값 0 |

> **Task 7 변경**: `customer_key`, `billing_key_encrypted`, `billing_key_hash`, `card_info` 컬럼이 `subscriptions` 테이블에서 제거됐다(Task 2/3). 빌링키·카드 정보는 `cards` 테이블에서 관리한다.

#### payments (`app/models/payment.py:19`)

| 컬럼 | 값 / 비고 |
|------|-----------|
| `subscription_id` | 위 구독 ID |
| `service_id` | 인증된 서비스 ID |
| `external_user_id` | 사용자 식별자 |
| `order_id` | `"f" + uuid4().hex` (전체 고유) |
| `amount` | 서버가 계산한 결제 금액 |
| `payment_type` | `FIRST` |
| `kind` | `SUBSCRIPTION` |
| `status` | PENDING → DONE / FAILED (2차 커밋에서 확정) |
| `idempotency_key` | `"first-{sub.id}"` (고정 — 재시도 멱등성) |
| `toss_payment_key` | DONE 시 토스 paymentKey |
| `approved_at` | DONE 시 승인 시각 |
| `raw_response` | 토스 원본 응답 JSONB |
| `failure_code` / `failure_message` | FAILED 시만 채워짐 |

> `amount=0` (무료 첫구독·체험) 이면 Payment 레코드가 생성되지 않는다 (`app/services/subscriptions.py:215`).

#### audit_logs (`app/models/audit_log.py:17`)

| 필드 | 값 |
|------|----|
| `actor_type` | `"SERVICE"` |
| `actor_service_id` | 인증된 서비스 ID |
| `action` | `"subscription.create"` (1차 커밋) / `"subscription.first_payment_unresolved"` / `"subscription.first_payment_failed"` (2차 커밋) |
| `target_type` | `"subscription"` |
| `target_id` | `sub.id` (문자열) |
| `detail` | `{external_user_id, plan_id, amount, is_first, trial}` |

### 읽기

| 테이블 | 목적 |
|--------|------|
| `services` | 인증 단계에서 API 키 해시로 서비스 조회 |
| `plans` | 요금제 유효성 확인, 금액 계산, 기간 계산 |
| `subscriptions` | `get_open_subscription` — 중복 구독 확인 |
| `payments` | `_is_first_subscription` — 첫구독 여부 판정 |
| `cards` | `get_card` — 등록 카드 조회 (빌링키·customerKey 획득) *(Task 7 추가)* |

---

## 6. 상태 전이표

구독 생성 후 도달 가능한 초기 상태:

| 경우 | 구독 최종 상태 | 결제 상태 | 설명 |
|------|--------------|-----------|------|
| `trial=True` + 성공 | `TRIAL` | 없음 | 체험 기간 중 결제 없음. 만료 시 배치가 첫 결제 실행 |
| `amount=0` (무료 첫구독) | `ACTIVE` | 없음 | 완전 무료. 다음 갱신 시 상시 할인가 청구 |
| 첫 결제 성공 | `ACTIVE` | `DONE` | 정상 구독 시작 |
| 첫 결제 실패 | (행 삭제·미저장) | (행 삭제·미저장) | 구독·결제 행을 남기지 않고 감사로그만. 재구독 가능(첫구독 혜택 유지) |
| 타임아웃 (결과 불명) | `ACTIVE` | `PENDING` | 슬롯 점유 유지. 배치 정산이 나중에 처리 |

전체 상태 머신 (`app/models/enums.py:67`):

```
(없음) ──────────────────────────────────────────► EXPIRED
(없음) ──trial=True──────────────────────────────► TRIAL
(없음) ──amount=0 또는 결제 성공──────────────────► ACTIVE
(없음) ──결제 실패────────────────────────────────► (행 삭제·미저장 — 감사로그만)

TRIAL ──만료 시 첫 결제 성공─────────────────────► ACTIVE  (05 갱신)
TRIAL ──취소──────────────────────────────────────► CANCELED → EXPIRED

ACTIVE ──갱신 결제 실패 반복──────────────────────► PAST_DUE (05)
ACTIVE ──취소──────────────────────────────────────► CANCELED
ACTIVE ──기간 만료──────────────────────────────────► EXPIRED (05)

PAST_DUE ──재시도 한도 초과────────────────────────► SUSPENDED (05)
SUSPENDED ──수동 결제 성공────────────────────────► ACTIVE  (06)
CANCELED ──만료일 도달────────────────────────────► EXPIRED (05)
```

---

## 7. 예외·엣지 케이스

| 상황 | 발생 위치 | HTTP | 코드 |
|------|-----------|------|------|
| 이미 구독 존재 (사전 검사) | `subscriptions.py` | 409 | `CONFLICT` |
| 동시 요청 경쟁 (DB 유니크 인덱스) | `subscriptions.py` | 409 | `CONFLICT` |
| 요금제 없음 / 비활성 / 타 서비스 | `subscriptions.py` | 404 | `NOT_FOUND` |
| 체험 불가 요금제에 trial=True | `subscriptions.py` | 422 | `VALIDATION_ERROR` |
| **등록된 카드 없음** *(Task 7 신규)* | `subscriptions.py` | 404 | `NOT_FOUND` |
| 첫 결제 실패 (카드 거절 등) | `subscriptions.py` | 402 | `PAYMENT_FAILED` |
| 타임아웃 (결과 불명) | `subscriptions.py` | 503 | `PAYMENT_UNRESOLVED` |
| 결제 처리율 초과 | `deps.py` | 429 | `RATE_LIMITED` |
| 킬스위치 활성화 | `deps.py` | 503 | `SERVER_DISABLED` |

### 중복 구독 방지 상세

`get_open_subscription`(SELECT)과 DB 유니크 인덱스(flush)가 이중으로 방어한다.

- **1차**: 단계 D에서 SELECT로 빠르게 확인. 일반적인 경우 여기서 차단되고 빌링키 발급 비용이 발생하지 않는다.
- **2차**: 동시 요청 경쟁 시 SELECT가 둘 다 '없음'을 반환하더라도 flush 단계에서 DB 부분 유니크 인덱스가 최종 중재한다. 패자는 `IntegrityError` → 발급된 빌링키를 즉시 삭제하고 409 반환.

### 타임아웃(결과 불명) 처리 원칙

토스 자동결제는 최대 60초가 걸릴 수 있다. 타임아웃이 발생했다고 결제 실패가 확정된 것이 아니다. 토스 서버에서 이미 청구가 됐을 수 있다. 따라서:

1. `payment.status = PENDING` 유지 (절대 FAILED 처리 금지)
2. `sub.status = ACTIVE` 유지 (슬롯 점유 — 재시도로 이중 결제 차단)
3. `billing_key_encrypted` 삭제 금지 (결제됐을 경우 갱신에 필요)
4. 외부 서비스가 503을 받고 재시도해도 단계 D에서 409 ConflictError로 차단

PENDING 결제는 갱신 배치의 정산 스윕(05 참조)이 추후 확정한다.

### 멱등 order_id

`idempotency_key = "first-{sub.id}"` — 구독 ID로 고정된다. 토스가 같은 멱등키로 재시도를 받으면 첫 응답을 그대로 반환해 이중 결제를 방지한다(`app/toss/client.py:110`).

---

## 8. 관련 테스트

| 테스트 파일 | 테스트 함수 | 검증 내용 |
|-------------|-------------|-----------|
| `test_subscription_create.py` | `test_create_with_full_price` | 카드 등록 후 정상 구독 생성, ACTIVE, payment DONE |
| `test_subscription_create.py` | `test_first_subscription_free_skips_charge` | 무료 첫구독 — 결제 없이 ACTIVE |
| `test_subscription_create.py` | `test_free_benefit_not_repeatable` | 무료 혜택 재사용 불가 |
| `test_subscription_create.py` | `test_first_subscription_discount_amount` | 첫구독 정액 할인 |
| `test_subscription_create.py` | `test_resubscribe_after_expiry_pays_full_price` | DONE 이력 있으면 재구독 정가 |
| `test_subscription_create.py` | `test_duplicate_subscription_conflicts` | 중복 구독 → 409, 추가 빌링키 발급 없음 |
| `test_subscription_create.py` | **`test_no_registered_card_raises_not_found`** | 카드 미등록 → NotFoundError 404 *(Task 7 신규)* |
| `test_subscription_create.py` | `test_concurrent_create_only_one_wins` | 동시 요청 경쟁 — 1개만 성공 |
| `test_subscription_create.py` | `test_first_charge_failure_not_persisted_keeps_benefit` | 첫 결제 실패 → 구독·결제 미저장(감사로그만), **카드 보존**, 재시도 시 혜택 유지 |
| `test_subscription_create.py` | `test_charge_failure_card_preserved` | 결제 실패 시 카드/빌링키 보존, 감사로그에 card_id 기록 *(Task 7 신규)* |
| `test_subscription_create.py` | `test_timeout_with_actual_approval_resolves_done` | 타임아웃 후 재조회 성공 → DONE |
| `test_subscription_create.py` | `test_timeout_without_approval_stays_unresolved` | 결과 불명 → PENDING 유지 + 재시도 차단 |
| `test_subscription_create.py` | `test_timeout_then_lookup_error_stays_unresolved` | 재조회 실패해도 PENDING 유지(FAILED 붕괴 없음) |
| `test_subscription_create.py` | `test_subscription_no_auto_renew_sets_no_next_billing` | auto_renew=False → next_billing_at=None |
| `test_subscription_create.py` | `test_trial_with_no_auto_renew_keeps_first_charge_schedule` | 체험+auto_renew=False → next_billing_at 유지 |
| `tests/unit/test_billing_math.py` | (전체) | 금액 계산 단위 테스트 |

---

## 9. 유지보수 팁

### 금액 계산 로직을 바꾸려면

`app/services/billing_math.py`의 `compute_first_amount`(54번째줄 근처) 또는 `compute_recurring_amount`(77번째줄 근처)를 수정한다.  
수정 후 `tests/unit/test_billing_math.py`를 반드시 실행해 엣지 케이스(0원 클램프, 음수 방지)가 깨지지 않는지 확인한다.

### 첫구독 판정 기준을 바꾸려면

`app/services/subscriptions.py:90`의 `_is_first_subscription`을 수정한다.  
현재 기준: DONE 결제 이력 있거나 결제 시도 자체가 없는(0원 무료) 구독을 소진된 것으로 본다. 이를 바꾸면 `test_free_benefit_not_repeatable`, `test_first_charge_failure_not_persisted_keeps_benefit` 테스트가 실패할 수 있으니 함께 수정한다.

### 상태 전이를 바꾸려면

`app/services/subscriptions.py:184-273`의 `create_subscription` 함수 내 상태 할당 부분을 수정한다.  
상태 문자열은 `app/models/enums.py:67`의 `SubscriptionStatus`에 정의돼 있다. 새 상태를 추가하면 `OPEN_SUBSCRIPTION_STATUSES`(`app/models/enums.py:82`)와 `ACCESS_ALLOWED_STATUSES`(`app/models/enums.py:77`)도 업데이트해야 DB 인덱스와 `access_allowed` 응답 필드가 올바르게 동작한다.

### 토스 결제 호출 흐름을 바꾸려면

`app/services/payment_utils.py:38`의 `resolve_charge`가 결제 실행 + 타임아웃 재조회를 담당한다. 실제 HTTP 요청은 `app/toss/client.py:109`의 `HttpTossClient.charge`에 있다.

### 흔한 디버깅

**PENDING에 멈춘 결제 (결과 불명 상태)**  
`payments` 테이블에서 `status='PENDING'`인 레코드를 찾아 `order_id`를 확인한다. 토스 어드민 콘솔이나 `GET /v1/payments/orders/{order_id}`로 실제 승인 여부를 확인한다.
- 실제로 DONE이면: `payment.status = DONE`, `payment.toss_payment_key` 업데이트, `payment.approved_at` 기록.
- 실제로 미처리이면: 구독을 EXPIRED 처리하고 사용자에게 재구독을 안내한다.

배치 정산이 자동으로 처리하도록 설계돼 있으므로 일반적으로 수동 개입은 불필요하다. 자세한 정산 흐름은 **05-subscription-renewal.md** 참조.

**신규 가입 첫 결제가 실패한 경우 (구독/결제 행이 없음)**  
신규 가입 첫 결제 실패는 구독·결제 테이블에 행을 남기지 않고 감사로그(`action='subscription.first_payment_failed'`)에만 기록한다. 사용자는 재구독으로 재시도할 수 있으며, DONE 결제 이력이 없으므로 첫구독 혜택이 유지된다. 실패 이력 추적은 `audit_logs`에서 확인한다.

**빌링키가 토스에 남아 있는데 DB에 없는 경우 (고아 키)**  
`safe_delete_billing_key` 호출이 실패하면 토스에 키가 잔존할 수 있다. 신규 가입 실패는 구독 행을 남기지 않으므로, `audit_logs` 테이블에서 `action='subscription.first_payment_failed'`와 `detail.billing_key_deleted=false`인 레코드를 찾아 `detail.billing_key_hash`로 키를 식별한 뒤 토스 어드민 콘솔에서 수동 삭제한다.
