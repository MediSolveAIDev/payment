# 13. 구독 기능

> 함께 보기: [카드 보관함 기능](12-feature-card.md)

이 문서는 구독 기능을 **호출 진입(라우트/스케줄러)부터 반환까지** 코드 흐름으로 따라갑니다. 생성(첫 결제/체험)·자동연장(스케줄러)·상태 전이·취소/재개/연장/수동결제·강제취소를 다룹니다.

> 쉽게 말하면 구독은 "요금제에 가입한 한 사용자의 상태(TRIAL→ACTIVE→…)를 관리하면서, 만료일이 되면 보관함 카드로 자동결제해 기간을 연장하는 것"입니다.

---

## 1. 기능 개요·관련 파일·DB 테이블

### 1-1. 핵심 규칙

- 서비스+사용자당 **EXPIRED를 제외한 '열린' 구독은 최대 1개**(부분 유니크 인덱스로 DB 강제).
- 빌링키는 구독이 직접 보유하지 않고 **`cards` 테이블(카드 보관함)에서 조회**합니다. 구독은 `card_id` FK만 갖습니다.
- 취소는 즉시 종료가 아니라 **CANCELED**로 전환 후 만료일에 배치가 **EXPIRED**로 종료합니다.
- 자동결제 실패는 **PAST_DUE(재시도) → SUSPENDED(정지) → EXPIRED**로 이어집니다.

### 1-2. 관련 파일

| 파일 | 역할 |
|------|------|
| `app/api/v1/subscriptions.py` | 외부 API 라우터 — 생성·조회·취소·재개·수동결제·사용일추가 |
| `app/services/subscriptions.py` | 생성·취소·재개·수동결제·강제취소·연장·사용일추가 |
| `app/services/renewals.py` | 정기 갱신 배치(`process_due`) — 자동연장·만료·재시도 |
| `app/services/transitions.py` | 상태 전이 중앙화(`transition` + 허용 전이 테이블) |
| `app/services/billing_math.py` | 결제 금액·주기 계산(`plan_first_amount` 등) |
| `app/services/cards.py` | `get_card` — 빌링키 조회 |
| `app/models/subscription.py` | `Subscription` 모델 |
| `app/models/enums.py` | `SubscriptionStatus` 등 열거형·상태 집합 |

### 1-3. DB 테이블 — `subscriptions` (`app/models/subscription.py:19`)

| 컬럼 | 설명 |
|------|------|
| `service_id` / `plan_id` | 소속 서비스·가입 요금제(둘 다 FK RESTRICT) |
| `external_user_id` | 외부 서비스 사용자 식별자 |
| `card_id` | 결제에 쓸 등록 카드(cards 참조, nullable) |
| `status` | 상태 머신 현재 위치 |
| `current_period_start`/`current_period_end` | 현재 주기 시작/종료(=접근 만료) |
| `next_billing_at` | 다음 자동결제 예정 시각(스케줄러가 이 값으로 조회) |
| `retry_count` | PAST_DUE에서 재시도 누적 횟수 |
| `suspended_at` | SUSPENDED 진입 시각(유예 만료 판정 기준) |

부분 유니크 인덱스 `uq_subscriptions_one_per_user`가 EXPIRED를 제외한 상태에 대해 서비스+사용자당 1건을 강제합니다(`app/models/subscription.py:48`).

### 1-4. 상태 열거형 (`app/models/enums.py:67`)

| 상태 | 의미 |
|------|------|
| `TRIAL` | 체험 — 만료 시 첫 정기 결제 |
| `ACTIVE` | 정상 이용 |
| `PAST_DUE` | 결제 실패/유예(접근 유지) |
| `SUSPENDED` | 강제 정지(접근 차단) — 수동 결제 대기 |
| `CANCELED` | 해지 예약(만료일까지 유지) |
| `EXTENDED` | 운영자 만료일 연장 — 이용 허용·새 만료일에 자동결제 |
| `EXPIRED` | 완전 종료(종단) |

---

## 2. 주요 흐름별 단계 추적

### 2-1. 구독 생성 — `POST /api/v1/subscriptions`

**1) 라우터** (`app/api/v1/subscriptions.py:70` `create_subscription`) — 첫 결제(토스 호출)를 수반하므로 `payment_rate_limit`.

```python
sub = await subscription_service.create_subscription(
    db, toss, cipher, service=service, plan_id=payload.plan_id,
    external_user_id=payload.external_user_id,
    trial=payload.trial, notifier=notifier)
return await _to_response(db, sub)
```

`_to_response`(`subscriptions.py:44`)는 구독 + 연결 Plan + `cards` 테이블의 마스킹 카드 정보를 묶어 응답합니다.

**2) 서비스 함수** (`app/services/subscriptions.py:155` `create_subscription`) 단계 추적:

| # | 단계 | 코드 위치 | DB/외부 |
|---|------|-----------|---------|
| 1 | `external_user_id` 검증 | `subscriptions.py:191` | — |
| 2 | 요금제 유효성(ACTIVE·소속) | `subscriptions.py:193` | `db.get(Plan)` |
| 3 | 체험 가능 여부 | `subscriptions.py:197` | — |
| 4 | 중복 구독(열린 슬롯) 확인 | `subscriptions.py:200` | `get_open_subscription` |
| 5 | **등록 카드 조회** — 없으면 `NotFoundError` | `subscriptions.py:206` | `get_card` |
| 6 | 비활성 카드면 `ConflictError` | `subscriptions.py:210` | — |
| 7 | 첫구독 판정 → 결제 금액 결정 | `subscriptions.py:213-218` | `_is_first_subscription` |
| 8 | `Subscription` 생성 + `flush` | `subscriptions.py:236-255` | INSERT(유니크 경쟁→ConflictError) |
| 9 | (금액>0이면) PENDING 결제행 생성 + 감사 + **1차 commit** | `subscriptions.py:258-276` | INSERT + COMMIT |
| 10 | (금액>0이면) 빌링키 복호화 → 결제 실행 | `subscriptions.py:278-323` | `resolve_charge` |
| 11 | 서비스 알림 + 반환 | `subscriptions.py:326-329` | `notifier.send` |

금액 결정 로직(`subscriptions.py:217`):

```python
amount = 0 if trial else (
    plan_first_amount(plan) if is_first else plan_recurring_amount(plan))
```

- **체험(`trial=True`)**: amount=0 → 결제 없이 TRIAL 시작(만료 시 상시 할인가로 첫 자동결제)
- **비체험 첫구독**: `plan_first_amount`(정가 + 첫구독 할인/무료)
- **재구독**: `plan_recurring_amount`(상시 할인가)

> 중요: commit이 **2회**입니다(`subscriptions.py:276`, `:323`). 결제 전 1차 commit으로 슬롯과 PENDING 결제행을 내구성 있게 선점하고, 결제 결과 확정 후 2차 commit으로 최종 상태를 기록합니다. 1차 commit 없이 결제하면 결제 성공 직후 DB 장애 시 "과금만 되고 구독이 없는" 상태가 됩니다.

첫 결제 결과별 처리(`subscriptions.py:282-323`):

```python
try:
    result = await resolve_charge(toss, billing_key=billing_key, customer_key=customer_key,
                                  amount=amount, order_id=payment.order_id, ...)
except TossTimeoutError as exc:
    # 결과 불명 — 절대 실패 확정 안 함. PENDING 유지, 503 반환(배치 정산이 추후 확정)
    await record_audit(..., action="subscription.first_payment_unresolved", ...)
    await db.commit()
    raise PaymentFailedError(PENDING_GRACE_MESSAGE, code="PAYMENT_UNRESOLVED", http_status=503)
except TossError as exc:
    # 확정 실패(카드 거절 등) — 구독·결제 행을 삭제(미저장). 감사로그만. 카드는 보존.
    await db.delete(payment); await db.delete(sub)
    await record_audit(..., action="subscription.first_payment_failed", ...)
    await db.commit()
    raise PaymentFailedError(f"첫 결제 실패: {exc.message}", code=exc.code)
payment.status = PaymentStatus.DONE; ...; await db.commit()
```

상태 전이 결과: 체험 → **TRIAL**, 비체험 성공 → **ACTIVE**, 첫 결제 실패 → 구독·결제 행 삭제(흔적 없음, 감사로그만), 타임아웃 → ACTIVE(결제 PENDING — 배치 정산 대기).

> 참고: 첫 결제 실패가 구독·결제 행을 남기지 않으므로(`subscriptions.py:116` `_is_first_subscription` 판정 대상에 안 잡힘), 재시도해도 첫구독 혜택이 유지됩니다.

### 2-2. 자동연장(스케줄러) — `process_due`

**진입점** (`app/services/renewals.py:132` `process_due`). 스케줄러/관리 명령이 배치 1회를 실행합니다.

**1) due 대상 조회**(읽기 전용, 락 없음) — 4개 카테고리를 due 시각 오름차순 + `BATCH_LIMIT`까지 수집(`renewals.py:165-186`):

| 카테고리 | 조건 | 처리 함수 |
|----------|------|-----------|
| `canceled_due` | CANCELED + 기간 만료 | `_expire_canceled` → EXPIRED |
| `suspended_due` | SUSPENDED + 유예일 초과 | `_expire_suspended` → EXPIRED |
| `renew_due` | TRIAL/ACTIVE/PAST_DUE(=`DUE_STATUSES`) + `next_billing_at` 도래 | `_renew_one` |
| `non_renewing_due` | ACTIVE + `next_billing_at` NULL + 기간 만료 | `_expire_non_renewing` → EXPIRED |

재시도 한계·간격·유예는 `GlobalSettings`(DB)에서 매 배치 로드합니다(`renewals.py:161`).

**2) 병렬 실행** — 세마포어(`BATCH_CONCURRENCY=10`)로 전 카테고리를 하나의 풀로 실행하고, 한 항목 실패는 `errors` 집계 후 계속합니다(`renewals.py:209-226`).

**3) `_renew_one` — 갱신 결제 1건**(`app/services/renewals.py:328`). 토스 호출(최대 65초) 동안 DB 행 잠금·커넥션을 쥐지 않도록 **3단계 트랜잭션**으로 분리합니다:

```python
# 1단계: Redis 락 + FOR UPDATE 검증 + PENDING 선기록 + commit
token = await acquire_lock(redis, f"lock:renew:{sub_id}")  # 실패 시 skipped
sub = await db.get(Subscription, sub_id, with_for_update=True)
... order_id = _renewal_order_id(sub)   # (sub.id, period_end, retry_count) 결정적
card = await get_card(db, service_id=..., external_user_id=...)  # 빌링키는 cards에서
if card is None or sub.card_id is None or not card.is_active:    # 미등록/비활성 → 실패 처리
    ...  # 합성 TossError → _handle_charge_failure 위임
billing_key = cipher.decrypt(card.billing_key_encrypted)
await db.commit()  # PENDING 내구성 + 행 잠금/커넥션 반납(외부 호출 전 필수)

# 2단계: 외부 호출(DB 비점유)
result = await resolve_charge(toss, billing_key=billing_key, ...)

# 3단계: FOR UPDATE 재취득 + 재검증 후 확정
sub = await db.get(Subscription, sub_id, with_for_update=True)
await db.refresh(payment, with_for_update=True)
if payment.status != PaymentStatus.PENDING:   # 웹훅/정산이 먼저 확정 → 중복 적용 금지
    await db.rollback(); stats["skipped"] += 1; return
... 성공 → payment DONE + _advance_period(sub, plan) / 실패 → _handle_charge_failure
```

> 중요: `order_id`는 `(sub.id, current_period_end, retry_count)`로 **결정적**입니다(`renewals.py:109` `_renewal_order_id`). 크래시 후 재실행해도 같은 주문/멱등키로 수렴해 이중결제를 막습니다. 타임아웃(결과 불명)은 절대 실패로 확정하지 않고 PENDING 유지 → 다음 배치가 같은 키로 재시도해 토스 멱등 재생으로 수렴합니다(`renewals.py:444-456`).

갱신 성공 시 기간 전진(`renewals.py:114` `_advance_period`): `transition(sub, ACTIVE)` → 새 주기 계산 → `next_billing_at` 재설정. 단 `plan.auto_renew=False`면 `next_billing_at=None`으로 두어 다음 주기 종료 시 `_expire_non_renewing`이 EXPIRED 처리합니다.

상태 전이(성공): TRIAL→ACTIVE, ACTIVE→ACTIVE, PAST_DUE→ACTIVE.

**4) 배치 종료** — `reconcile_pending`으로 타임아웃 결제 PENDING 정산 스윕을 실행하고 stats를 반환합니다(`renewals.py:227-230`).

### 2-3. 자동결제 실패 처리 — `_handle_charge_failure`

`app/services/renewals.py:534`. `retry_count`에 따라 분기합니다.

```python
payment.status = PaymentStatus.FAILED; payment.failure_code = exc.code; ...
if sub.retry_count >= cfg.retry_limit:
    transition(sub, SubscriptionStatus.SUSPENDED, now=now)  # suspended_at 기록 + next_billing=None
    await record_audit(..., action="subscription.suspended", ...)
    await email_sender.send(...)  # 담당자 정지 안내 메일
    stats["suspended"] += 1
else:
    sub.retry_count += 1
    transition(sub, SubscriptionStatus.PAST_DUE)
    sub.next_billing_at = now + cfg.retry_interval   # 재시도 예약
    await record_audit(..., action="subscription.payment_failed", ...)
    await email_sender.send(...)  # 담당자 실패 안내 메일
    stats["failed"] += 1
```

- `retry_count < retry_limit` → **PAST_DUE**(재시도 예약, 접근 유지)
- `retry_count >= retry_limit` → **SUSPENDED**(정지, 접근 차단, 자동결제 중지). 유예일(`suspended_grace`) 초과 시 `_expire_suspended`가 EXPIRED 처리.

> 참고: SUSPENDED에서도 **빌링키를 삭제하지 않습니다.** 수동 결제로 복구할 수 있도록 카드를 보존합니다(빌링키는 카드 보관함이 소유).

### 2-4. 취소 / 재개 / 수동결제 / 사용일추가

| 동작 | 라우터 | 서비스 함수 | 결과 |
|------|--------|-------------|------|
| 취소 | `subscriptions.py:174` | `cancel_subscription`(`:332`) | CANCELED(체험은 즉시 만료) |
| 재개 | `subscriptions.py:196` | `resume_subscription`(`:502`) | CANCELED→ACTIVE 또는 PAST_DUE |
| 수동결제 | `subscriptions.py:99` | `manual_charge_subscription`(`:458`) | SUSPENDED/PAST_DUE→ACTIVE |
| 사용일추가 | `subscriptions.py:126` | `add_usage_days`(`:551`) | 만료일·결제일 연장(상태 불변) |

**취소**(`subscriptions.py:332`) — 일반 구독은 기간 만료까지 혜택 유지, 체험 취소는 즉시 만료:

```python
transition(sub, SubscriptionStatus.CANCELED)  # next_billing=None 포함
if was_trial:
    sub.current_period_end = utcnow()  # 체험 취소 → 즉시 만료(다음 배치가 EXPIRED)
```

**재개**(`subscriptions.py:502`) — 만료 전 CANCELED만 가능:

```python
if sub.retry_count > 0:
    transition(sub, SubscriptionStatus.PAST_DUE)
    sub.next_billing_at = now           # 미수금 — 즉시 재시도
else:
    transition(sub, SubscriptionStatus.ACTIVE)
    sub.next_billing_at = sub.current_period_end  # 기존 기간 끝에 자동 갱신
    # auto_renew=False면 next_billing_at=None (현 주기 종료 시 만료)
```

**수동결제**(`subscriptions.py:365` `_perform_manual_charge` 공통 코어) — SUSPENDED/PAST_DUE 구독을 빌링키로 즉시 재청구. 성공 시 ACTIVE 복귀 + **결제 기준일을 결제 시점으로 리셋**:

```python
card = await get_card(db, service_id=sub.service_id, external_user_id=sub.external_user_id)
if card is None or sub.card_id is None:
    raise PaymentFailedError("등록된 카드가 없습니다. ...", code="NO_BILLING_KEY")
if not card.is_active:
    raise PaymentFailedError("비활성화된 카드입니다. ...", code="CARD_INACTIVE")
...
result = await resolve_charge(toss, billing_key=cipher.decrypt(card.billing_key_encrypted), ...)
payment.status = PaymentStatus.DONE; ...
transition(sub, SubscriptionStatus.ACTIVE)
sub.current_period_start = now
sub.current_period_end = compute_period_end(now, plan.billing_cycle, plan.cycle_days)
sub.next_billing_at = sub.current_period_end
```

외부 서비스 호출(`manual_charge_subscription`)은 actor_type=SERVICE, 어드민 호출(`admin_retry_payment`, `subscriptions.py:481`)은 actor_type=USER로 동일 코어를 재사용합니다.

### 2-5. 강제취소 / 연장 (어드민)

**강제취소**(`subscriptions.py:585` `force_cancel_subscription`) — ACTIVE·PAST_DUE·EXTENDED만 허용. `transition(sub, CANCELED)`로 즉시 `next_billing_at=None`이 되어 자동갱신 차단, 기간 만료 시 배치가 EXPIRED 처리. `service_scope`로 담당 서비스 권한을 검사합니다(목록 밖이면 NotFoundError).

**연장**(`subscriptions.py:621` `extend_subscription`) — EXPIRED 외 열린 상태만 허용. 미래 날짜 `new_end`로 만료일·결제일을 모두 설정하고 상태를 **EXTENDED**로 전환:

```python
transition(sub, SubscriptionStatus.EXTENDED)
sub.retry_count = 0; sub.suspended_at = None      # 실패/정지 흔적 정리
sub.current_period_end = new_end
sub.next_billing_at = new_end   # 그 시점에 갱신 배치가 자동결제로 갱신(DUE에 EXTENDED 포함)
```

---

## 3. 상태 전이·제약

### 3-1. 상태 머신 (`app/services/transitions.py`)

모든 상태 변경은 `transition(sub, new_status)`(`transitions.py:92`)를 거칩니다. 허용되지 않은 전이는 `InvalidStateTransition`(코드 버그 → 500)으로 드러납니다.

```
TRIAL ──→ ACTIVE ──→ PAST_DUE ──→ SUSPENDED ──→ EXPIRED
  │         │  ↑        │  ↑          │
  │         │  └────────┘  │          └──(수동결제)──→ ACTIVE
  └────┬────┴──────────────┘
       ↓
   CANCELED ──→ EXPIRED        (재개: CANCELED → ACTIVE | PAST_DUE)
```

`transition`은 **전이 허용 검증 + 보편 불변식**만 책임집니다(`transitions.py:106-115`):

- EXPIRED/CANCELED 진입 → `next_billing_at=None`
- SUSPENDED 진입 → `suspended_at=now` 기록 + `next_billing_at=None`
- ACTIVE 진입 → `retry_count=0`, `suspended_at=None`(실패 흔적 초기화)

전이별 고유 필드(기간 전진, 재시도 스케줄 등)는 **호출측이 transition 호출 후** 설정합니다. EXPIRED는 종단 상태로 어떤 전이도 불가합니다(`transitions.py:88`).

### 3-2. 제약 요약

| 제약 | 위치 |
|------|------|
| 서비스+사용자당 열린 구독 1개 | `uq_subscriptions_one_per_user`(부분 유니크) |
| 구독 생성 전 카드 등록 필수 | `subscriptions.py:206` `get_card` → NotFoundError |
| 비활성 카드로 생성 불가 | `subscriptions.py:210` ConflictError |
| 활성 구독 있는 카드 삭제 불가 | `cards.py:314` (카드 문서 참조) |
| 자동결제 실패 → PAST_DUE → SUSPENDED → EXPIRED | `renewals.py:_handle_charge_failure` |

### 3-3. 에러 처리

| 조건 | 예외 | HTTP |
|------|------|------|
| 요금제 없음/비활성/타 서비스 | `NotFoundError` | 404 |
| 체험 미제공 요금제에 trial | `InputValidationError` | 422 |
| 이미 열린 구독 존재 | `ConflictError` | 409 |
| 카드 미등록 | `NotFoundError` | 404 |
| 비활성 카드 | `ConflictError` | 409 |
| 첫 결제 타임아웃(결과 불명) | `PaymentFailedError(503, PAYMENT_UNRESOLVED)` | 503 |
| 첫 결제 카드 거절 등 | `PaymentFailedError` | 4xx |

---

## 4. 유지보수 팁

- **재시도 정책을 바꾸려면**: `GlobalSettings`(DB)의 `retry_limit`/`retry_interval_hours`/`suspended_grace_days`를 수정하세요. `process_due`가 매 배치 로드하므로 즉시 반영됩니다(`renewals.py:161`). DB 연결 불가 시 폴백은 `renewals.py:56-58`.
- **상태 전이 규칙을 바꾸려면**: `app/services/transitions.py:43` `ALLOWED_TRANSITIONS`만 고치면 됩니다. 호출부 if문에 흩어져 있던 규칙이 한곳에 모였습니다.
- **결제 금액 계산을 바꾸려면**: `app/services/billing_math.py`의 `plan_first_amount`(첫구독)·`plan_recurring_amount`(상시)를 보세요. 금액 결정 분기는 `subscriptions.py:217`.
- **배치 처리량/동시성을 조정하려면**: `BATCH_LIMIT`(.env `renewal_batch_limit`), `BATCH_CONCURRENCY=10`(`renewals.py:64-68`). 상한 도달 시 WARNING 로그가 남고 잔여분은 다음 주기에 처리됩니다.
- **이중결제가 의심되면**: `_renewal_order_id`(`renewals.py:109`)의 결정성과 3단계 트랜잭션의 PENDING 재검증(`renewals.py:475`)을 확인하세요. 타임아웃은 절대 실패 확정하지 않습니다.
- **수동결제가 카드 없음/비활성으로 막히면**: `_perform_manual_charge`(`subscriptions.py:381-389`)의 `get_card`·`is_active` 검사를 보세요. 카드 보관함에서 카드를 재등록/활성화한 뒤 다시 시도해야 합니다.

> 함께 보기: 자동연장에 쓰이는 빌링키가 어떻게 보관·복호화되는지는 [카드 보관함 기능](12-feature-card.md)을 보세요.
