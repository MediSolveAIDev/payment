# 05. 자동 갱신 · 재시도 · 정지 · 만료 (배치)

> 04에서 만든 구독은 시간이 흐르며 **사람의 개입 없이** 자동 결제·전이된다.
> 이 일을 하는 것이 주기적으로 도는 **갱신 배치**다. 체험 만료 자동결제, 정기 결제,
> 결제 실패 재시도, 정지, 만료가 모두 여기서 일어난다.
>
> 선행: [04-subscription-create.md](04-subscription-create.md)(PENDING 선커밋·타임아웃 처리),
> [03-plans.md](03-plans.md)(금액/기간 계산). PENDING 정산 스윕 상세는 [07-payment-reconcile.md] 참조.

---

## 0. 한눈에 보기

- **호출 주체**: 사람도 외부 서비스도 아닌 **스케줄러**(APScheduler, 기본 5분 간격).
- **진입점**: `app/scheduler/runner.py` `run_renewals` → `app/services/renewals.py` `process_due`.
- **HTTP 없음**: 이 기능엔 API 엔드포인트가 없다. 시간이 트리거다.

| 처리 대상 | 무엇을 하나 | 함수 |
|---|---|---|
| 결제일 도래(TRIAL/ACTIVE/PAST_DUE) | 자동 결제 → 성공 시 다음 주기로 | `_renew_one` |
| 결제 실패 | 재시도 예약(PAST_DUE) 또는 정지(SUSPENDED) | `_handle_charge_failure` |
| 정지 후 유예 초과(SUSPENDED) | 만료(EXPIRED) + 빌링키 삭제 | `_expire_suspended` |
| 해지 후 기간 종료(CANCELED) | 만료(EXPIRED) + 빌링키 삭제 | `_expire_canceled` |
| **자동결제 안함 기간 종료(ACTIVE, next_billing=None)** | **만료(EXPIRED) + 빌링키 삭제** | **`_expire_non_renewing`** |
| 결과 불명(PENDING) 결제 | 토스 재조회로 확정 | `_reconcile_pending_payments`(문서 07) |

관련 파일: `app/scheduler/runner.py`, `app/services/renewals.py`.
설정: 재시도 관련 3개 값(`retry_limit`/`retry_interval_hours`/`suspended_grace_days`)은
**`GlobalSettings`(DB) 단일 행**에서 매 배치 실행마다 로드(문서 13). 스케줄러 주기는
`app/core/config.py`(`scheduler_interval_minutes`).

---

## 1. 스케줄러 배선 (`scheduler/runner.py`)

```python
def start_scheduler(app):
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(run_renewals, "interval",
                      minutes=settings.scheduler_interval_minutes,   # 기본 5분
                      args=[app], max_instances=1, coalesce=True)
    scheduler.start()
```
- `main.py`의 lifespan에서 `scheduler_enabled`(기본 True)면 시작된다(문서 00).
- `max_instances=1`+`coalesce=True` — 한 번에 하나만 실행, 밀린 실행은 합쳐서 1회로.

```python
async def run_renewals(app):
    if not await redis.set(GLOBAL_LOCK_KEY, "1", nx=True, ex=240):   # 전역 락
        return None        # 다른 인스턴스가 이미 실행 중이면 건너뜀
    try:
        stats = await process_due(session_factory, redis, toss, cipher, email_sender,
                                  settings=settings)
        return stats
    finally:
        await redis.delete(GLOBAL_LOCK_KEY)
```
- **전역 Redis 락**: 서버가 여러 대(다중 인스턴스)여도 배치가 동시에 두 번 안 돌게 막는다.
  TTL 240초(인터벌 300초보다 짧음) = **데드맨 스위치**: 배치가 예외 없이 멈춰도 다음 주기 전에
  락이 만료돼 영구 정지를 막는다.
- 락이 겹치는 짧은 순간이 있어도, 아래의 **구독별 락 + 토스 멱등키**가 이중 결제를 차단한다(이중 안전).

---

## 2. 배치 1회 = `process_due` (`renewals.py:96`)

```python
async def process_due(..., now=None):
    now = now or utcnow()
    stats = {...}
    async with session_factory() as db:
        gs = await get_global_settings(db)   # ★ 재시도 한계·간격·유예를 DB 전역설정에서 로드(요청 013)
        cfg = _Cfg(gs)                       # GlobalSettings → timedelta 변환
        # ① 처리 대상 'ID만' 먼저 수집(짧은 트랜잭션)
        canceled_due  = CANCELED 이고 current_period_end <= now
        suspended_due = SUSPENDED 이고 suspended_at <= now - 유예일수
        renew_due     = status in (TRIAL,ACTIVE,PAST_DUE) 이고 next_billing_at is not None 이고 <= now
        non_renewing_due = ACTIVE 이고 next_billing_at is None 이고 current_period_end <= now  # ★ auto_renew=False
    # ② 각 ID를 개별 처리(항목마다 독립 트랜잭션 + 락)
    for id in canceled_due:       _expire_canceled(...)        # try/except로 감쌈
    for id in suspended_due:      _expire_suspended(...)
    for id in renew_due:          _renew_one(...)
    for id in non_renewing_due:   _expire_non_renewing(...)    # ★ 자동결제 안함 만료
    # ③ 결과 불명 PENDING 정산 스윕(문서 07)
    reconcile_pending(...)
    return stats
```

설계 포인트(초급자용):
- **재시도 설정은 DB에서 로드**: `retry_limit`/`retry_interval_hours`/`suspended_grace_days`를
  `GlobalSettings`(DB)에서 매 배치 실행마다 읽는다(요청 013). Admin에서 값을 바꾸면
  **다음 배치 실행부터 즉시 반영**된다. `.env`를 바꿀 필요 없다(문서 13).
- **ID만 먼저 모으고**, 처리는 항목별로 따로 한다. 한 번의 거대한 트랜잭션이 아니라
  **구독 1개 = 트랜잭션 1개**. 한 건이 실패해도(예: 토스 일시 오류) 나머지는 계속 처리된다.
- 각 `for` 루프가 `try/except`로 감싸져 있어 **한 항목의 예외가 배치 전체를 죽이지 않는다**
  (실패는 `stats["errors"]`로 집계하고 로그만 남김).
- `_DUE_STATUSES = (TRIAL, ACTIVE, PAST_DUE)` — **체험 만료도 여기 포함**. 그래서 04의 체험
  구독(`next_billing_at = 체험 만료일`)이 만료되면 `_renew_one`이 잡아 첫 자동결제를 한다.
  `renew_due` 쿼리는 `next_billing_at is not None` 조건을 추가해 `non_renewing_due`(자동결제 안함)와 겹치지 않는다.
- `stats`는 처리 통계(`renewed/failed/suspended/expired/skipped/unresolved/reconciled/errors`).

---

## 3. 핵심 — 자동 결제 `_renew_one` (`renewals.py:215`)

가장 중요한 함수. 04의 첫 결제와 같은 "PENDING 선커밋 → 결제 → 결과 확정" 패턴을 따른다.

### 3-1. 락 + 재확인(가드)

```python
lock_key = f"lock:renew:{sub_id}"
token = await _acquire_lock(redis, lock_key)        # 구독별 분산 락
if token is None: stats["skipped"] += 1; return     # 다른 워커가 처리 중 → 건너뜀
try:
    async with session_factory() as db:
        sub = await db.get(Subscription, sub_id, with_for_update=True)   # 행 잠금(SELECT FOR UPDATE)
        if (sub is None or sub.status not in _DUE_STATUSES
                or sub.next_billing_at is None or sub.next_billing_at > now
                or sub.billing_key_encrypted is None):
            stats["skipped"] += 1; return            # 조건 재확인 — 그새 바뀌었으면 건너뜀
```
- **이중 잠금**: Redis 락(`_acquire_lock`)으로 워커 간 중복을, `with_for_update=True`로 DB 행을 잠근다.
- **재확인**: 대상 ID를 모은 뒤 실제 처리까지 시간차가 있다. 그새 취소/결제됐을 수 있으니
  **다시 조건을 검사**한다(빌링키 없으면 결제 불가 → skip).

### 3-2. 결정적 주문번호 + 멱등 복구

```python
order_id = _renewal_order_id(sub)   # = f"r{sub.id}p{기간종료 timestamp}a{retry_count}"
payment = await db.scalar(select(Payment).where(Payment.order_id == order_id))
if payment is not None and payment.status == DONE:
    _advance_period(sub, plan)      # 이미 DONE으로 기록돼 있으면 재결제 없이 기간만 전진
    record_audit("subscription.renewed", detail={"recovered": True}); commit
    stats["renewed"] += 1; return
```
- **`_renewal_order_id`는 (구독, 현재 기간, 시도횟수)에 대해 결정적**이다. 같은 상황을 다시 처리해도
  **같은 order_id/멱등키**가 나온다 → 크래시 후 재실행해도 토스가 같은 결제를 멱등 처리.
- 같은 order_id가 이미 DONE이면(웹훅/수동정정 등) **재결제 없이 기간만 전진**하는 방어 복구.

### 3-3. PENDING 결제 생성 → 커밋 → 결제

```python
amount = plan_recurring_amount(plan)    # ★ 갱신은 항상 '상시 할인가'(문서 03)
if payment is None:
    payment = Payment(subscription_id=sub.id, order_id=order_id, amount=amount,
                      payment_type=(RENEWAL if retry_count==0 else RETRY),
                      status=PENDING, idempotency_key=f"renew-{order_id}", requested_at=now,
                      kind=PaymentKind.SUBSCRIPTION,      # ★ 구독 결제 종류 명시
                      service_id=sub.service_id,          # ★ 서비스 추적용
                      external_user_id=sub.external_user_id)  # ★ 사용자 추적용
    db.add(payment); await db.commit()   # 결제 전 내구성 선점(04와 동일 원칙)

billing_key = cipher.decrypt(sub.billing_key_encrypted)
try:
    result = await resolve_charge(toss, billing_key=..., amount=amount,
                                  order_id=order_id, idempotency_key=payment.idempotency_key)
except TossTimeoutError:
    record_audit("subscription.renewal_unresolved"); commit; stats["unresolved"] += 1; return
except TossError as exc:
    ... (3-5 실패 처리)
    return
# 성공
payment.status = DONE; payment.toss_payment_key = result.payment_key
payment.approved_at = utcnow(); payment.raw_response = result.raw
_advance_period(sub, plan)              # ★ 다음 주기로 전진 + ACTIVE
record_audit("subscription.renewed"); commit; stats["renewed"] += 1
```

- 갱신 금액은 **`plan_recurring_amount`(상시 할인가)**. 첫구독 할인은 가입 때 한 번뿐(문서 03).
- 결제 유형: 첫 시도면 `RENEWAL`, 재시도면 `RETRY`.
- **타임아웃**: 04와 동일하게 **PENDING 유지 + 다음 배치/정산에 맡김**(실패로 단정 안 함).
  같은 order_id로 다음 배치가 재시도 → 토스 멱등으로 수렴.

### 3-4. 기간 전진 `_advance_period` (성공 시)
```python
sub.current_period_start = 기존 current_period_end   # 새 주기 시작 = 직전 종료
sub.current_period_end   = compute_period_end(새 시작, cycle, cycle_days)
sub.next_billing_at      = 새 종료일
sub.retry_count          = 0
sub.status               = ACTIVE
```
체험(TRIAL)이든 미수(PAST_DUE)든 **성공하면 ACTIVE로 정상화**되고 다음 결제일이 다시 미래로 설정된다.
기준일을 "직전 종료일"부터 이어 붙여 결제일이 밀리지 않는다.

### 3-5. 결제 실패 처리 `_handle_charge_failure` (`renewals.py`)

`TossError`(카드 거절 등)일 때:
```python
payment.status = FAILED; payment.failure_code/message = ...
if sub.retry_count >= cfg.retry_limit:        # 재시도 한도 소진(기본 4회)
    sub.status = SUSPENDED                     # 강제 정지(접근 차단)
    sub.suspended_at = now
    sub.next_billing_at = None                 # 자동결제 중지(빌링키는 수동결제용 보존)
    record_audit("subscription.suspended", detail={reason:"retries_exhausted", ...})
    email_sender.send(대표 담당자, "구독 정지 안내 ...")
    stats["suspended"] += 1
else:
    sub.retry_count += 1
    sub.status = PAST_DUE                       # 미수(접근은 유지)
    sub.next_billing_at = now + cfg.retry_interval   # 기본 12시간 뒤 재시도
    record_audit("subscription.payment_failed", detail={retry_count, code})
    email_sender.send(대표 담당자, "결제 실패 안내 (재시도 n/limit)")
    stats["failed"] += 1
```

실패 → **재시도 루프**:
- 1~4회차: `PAST_DUE`(접근 유지!) + 12시간 뒤 다시 시도. 매번 담당자에게 실패 메일.
- 한도(4회) 소진: `SUSPENDED`(접근 차단). 자동결제 멈추고(`next_billing_at=None`), 빌링키는
  **수동 결제(문서 06)** 를 위해 남겨둠. 정지 안내 메일.

> 왜 PAST_DUE에선 접근을 유지하나? `ACCESS_ALLOWED_STATUSES`에 PAST_DUE가 포함(문서 00) —
> 일시적 결제 실패로 곧장 서비스를 끊지 않고 재시도 유예를 준다. SUSPENDED부터 차단.

---

## 4. 만료 처리 (종단 상태로)

### 4-1. 정지 만료 — `_expire_suspended`
`SUSPENDED`가 된 뒤 **유예일수(기본 30일)** 가 지나면(`suspended_at <= now - grace`):
- `status = EXPIRED`(완전 종료), `next_billing_at = None`.
- **빌링키 삭제**(`safe_delete_billing_key`) + 성공 시 암호문 제거 → 결제수단 영구 제거.
- 감사 `subscription.expired` (reason: `suspended_timeout`).

### 4-2. 해지 만료 — `_expire_canceled`
사용자가 해지 예약(`CANCELED`, 문서 06)한 구독이 **기간 종료일이 지나면**:
- `status = EXPIRED`, `next_billing_at = None`, 빌링키 삭제(best-effort).
- 감사 `subscription.expired` (reason: `canceled_period_end`).

### 4-3. 비자동갱신 만료 — `_expire_non_renewing` (요청 013)

`auto_renew=False` 요금제로 생성된 구독은 구독 생성 시 `next_billing_at=None`으로 저장된다
(문서 03·04). 배치는 다음 조건이 모두 충족되면 EXPIRED로 처리한다:

```
ACTIVE 상태
  AND next_billing_at IS NULL      # 자동갱신 없음 표시
  AND current_period_end <= now    # 기간 만료
```

처리:
- `status = EXPIRED`, `next_billing_at = None`, 빌링키 삭제(best-effort).
- 감사 `subscription.expired` (reason: `non_renewing_period_end`).

> **왜 ACTIVE + next_billing=None인가?** CANCELED 구독도 `next_billing_at=None`이지만
> 이미 `_expire_canceled`가 처리한다. `_expire_non_renewing`은 `ACTIVE`만 대상으로 하여
> 중복 처리를 피한다.

세 만료 모두 락 + 행잠금 + 재확인 가드를 거친다(`_renew_one`과 동일 안전 패턴).
EXPIRED는 **종단 상태**(되돌릴 수 없음) — 다시 쓰려면 새 구독을 만들어야 한다.

---

## 5. 구독 상태 전이 전체 그림

```
                      ┌──────────── 결제 성공 ───────────┐
                      ▼                                  │
  TRIAL ──만료+첫결제──▶ ACTIVE ──결제일 도래──▶ (_renew_one)
                            ▲                       │  성공→ACTIVE(기간전진)
                            │ 수동결제 성공          │  실패↓
                       SUSPENDED ◀──재시도 한도소진── PAST_DUE ──재시도(12h)──┐
                          │ (접근차단, 자동결제중지)   (접근유지)             │
                          │ 유예(30일) 초과                                  └─→ 재시도 반복
                          ▼
                       EXPIRED ◀── CANCELED(해지예약) 기간종료 ──┐
                       (종단)                                    │
                                              사용자 cancel(문서06)─┘
```
- **체험 만료**: `TRIAL` + `next_billing_at(=만료일) <= now` → `_renew_one`이 상시 할인가로 첫 결제 → `ACTIVE`.
- **정기 결제**: `ACTIVE` + 결제일 → 성공 시 기간 전진(ACTIVE 유지), 실패 시 PAST_DUE.
- **재시도**: PAST_DUE에서 `retry_interval_hours`(DB 기본 12h) 간격 최대 `retry_limit`(DB 기본 4)회 → 소진 시 SUSPENDED.
- **만료**: SUSPENDED `suspended_grace_days`(DB 기본 30일) 경과 또는 CANCELED 기간 종료 → EXPIRED(빌링키 삭제).
- **비자동갱신 만료(요청 013)**: `auto_renew=False` 구독(ACTIVE + next_billing=None) 기간 종료 → EXPIRED(빌링키 삭제).

---

## 6. 멱등성 · 동시성 (이중·삼중 안전)

| 장치 | 역할 |
|---|---|
| 전역 Redis 락(`lock:scheduler:renewals`, TTL 240s) | 인스턴스 간 배치 중복 실행 차단 + 데드맨 스위치 |
| 구독별 Redis 락(`lock:renew:{id}`) | 같은 구독을 두 워커가 동시에 처리 못 하게 |
| `with_for_update=True` | DB 행 잠금(트랜잭션 레벨 직렬화) |
| 처리 직전 조건 재확인 | 대상 수집~처리 사이 상태 변화 방어 |
| 결정적 `order_id`/`idempotency_key` | 크래시 후 재실행·중복 시도에도 토스가 멱등 처리 |
| 결제 전 PENDING 커밋 | 크래시·타임아웃 후에도 추적·정산 가능(문서 07) |
| 타임아웃=결과 불명 | 실패로 단정 안 함 → 이중결제/유실 방지 |

이 장치들이 합쳐져, **배치가 겹쳐 돌거나 중간에 죽어도 한 구독에 결제가 두 번 일어나지 않는다.**

---

## 7. 설정값

### 스케줄러 설정 (`core/config.py`)

| 설정 | 기본 | 의미 |
|---|---|---|
| `scheduler_enabled` | True | 스케줄러 가동 여부(테스트는 False) |
| `scheduler_interval_minutes` | 5 | 배치 주기 |

### 재시도·유예 설정 (`global_settings` 테이블, 문서 13)

| 컬럼 | 기본 | 의미 |
|---|---|---|
| `retry_limit` | 4 | 결제 실패 재시도 횟수(초과 시 정지) |
| `retry_interval_hours` | 12 | 재시도 간격(시간) |
| `suspended_grace_days` | 30 | 정지 후 만료까지 유예(일) |

`.env` 대신 **DB의 GlobalSettings** 로 관리한다. Admin 화면(`/admin/settings`)에서
서버 재시작 없이 즉시 변경할 수 있으며, 다음 배치 실행부터 반영된다(문서 13).

---

## 8. 관련 테스트

- `tests/integration/test_renewals.py` — 정기 결제 성공/실패, 재시도→정지, 정지/해지 만료,
  타임아웃→unresolved, 결정적 order_id 멱등, 정지 메일 수신처(대표 담당자) 등.
- `tests/integration/test_trial_and_manual.py` — 체험 만료 자동 전환.
- `tests/integration/test_scheduler.py` — 배치 실행/전역 락.
- `tests/e2e/test_full_flow.py` — 구독 생성→만료 도래→`run_renewals`→정가 자동연장 전 구간.

테스트는 보통 `process_due(..., now=미래시각)`을 직접 호출해 "시간이 흐른 것처럼" 만든다
(`now` 파라미터가 그 용도).

---

## 9. 유지보수 체크리스트

1. **결제 흐름의 3원칙(문서 04)을 여기서도 절대 깨지 말 것**: 결제 전 PENDING 커밋,
   타임아웃=PENDING 유지, 결정적 order_id. 깨면 이중결제/유실.
2. **재시도/정지/유예 정책 변경**은 `GlobalSettings`(DB, `/admin/settings`)로. 코드(`_handle_charge_failure`)는 그대로.
   `retry_limit`를 낮추면 이미 PAST_DUE인 구독도 다음 배치에서 소진 처리될 수 있다.
3. **새 상태 전이 추가 시**: `_DUE_STATUSES`/`ACCESS_ALLOWED_STATUSES`(enum) 영향 검토 +
   `process_due`의 대상 수집 쿼리 + 상태 전이도 + 테스트.
4. **비자동갱신 만료(`_expire_non_renewing`)**: `auto_renew=False` 요금제로 구독을 생성하면
   `next_billing_at=None`이 설정된다. 배치는 ACTIVE + next_billing=None + 기간 만료 조건으로
   이 구독들을 찾아 만료 처리한다. 새로운 "기간 한정" 요금제 설계 시 이 경로를 활용한다.
4. **항목별 독립 트랜잭션 유지**: 여러 구독을 한 트랜잭션으로 묶지 말 것(한 건 실패가 전체를 막음).
5. **락 순서**: 항상 `_acquire_lock` → 처리 → `finally: _release_lock`. 락 안에서 조건 재확인 필수.
6. PENDING 정산 스윕(`_reconcile_pending_payments`) 변경은 **문서 07**과 함께 — 갱신 풀과의
   소유권 경계(같은 order_id를 누가 확정하는가)를 깨지 않도록 주의.
