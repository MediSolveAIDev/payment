# 05. 구독 갱신·만료·재시도·정합성(스케줄러 배치)

> 상호참조: [04. 구독 생성](04-subscription-create.md) · [02. 데이터베이스](02-database.md) ·
> [14. 전체 설정](14-global-settings.md) · [12. 웹훅 처리](12-webhooks.md)

---

## 1. 한 줄 요약

스케줄러가 **5분마다** 만료·취소·정지된 구독을 자동으로 결제하거나 종료하는 배치다.
사람이 아무것도 하지 않아도 구독이 알아서 갱신되고, 결제 실패 시 재시도하며, 한계를 넘으면 이용 정지까지 간다.

---

## 2. 언제 실행되나 — 트리거

**트리거: APScheduler 주기 실행 (외부 HTTP 요청 없음)**

```
APScheduler (interval, 기본 5분)
  └─ app/scheduler/runner.py : run_renewals()   ← 전역 Redis 락 획득
       └─ app/services/renewals.py : process_due()
            ├─ _expire_canceled()   취소 구독 기간 만료 → EXPIRED
            ├─ _expire_suspended()  정지 유예 초과 → EXPIRED
            ├─ _renew_one()         정기·재시도 결제
            ├─ _expire_non_renewing() auto_renew=False 구독 만료
            └─ reconcile_pending()  결과불명 PENDING 정산
```

**스케줄 설정 위치**
- `app/core/config.py:54` — `scheduler_enabled: bool = True`
- `app/core/config.py:55` — `scheduler_interval_minutes: int = 5`
- `app/main.py:55` — `scheduler_enabled`이 False이면 `start_scheduler` 자체를 건너뜀

**단일 실행 보장 — Redis 전역 락**

`app/scheduler/runner.py`에 `GLOBAL_LOCK_KEY = "lock:scheduler:renewals"` 키가 정의돼 있다.
`run_renewals`는 무작위 토큰 값으로 `redis.set(GLOBAL_LOCK_KEY, token, nx=True, ex=240)` SET NX를 시도한다.
- SET NX 성공 → 이 인스턴스가 배치를 실행한다.
- SET NX 실패 → 이미 다른 인스턴스가 실행 중. 즉시 `None` 반환하고 끝.
- **heartbeat 연장(감사 Phase 1 — 성능 H2)**: 배치 실행 동안 백그라운드 태스크가
  TTL(240s)의 1/3 주기로 토큰 비교 Lua를 통해 자기 소유 락의 TTL을 연장한다.
  따라서 배치가 240초보다 오래 걸려도 락이 만료돼 다른 인스턴스와 중첩 실행되지 않는다.
  TTL은 "heartbeat가 멈춘 뒤(프로세스 사망 등) 락이 자연 해소되기까지의 시간"(데드맨
  스위치)으로만 작동한다.
- 해제도 토큰 비교 Lua로 수행 — 만에 하나 락을 잃은 인스턴스가 남의 락을 지우지 못한다.
  `finally`에서 heartbeat 중단 + 락 해제를 보장한다.

---

## 3. 요청 진입점

HTTP 요청이 없다. 앱 시작 시 `app/main.py:55`에서 `start_scheduler(app)`을 호출하고,
`app/scheduler/runner.py:49–62`의 `start_scheduler`가 APScheduler에 잡을 등록한다.

```python
# app/scheduler/runner.py:58–60
scheduler.add_job(run_renewals, "interval",
                  minutes=app.state.settings.scheduler_interval_minutes,
                  args=[app], max_instances=1, coalesce=True)
```

- `max_instances=1` — 같은 프로세스 내 중첩 실행 차단(전역 Redis 락에 더해 2차 방어)
- `coalesce=True` — 지연된 실행이 쌓이면 한 번만 실행

---

## 4. 단계별 처리 흐름

### 4-1. GlobalSettings 로드 + 후보 조회

`app/services/renewals.py:96–138` — `process_due` 함수의 첫 번째 세션 블록.

```python
# renewals.py:118–119
async with session_factory() as db:
    gs = await get_global_settings(db)   # DB GlobalSettings(id=1) 로드
    cfg = _Cfg(gs)                       # timedelta로 변환해 cfg 객체에 보관
```

`_Cfg`(`:52–70`) 는 `GlobalSettings`(DB) 또는 하드코딩 기본값을 받아 세 가지 설정을 `timedelta`로 변환한다.

| cfg 필드 | DB 컬럼 | 기본값 | 역할 |
|---|---|---|---|
| `retry_limit` | `global_settings.retry_limit` | 4 | 재시도 최대 횟수 |
| `retry_interval` | `retry_interval_hours` → `timedelta(hours=...)` | 12h | PAST_DUE 재시도 간격 |
| `suspended_grace` | `suspended_grace_days` → `timedelta(days=...)` | 30d | SUSPENDED→EXPIRED 유예 |

**설정을 배치 실행마다 DB에서 다시 로드하는 이유**: 어드민에서 재시도 횟수를 바꾸면 다음 배치부터 바로 반영된다(재시작 불필요). 관련 테스트: `test_renewals.py:509`.

이어서 **같은 세션에서 4종 후보 ID 목록**을 읽기 전용(락 없음)으로 조회한다.

```python
# renewals.py:121–138 (조건 요약)
canceled_due    = status=CANCELED  AND current_period_end <= now
suspended_due   = status=SUSPENDED AND suspended_at <= now - cfg.suspended_grace
renew_due       = status IN (TRIAL, ACTIVE, PAST_DUE)
                  AND next_billing_at IS NOT NULL
                  AND next_billing_at <= now
non_renewing_due= status=ACTIVE
                  AND next_billing_at IS NULL      ← auto_renew=False 표시
                  AND current_period_end <= now
```

`DUE_STATUSES = (TRIAL, ACTIVE, PAST_DUE)` 는 `app/services/locks.py:22–23`에 정의돼 있다.

`next_billing_at IS NOT NULL` 조건이 중요하다. `auto_renew=False` 구독은 첫 결제 후 `next_billing_at=None`으로 저장되므로(`renewals.py:92–93`) `renew_due`에서 자동으로 빠지고, 대신 `non_renewing_due`에서 처리된다.

**처리 상한과 병렬 실행(감사 Phase 1 — 성능 H2)**

- 각 후보 쿼리는 **due 시각 오름차순 + `BATCH_LIMIT`(기본 1000)** 으로 끊는다.
  due가 폭주해도 한 배치가 끝없이 길어지지 않고, 잔여분은 다음 주기(5분)가 오래된
  건부터 이어서 처리한다. 상한 도달 시 `logger.warning`으로 명시적으로 남긴다.
- 4개 카테고리의 전 작업을 **`asyncio.Semaphore(BATCH_CONCURRENCY)`(기본 10) 병렬
  풀**로 실행한다. 과거에는 1건씩 직렬 처리라 1만 건이면 배치가 수 시간 걸렸다.
  병렬이 안전한 이유: ① 카테고리 간 대상 상태 집합이 겹치지 않아(CANCELED/SUSPENDED/
  DUE/ACTIVE+non-renewing) 한 구독이 두 카테고리에 동시에 들어올 수 없고,
  ② 건별 동시성은 구독별 Redis 락 + 결정적 order_id/토스 멱등키가 방어한다.
- 한 항목의 예외는 `stats["errors"]`만 올리고 계속 — 배치 전체를 죽이지 않는다(기존 동일).

### 4-2. 취소 만료 — `_expire_canceled`

`renewals.py:227–238` → `_expire_subscription(reason="canceled_period_end")`(`:174–207`)

흐름:
1. `lock:renew:{sub_id}` Redis 락 획득 — 실패 시 `skipped`
2. `db.get(Subscription, sub_id, with_for_update=True)` — FOR UPDATE 행 락
3. 상태 재검증: `status == CANCELED and current_period_end <= now`
4. `sub.status = EXPIRED`, `sub.next_billing_at = None`
5. `safe_delete_billing_key`로 빌링키 삭제 → 성공 시에만 `sub.billing_key_encrypted = None`
6. `record_audit(..., action="subscription.expired", detail={"reason": "canceled_period_end"})` + commit
7. `stats["expired"] += 1`

> **왜 Redis 락 + FOR UPDATE 둘 다 쓰나?**
> Redis 락은 다중 인스턴스 경쟁을 막고, FOR UPDATE는 같은 DB 트랜잭션 내 동시성을 막는다.
> 두 방어선을 조합해 이중 만료를 원천 차단한다.

### 4-3. 정지 만료 — `_expire_suspended`

`renewals.py:210–224` → `_expire_subscription(reason="suspended_timeout")`(`:174–207`)

판정 조건: `status == SUSPENDED and suspended_at <= now - cfg.suspended_grace`
처리 로직은 `_expire_canceled`와 동일(`_expire_subscription` 공유).

> **SUSPENDED 시 빌링키를 바로 지우지 않는 이유**: 수동 결제 복구를 위해 `suspended_grace` 기간 동안 결제수단을 보존해야 한다. 유예 초과 시에만 최종 삭제한다(`_expire_suspended`가 호출되면서 삭제). 관련 테스트: `test_renewals.py:110`.

### 4-4. 정기·재시도 결제 — `_renew_one`

갱신 배치의 핵심 함수 (`renewals.py`의 `_renew_one`).

> **트랜잭션 3단계 분리(감사 Phase 1 — 성능 H1)**: 토스 호출(최대 65초) 동안
> FOR UPDATE 행 잠금과 풀 커넥션을 쥐지 않도록, [1단계] FOR UPDATE 검증 +
> PENDING 선기록 → commit(잠금·커넥션 반납) → [2단계] 토스 호출(DB 비점유) →
> [3단계] FOR UPDATE **재취득 + 재검증** 후 확정 — 의 구조로 동작한다.
> 외부 호출 사이의 동시성은 ① 구독별 Redis 락(함수 전체를 감쌈, TTL 300s > 65s),
> ② 결정적 order_id + 토스 멱등키, ③ 3단계 재검증이 방어한다.

**흐름 (단계별):**

**① Redis 락 획득** (`:282–285`)
```python
lock_key = f"lock:renew:{sub_id}"
token = await acquire_lock(redis, lock_key)
if token is None:
    stats["skipped"] += 1; return
```
`acquire_lock`은 `app/services/locks.py:34–38`에서 UUID 토큰으로 SET NX + TTL 300s.

**② DB 락 + 재검증** (`:288–295`)
```python
sub = await db.get(Subscription, sub_id, with_for_update=True)
if (sub is None
        or sub.status not in DUE_STATUSES          # 상태 재검증
        or sub.next_billing_at is None or sub.next_billing_at > now  # 시점 재검증
        or sub.billing_key_encrypted is None):       # 빌링키 존재 재검증
    stats["skipped"] += 1; return
```
락 획득 사이에 상태가 바뀌었을 수 있으므로 FOR UPDATE 후 반드시 재검증한다.

**③ 결정적 order_id 생성** (`:298`, `:73–75`)
```python
order_id = _renewal_order_id(sub)
# f"r{sub.id.hex}p{int(sub.current_period_end.timestamp())}a{sub.retry_count}"
```
`(구독 ID, 기간 종료 timestamp, 재시도 횟수)`의 조합으로 생성한다.
같은 (구독, 기간, 시도)에 대해 항상 **같은 값**이 나온다.
크래시 후 재실행해도 동일한 `order_id`/멱등키로 수렴 → **이중결제 원천 차단**.

**④ DONE 결제 복구 확인** (`:300–310`)
```python
payment = await db.scalar(select(Payment).where(Payment.order_id == order_id))
if payment is not None and payment.status == PaymentStatus.DONE:
    # 방어적 복구: 결제는 됐는데 DB 커밋 전 크래시 → 재결제 없이 기간만 전진
    _advance_period(sub, plan)
    await db.commit()
    stats["renewed"] += 1; return
```
이미 `DONE` 결제가 있으면 **토스에 결제 요청을 전혀 보내지 않는다**. 기간만 전진.
관련 테스트: `test_renewals.py:261`.

**⑤ PENDING 결제 생성 → 선커밋 (1단계 트랜잭션 종료)**
```python
payment = Payment(..., status=PaymentStatus.PENDING, ...)
db.add(payment)
await db.commit()  # ← 결제 전 내구성 확보 + FOR UPDATE 잠금·커넥션 반납
```
`await db.commit()`이 결제 API 호출 전에 먼저 실행된다. 두 가지 목적:
1. **내구성**: 서버가 크래시해도 `PENDING` 레코드가 DB에 남아 `reconcile_pending`이 정산할 수 있다.
2. **잠금·커넥션 반납(감사 Phase 1 — 성능 H1)**: 토스 응답(최대 65초)을 기다리는 동안
   FOR UPDATE 행 잠금과 풀 커넥션을 쥐지 않는다. 과거에는 PENDING이 이미 존재하는
   재시도 경로에서 commit 없이 바로 토스를 호출해, 토스 지연 시 커넥션 풀이 고갈됐다.

`payment_type` 결정(`:315–317`):
- `retry_count == 0` → `PaymentType.RENEWAL`
- `retry_count > 0` → `PaymentType.RETRY`

**⑥ 토스 결제 호출** (`:326–330`)
```python
result = await resolve_charge(
    toss, billing_key=billing_key, customer_key=sub.customer_key,
    amount=amount, order_id=order_id, order_name=plan.name,
    idempotency_key=payment.idempotency_key)
```
`resolve_charge`(`app/services/payment_utils.py:38–57`)는 토스 결제 후 타임아웃 시 `order_id`로 재조회를 시도해 DONE이면 결과를 반환한다.

**⑦ 예외 처리 — 결제 3원칙** (`:331–368`)

| 예외 | 처리 | 이유 |
|---|---|---|
| `TossTimeoutError` | `payment=PENDING`, `sub` 불변, `stats["unresolved"]+=1` | 타임아웃 = 결과 불명. 실패로 확정 금지 — 원결제가 실제 승인됐을 수 있다. 다음 배치가 **같은 order_id/멱등키**로 재시도 → 토스 멱등 재생으로 수렴 |
| `TossError(ALREADY_PROCESSED_PAYMENT)` | `toss.get_payment_by_order_id`로 재조회 → DONE이면 복구 | 멱등 재생이 안 된 비정상 케이스. 재조회로 실제 결과 확인 |
| 그 외 `TossError` | `_handle_charge_failure` 호출 | 카드 거절 등 명확한 실패 |

> **결제 3원칙 — 절대 잊지 말 것**
> 1. 타임아웃은 실패가 아니다. `payment`를 `PENDING`으로 유지하고 sub는 건드리지 않는다.
> 2. 결정적 `order_id`(+멱등키)가 있어야 이중결제를 막을 수 있다.
> 3. `PENDING` 결제를 절대 실패로 확정하지 않는다. `reconcile_pending`이 10분 후 정산한다.

**⑧ 결과 확정 — 3단계 트랜잭션: FOR UPDATE 재취득 + 재검증**

외부 호출 동안 행 잠금이 풀려 있었으므로, 확정 전에 반드시 재취득·재검증한다:

```python
sub = await db.get(Subscription, sub_id, with_for_update=True)   # 재취득
await db.refresh(payment, with_for_update=True)
if payment.status != PaymentStatus.PENDING:
    await db.rollback(); stats["skipped"] += 1; return   # 다른 경로가 이미 확정
still_due = sub is not None and sub.status in DUE_STATUSES
```

- `payment`가 더 이상 PENDING이 아니면(웹훅/정산 스윕이 먼저 확정) **중복 적용 금지** → skip
- 구독이 호출 사이에 갱신 풀을 벗어났으면(취소 등) 기간을 전진시키지 않고
  결제 결과만 기록한다 — 성공 시 `requires_review` 감사를 남겨 환불 검토 대상으로
  표시(reconciliation의 orphaned 결제 정책과 동일), 실패 시 `sub_left_due_pool` 표시.
- 정상 경로(still_due=True) 성공 확정:

```python
payment.status = PaymentStatus.DONE
payment.toss_payment_key = result.payment_key
payment.approved_at = utcnow()
payment.raw_response = result.raw
_advance_period(sub, plan)
await db.commit()
stats["renewed"] += 1
```

**⑨ `_advance_period` — 기간 전진** (`renewals.py:78–93`)
```python
new_start = sub.current_period_end
sub.current_period_start = new_start
sub.current_period_end = compute_period_end(new_start, plan.billing_cycle, plan.cycle_days)
sub.next_billing_at = sub.current_period_end
sub.retry_count = 0
sub.status = SubscriptionStatus.ACTIVE  # TRIAL→ACTIVE, PAST_DUE→ACTIVE도 여기서
if not plan.auto_renew:
    sub.next_billing_at = None  # 마지막 주기 — 다음 갱신 예약 없음
```
새 기간은 이전 기간 종료일부터 시작(연속성 보장). `retry_count`를 0으로 초기화한다.

### 4-5. 결제 실패 처리 — `_handle_charge_failure`

`renewals.py:384–435`.

```
retry_count < retry_limit  →  PAST_DUE (재시도 예약)
retry_count >= retry_limit →  SUSPENDED (접근 차단)
```

**PAST_DUE 경로** (`:422–434`)
- `payment.status = FAILED`, `payment.failure_code = exc.code`
- `sub.retry_count += 1`
- `sub.status = PAST_DUE`
- `sub.next_billing_at = now + cfg.retry_interval` (12시간 후 재시도)
- 담당자 이메일 발송

**SUSPENDED 경로** (`:403–421`)
- `sub.status = SUSPENDED`, `sub.suspended_at = now`
- `sub.next_billing_at = None` — 자동결제 중지
- **빌링키는 삭제하지 않는다** — 수동 결제 복구를 위해 보존
- 담당자 이메일 발송("구독 정지 안내")
- `cfg.suspended_grace.days`일 내 수동 결제가 없으면 `_expire_suspended`가 EXPIRED 처리

### 4-6. 자동결제 안함 만료 — `_expire_non_renewing`

`renewals.py:241–257` → `_expire_subscription(reason="non_renewing_period_end")`

`auto_renew=False`인 요금제로 생성된 구독은 `_advance_period`에서 `next_billing_at=None`(`:92–93`)으로 저장된다.
기간이 지나면 `non_renewing_due` 목록에 들어와 결제 없이 바로 EXPIRED가 된다.
관련 테스트: `test_renewals.py:482`.

### 4-7. PENDING 정합성 — `reconcile_pending`

`app/services/reconciliation.py:33–65`. 배치 마지막에 항상 실행된다(`renewals.py:168–170`).

**목적**: 타임아웃 등으로 결과불명이 된 PENDING 결제를 10분 후 토스에 재조회해 확정한다.

**조회 조건** (`:50–55`)
```python
Payment.status == PENDING
AND Payment.requested_at <= now - PENDING_RECONCILE_GRACE  # 10분 경과
```
`PENDING_RECONCILE_GRACE = timedelta(minutes=10)` — `app/services/locks.py:19`.

**건너뛰는 케이스** (`:56–59`)
```python
if (stuck_payment.payment_type != PaymentType.FIRST
        and stuck_sub is not None and stuck_sub.status in DUE_STATUSES):
    continue  # _renew_one 수렴 경로가 처리(RENEWAL/RETRY는 건드리지 않음)
```
갱신 풀(TRIAL/ACTIVE/PAST_DUE)에 있는 구독의 RENEWAL/RETRY는 `_renew_one`이 같은 `order_id`/멱등키로 자체 수렴한다. `reconcile_pending`이 끼어들면 중복 처리 위험이 있으므로 건너뛴다.

**`_reconcile_one_payment` 처리 결과** (`reconciliation.py`)

> 갱신과 동일한 트랜잭션 분리가 적용돼 있다(감사 Phase 1 — 성능 H1):
> [읽기 트랜잭션] PENDING 검증 + order_id 추출 후 종료 → [외부 호출 — DB 비점유]
> 토스 재조회 → [확정 트랜잭션] FOR UPDATE 재취득 + PENDING **재검증** 후 확정.
> 재검증에서 PENDING이 아니면(웹훅 등이 먼저 확정) 중복 적용을 막기 위해 skip한다.

| 토스 조회 결과 | 처리 |
|---|---|
| `DONE` | `payment=DONE` 확정. RENEWAL/RETRY인데 구독이 CANCELED/EXPIRED이면 담당자에게 환불 검토 이메일 |
| `None`(미체결) | `payment=FAILED(RECONCILE_NOT_FOUND)`. FIRST 타입 + 구독 ACTIVE이면 구독 EXPIRED + 빌링키 삭제 |
| 비DONE 진행 중 | 건드리지 않음, 다음 주기 재확인 |
| `TossError` 조회 실패 | 건드리지 않음, 다음 주기 재시도 |

---

## 5. 사용하는 DB 테이블·컬럼

### 읽기

| 테이블 | 읽는 컬럼 | 역할 |
|---|---|---|
| `global_settings` | `retry_limit`, `retry_interval_hours`, `suspended_grace_days` | 배치 설정 로드 |
| `subscriptions` | `status`, `next_billing_at`, `current_period_end`, `suspended_at`, `billing_key_encrypted`, `retry_count`, `plan_id`, `service_id` | 후보 조회 + 상태 판정 |
| `plans` | `price`, `billing_cycle`, `cycle_days`, `name`, `recurring_discount_type/value`, `auto_renew` | 금액 계산·기간 전진 |
| `payments` | `order_id`, `status` | DONE 결제 존재 여부 확인(이중결제 방어) |
| `services` | `manager_email`, `name` | 실패 시 이메일 발송 |

### 쓰기

| 테이블 | 쓰는 컬럼 | 시점 |
|---|---|---|
| `subscriptions` | `status`, `next_billing_at`, `current_period_start`, `current_period_end`, `retry_count`, `suspended_at`, `billing_key_encrypted` | 갱신 성공/실패/만료 시 |
| `payments` | `status`, `toss_payment_key`, `approved_at`, `raw_response`, `failure_code`, `failure_message` | 결제 확정/실패 시 |
| `audit_logs` | 전 컬럼 | 모든 상태 전이마다 |

**인덱스 활용**: `subscriptions.ix_subscriptions_due` — `(status, next_billing_at)` 복합 인덱스(`app/models/subscription.py:55`)가 `renew_due` 조회 성능을 보장한다.

---

## 6. 상태 전이표

> **전이 중앙화(감사 Phase 4 — S1)**: 모든 상태 변경은 `app/services/transitions.py`의
> `transition(sub, new_status)` 헬퍼를 거친다. 허용 전이 테이블(`ALLOWED_TRANSITIONS`)에
> 없는 전이(예: EXPIRED→ACTIVE)는 `InvalidStateTransition`으로 즉시 실패하고,
> 보편 불변식(EXPIRED/CANCELED→`next_billing_at=None`, SUSPENDED→`suspended_at` 기록,
> ACTIVE 복귀→`retry_count=0`·`suspended_at=None`)을 헬퍼가 일괄 적용한다.
> 새 상태/전이를 추가할 때는 이 테이블과 단위 테스트(`tests/unit/test_transitions.py`)만
> 갱신하면 된다 — 과거처럼 11곳의 대입 지점을 일일이 찾을 필요가 없다.

```
TRIAL ─────────────────────┐
                            ▼ 결제 성공(_renew_one)
ACTIVE ──────────────────> ACTIVE (기간 전진, retry_count=0)
  │
  │ next_billing_at 도래 + 결제 실패
  ▼
PAST_DUE ────────────────> ACTIVE  (재시도 성공)
  │                  retry_count < retry_limit
  │ retry_count >= retry_limit
  ▼
SUSPENDED ──────────────> EXPIRED  (suspended_grace 초과: _expire_suspended)
  │
  │ 수동 결제 성공 → ACTIVE 복구 (06-subscription-manage 참고)

CANCELED ───────────────> EXPIRED  (current_period_end 도래: _expire_canceled)

ACTIVE(auto_renew=False)
  next_billing_at=None
  + current_period_end 도래 → EXPIRED (_expire_non_renewing)
```

| 전이 | 조건 | 처리 함수 |
|---|---|---|
| TRIAL/ACTIVE/PAST_DUE → ACTIVE | `_renew_one` 결제 성공 | `_advance_period` |
| ACTIVE/TRIAL → PAST_DUE | 결제 실패, `retry_count < retry_limit` | `_handle_charge_failure` |
| PAST_DUE → SUSPENDED | 결제 실패, `retry_count >= retry_limit` | `_handle_charge_failure` |
| CANCELED → EXPIRED | `current_period_end <= now` | `_expire_canceled` |
| SUSPENDED → EXPIRED | `suspended_at <= now - grace` | `_expire_suspended` |
| ACTIVE(auto_renew=False) → EXPIRED | `next_billing_at IS NULL AND period_end <= now` | `_expire_non_renewing` |

**서비스 접근 권한**: `TRIAL`, `ACTIVE`, `PAST_DUE`, `CANCELED` 상태는 접근 허용(`ACCESS_ALLOWED_STATUSES`, `app/models/enums.py:77`). `SUSPENDED`와 `EXPIRED`는 접근 차단.

---

## 7. 예외·엣지 케이스

### 한 건 실패가 배치 전체를 중단시키지 않는다

`process_due` 내 모든 `for` 루프는 `except Exception`으로 예외를 잡고 `stats["errors"] += 1`만 증가시킨 뒤 계속 진행한다(`renewals.py:143–145`, `:150–152`, `:157–159`, `:165–167`). 사용자 A의 결제 실패가 사용자 B의 갱신을 막지 않는다.

### 이중결제 방지 — 결정적 order_id

`_renewal_order_id(sub)`(`:73–75`)는 `(sub.id, current_period_end, retry_count)` 조합으로 항상 같은 값을 반환한다. 배치가 크래시 후 재실행되어도 같은 `order_id`로 수렴한다.
- 토스 API는 같은 `order_id`/멱등키를 받으면 이미 처리된 결과를 그대로 반환한다.
- DB에서도 `DONE` 결제를 발견하면 재결제 없이 기간만 전진한다(`:300–310`).

### 타임아웃 → PENDING 유지

`TossTimeoutError`를 잡으면 `payment`를 `PENDING`으로 유지하고 `sub`은 절대 건드리지 않는다(`:331–342`). 타임아웃 ≠ 실패다. 다음 배치가 같은 `order_id`/멱등키로 재시도하면 토스 멱등 재생이 동작한다. `reconcile_pending`(10분 경과 후)도 별도로 정산한다. 관련 테스트: `test_renewals.py:296`.

### ALREADY_PROCESSED_PAYMENT

멱등 재생이 안 된 비정상 케이스. 토스에 `get_payment_by_order_id`로 재조회해 `DONE`이면 복구 처리(`:344–364`).

### 빌링키 삭제 실패

`safe_delete_billing_key`(`app/services/payment_utils.py:24–35`)는 삭제 실패 시 `False` 반환. 호출측은 삭제 성공 시에만 `billing_key_encrypted = None`으로 지운다(`renewals.py:199–200`). 실패 시 암호문을 보존해 운영자가 수동으로 재시도할 수 있게 한다. 404 응답은 "이미 삭제됨"으로 성공 처리(`:32`).

### 자동결제 안함(auto_renew=False) 만료

`plan.auto_renew=False`이면 `_advance_period`에서 `sub.next_billing_at = None`(`:92–93`)으로 저장된다. 이 구독은 `renew_due` 목록에 들어오지 않고(`:129–133`의 `IS NOT NULL` 조건), 대신 `non_renewing_due`(`:134–138`)에서 결제 없이 EXPIRED 처리된다.

### reconcile_pending이 RENEWAL/RETRY를 건너뛰는 이유

갱신 풀에 있는 구독의 RENEWAL/RETRY PENDING은 다음 배치의 `_renew_one`이 같은 `order_id`/멱등키로 처리한다. `reconcile_pending`이 끼어들면 락 충돌 없이 중복 처리될 수 있으므로 명시적으로 `continue`한다(`reconciliation.py:57–59`).

---

## 8. 관련 테스트

모든 갱신 배치 테스트는 `tests/integration/test_renewals.py`에 있다.

| 테스트 함수 | 검증 내용 |
|---|---|
| `test_renews_due_subscription` (`:40`) | ACTIVE 구독 정기 갱신 성공 → DONE + 기간 전진 |
| `test_not_due_untouched` (`:59`) | 미래 next_billing 구독은 건드리지 않음 |
| `test_failure_moves_to_past_due_and_notifies` (`:69`) | 결제 실패 → PAST_DUE + retry_count=1 + 이메일 |
| `test_retry_success_restores_active_continuous_period` (`:91`) | PAST_DUE 재시도 성공 → ACTIVE + 기간 연속 + RETRY 타입 |
| `test_retries_exhausted_suspends_and_keeps_key` (`:110`) | 재시도 소진 → SUSPENDED + 빌링키 보존 |
| `test_full_retry_storyline_to_suspended` (`:132`) | 전체 스토리: 1+4회 실패 → SUSPENDED |
| `test_suspended_expires_after_grace` (`:153`) | SUSPENDED 유예 초과 → EXPIRED + 빌링키 삭제 |
| `test_suspended_within_grace_kept` (`:171`) | SUSPENDED 유예 내 → 건드리지 않음 |
| `test_trial_expiry_charges_to_active` (`:185`) | TRIAL 만료 → 결제 → ACTIVE |
| `test_trial_charge_failure_goes_past_due` (`:203`) | TRIAL 결제 실패 → PAST_DUE |
| `test_canceled_expires_at_period_end_without_charge` (`:219`) | CANCELED 기간 만료 → EXPIRED (결제 없음) |
| `test_redis_lock_prevents_double_charge` (`:249`) | Redis 락 존재 시 → skipped (이중 결제 방지) |
| `test_crash_recovery_done_payment_advances_without_recharge` (`:261`) | 크래시 복구: DONE 결제 존재 → 재결제 없이 기간 전진 |
| `test_renewal_timeout_unresolved_preserved_then_converges` (`:296`) | 타임아웃 → PENDING 유지 → 다음 배치 같은 멱등키로 수렴 |
| `test_reconcile_stuck_first_payment_done` (`:323`) | FIRST PENDING → 토스 DONE → 확정 |
| `test_reconcile_stuck_first_payment_not_found_expires` (`:351`) | FIRST PENDING → 토스 미체결 → FAILED + 구독 EXPIRED |
| `test_reconcile_young_pending_untouched` (`:378`) | 10분 미경과 PENDING → 건드리지 않음 |
| `test_reconcile_orphan_renewal_on_canceled_sub_done` (`:423`) | 취소된 구독의 RENEWAL DONE → 환불 검토 이메일 |
| `test_reconcile_skips_renewal_pending_while_sub_in_pool` (`:450`) | 갱신 풀 구독의 RENEWAL PENDING → skip |
| `test_non_renewing_expires_at_period_end` (`:482`) | auto_renew=False 기간 만료 → EXPIRED (결제 없음) |
| `test_retry_limit_from_global_settings` (`:509`) | GlobalSettings.retry_limit DB 반영 즉시 적용 |

스케줄러 수준 테스트는 `tests/integration/test_scheduler.py`에 있다:
- `test_run_renewals_processes_due` (`:10`) — `run_renewals(app)` 엔드투엔드
- `test_run_renewals_skips_when_global_lock_held` (`:22`) — 전역 Redis 락 보유 시 skip

**테스트 실행**
```bash
# 전체 갱신 배치 테스트
pytest tests/integration/test_renewals.py -v

# 스케줄러 테스트
pytest tests/integration/test_scheduler.py -v

# 특정 케이스만
pytest tests/integration/test_renewals.py::test_crash_recovery_done_payment_advances_without_recharge -v
```

`tests/conftest.py:27`에서 `scheduler_enabled=False`로 테스트 중 자동 배치 실행을 막는다.
테스트는 `process_due`를 직접 호출해 시간을 `now=` 파라미터로 주입한다.

---

## 9. 유지보수 팁

### 재시도 횟수·간격·유예 일수 바꾸기

**방법 1 (즉시 반영)**: 어드민 콘솔 → 설정 → 재시도 설정.
`app/admin/routes/settings.py`에서 `app_settings.update_retry_settings`를 호출한다.
다음 배치 실행 시 `process_due`가 `get_global_settings`(`:119`)로 DB를 다시 읽으므로 **재시작 없이 즉시 적용**된다.

**방법 2 (환경변수 기본값)**: `.env`의 `RETRY_INTERVAL_HOURS`, `RETRY_LIMIT`, `SUSPENDED_GRACE_DAYS`.
이 값은 DB에 행이 없을 때 `GlobalSettings` 모델 기본값(`app/models/global_settings.py:22–24`)으로 사용된다. DB 행이 이미 있으면 영향 없음.

### 스케줄 주기 바꾸기

`.env`의 `SCHEDULER_INTERVAL_MINUTES` 또는 `app/core/config.py:55` — `scheduler_interval_minutes`.
변경 후 서버 재시작 필요. `scheduler_enabled=False`이면 배치 자체가 실행되지 않는다(`app/main.py:55`).

### "PENDING에 멈췄다" — 디버깅

1. `payments` 테이블에서 `status='PENDING'` 레코드를 확인한다.
2. `requested_at`이 10분 이상 지났으면 `reconcile_pending`이 다음 배치에서 처리한다.
3. 즉시 확인하려면 어드민에서 배치를 수동 실행하거나 `process_due`를 CLI로 호출한다.
4. `audit_logs`에서 `action='subscription.renewal_unresolved'`를 찾으면 타임아웃이 있었다는 의미.
5. 같은 `order_id`로 토스 어드민 콘솔에서 결제 상태를 직접 조회한다.
6. `order_id` 구조는 `r{sub_id_hex}p{period_end_timestamp}a{retry_count}` — 직접 디코딩 가능.

### "SUSPENDED 됐다" — 디버깅

1. `subscriptions` 테이블에서 `status='SUSPENDED'`, `suspended_at` 확인.
2. `audit_logs`에서 `action='subscription.payment_failed'` 이력과 `detail.retry_count` 확인.
3. `payments`에서 `subscription_id`로 조회해 `FAILED` 레코드와 `failure_code` 확인.
4. `suspended_at + suspended_grace_days` 이전에 수동 결제(`manual_charge_subscription`)로 복구 가능.
5. 유예 기간이 지나면 다음 배치에서 EXPIRED가 된다 — 복구 불가.

### 감사 로그 action 목록 (갱신 배치)

| action | 의미 |
|---|---|
| `subscription.renewed` | 갱신 결제 성공 (크래시 복구 포함) |
| `subscription.payment_failed` | 결제 실패 → PAST_DUE |
| `subscription.suspended` | 재시도 소진 → SUSPENDED |
| `subscription.expired` | 만료 처리 완료 |
| `subscription.renewal_unresolved` | 타임아웃 → PENDING 유지 |
| `payment.reconciled_done` | 정합성 정산 → DONE 확정 |
| `payment.reconciled_failed` | 정합성 정산 → FAILED 확정 |

`actor_type`은 전부 `"SYSTEM"`. 어드민 감사 로그 화면에서 action으로 필터링 가능.
