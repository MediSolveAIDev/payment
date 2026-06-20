# 04. 구독 생성 (첫 결제 / 체험 / 빌링키)

> 외부 서비스가 **사용자를 구독시키는** 핵심 흐름. 여기서 처음으로
> 외부 API(`/api/v1`)와 토스 결제가 등장한다. "결제 전 PENDING 선커밋"과
> "타임아웃=결과 불명" 처리가 이 시스템에서 가장 중요한 안전장치다.
>
> 선행: [00-overview.md](00-overview.md), [03-plans.md](03-plans.md)의 금액 계산.

---

## 0. 한눈에 보기

- **호출 주체**: 외부 서비스(서버). 사람(Admin)이 아니다.
- **엔드포인트**: `POST /api/v1/subscriptions`
- **인증**: HMAC 서명 + IP 화이트리스트(문서 08) + **결제 전용 추가 레이트리밋**
- **결제**: 토스 빌링키 발급 → 첫 결제 승인(토스)

| 단계 | 코드 |
|---|---|
| HTTP 진입 + 인증/레이트리밋 | `app/api/v1/subscriptions.py` `create_subscription`, `api/deps.payment_rate_limit` |
| 요청 본문 검증(스키마) | `app/schemas/api.py` `SubscriptionCreateRequest` |
| 도메인 로직 | `app/services/subscriptions.py` `create_subscription` |
| 금액 계산 | `app/services/billing_math.py`(문서 03) |
| 토스 호출 | `app/toss/client.py` `issue_billing_key` / `charge` |
| 응답 직렬화 | `SubscriptionResponse.from_model` |
| Payment 신규 필드(c3d4e5f6a7b8) | `kind=SUBSCRIPTION`, `service_id`, `external_user_id` — `Payment` 생성 시 함께 설정 |

연동 사전 조건(프론트엔드): 토스 SDK `requestBillingAuth()`로 **`authKey`** 를 받아두고,
외부 서버가 그 `authKey` + `customer_key`로 이 API를 호출한다(README "외부 서비스 연동" 참고).

---

## 1. 요청/응답 구조

### 요청 — `SubscriptionCreateRequest` (`schemas/api.py`)
```python
external_user_id : str   # 외부 서비스의 사용자 식별자(그 서비스 내에서 유일)
plan_id          : UUID  # 구독할 요금제
auth_key         : str   # 토스 빌링 인증키(프론트 SDK에서 받음)
customer_key     : str   # 토스 customerKey(2~300자)
trial            : bool = False   # 체험으로 시작할지
# ❗ 금액 필드가 없다 — 금액은 서버가 plan에서 계산한다(조작 차단)
```
**핵심 보안**: 요청에 금액이 없다. 클라이언트가 "100원만 받아라" 식으로 조작할 수 없고,
서버가 요금제(plan)에서 금액을 직접 계산한다.

### 응답 — `SubscriptionResponse`
```python
id, external_user_id, plan_id, plan_name, status,
access_allowed,                 # ★ 외부 서비스는 이 값으로 접근 허용을 판단
current_period_start/end, next_billing_at, card, retry_count
```
`access_allowed` = `status ∈ {TRIAL, ACTIVE, PAST_DUE, CANCELED}` → true,
`{SUSPENDED, EXPIRED}` → false. 외부 서비스는 보통 사용자 접근을 막거나 허용할 때 이 값만 본다.

---

## 2. HTTP 진입 — 라우트와 인증

```python
# api/v1/subscriptions.py:33
@router.post("/subscriptions", status_code=201)
async def create_subscription(
    payload: SubscriptionCreateRequest,
    service: Service = Depends(payment_rate_limit),   # ← 인증 + 결제 레이트리밋
    db=..., toss=..., cipher=...):
    sub = await subscription_service.create_subscription(
        db, toss, cipher, service=service, plan_id=payload.plan_id,
        external_user_id=payload.external_user_id,
        customer_key=payload.customer_key, auth_key=payload.auth_key,
        trial=payload.trial)
    return await _to_response(db, sub)
```

라우트는 거의 비어 있다. 두 가지만 한다:
1. `Depends(payment_rate_limit)` — 이게 통과해야 본문이 실행된다. 내부적으로:
   - `authenticate_service`(문서 08): API키 해시 대조 → **IP 화이트리스트** → 분당 레이트리밋
     → 타임스탬프 윈도우 → **HMAC 서명 검증** → **nonce 1회용**. 통과하면 `Service`를 반환.
   - 그 위에 **결제 전용 추가 throttle**(`rate_limit_payment_per_minute`). 결제성 엔드포인트는
     일반보다 더 빡빡하게 막는다.
2. 검증된 `service`와 파싱된 `payload`로 도메인 함수 호출 → 결과를 `SubscriptionResponse`로.

> 즉 "인증된 서비스만, 정해진 IP에서, 한도 내에서, 위변조 없이" 요청이 도메인 로직에 도달한다.

---

## 3. 도메인 로직 — `create_subscription` (`services/subscriptions.py:137`)

이 함수가 구독 생성의 실체다. 크게 **(A) 검증·준비 → (B) 빌링키 발급 → (C) 구독 INSERT +
PENDING 선커밋 → (D) 실제 결제 → 결과 확정**의 5막으로 읽으면 된다.

### (A) 검증 · 준비

```python
_validate_inputs(customer_key, external_user_id)        # 형식 검증(정규식/길이)

plan = await db.get(Plan, plan_id)
if plan is None or plan.service_id != service.id or plan.status != ACTIVE:
    raise NotFoundError("요금제를 찾을 수 없습니다")     # ① 내 서비스의 활성 요금제만

if trial and not (plan.trial_enabled and plan.trial_days >= 1):
    raise InputValidationError("이 요금제는 체험을 제공하지 않습니다")  # ② 체험 가능 여부

if await get_open_subscription(service_id, external_user_id):
    raise ConflictError("이미 구독이 존재합니다")        # ③ 1인 1구독 규칙(앱 레벨 사전 체크)

is_first = await _is_first_subscription(service_id, external_user_id)  # ④ 첫구독 판정
amount = 0 if trial else (plan_first_amount(plan) if is_first
                          else plan_recurring_amount(plan))            # ⑤ 금액 결정
```

- **①** 요금제는 반드시 **이 서비스 소유 + ACTIVE**여야 한다. 남의 요금제·보관된 요금제 차단.
- **③ 1인 1구독**: 같은 (서비스, 사용자)에 "열린" 구독(EXPIRED 제외)이 있으면 거부. 여기는
  친절한 사전 체크이고, 최종 방어선은 (C)의 DB 부분 유니크 인덱스다.
- **④ 첫구독 판정** `_is_first_subscription`: "혜택을 소진한 과거 구독"이 없으면 첫구독.
  혜택 소진 = (a) DONE 결제가 있던 구독 또는 (b) 결제 시도 자체가 없던 구독(무료/100% 할인).
  → **첫 결제가 실패해 즉시 만료된 구독은 FAILED 결제만 있어** 혜택 미소진으로 보고, 재시도 시
  첫구독 할인을 유지한다. 무료 첫구독은 (b)에 걸려 재구독 시 무료가 반복되지 않는다.
- **⑤ 금액**(문서 03의 헬퍼 사용):
  - `trial`이면 **0원**(가입 시 결제 안 함).
  - 첫구독이면 `plan_first_amount`(첫구독 할인가).
  - 재구독이면 `plan_recurring_amount`(상시 할인가).

### (B) 빌링키 발급 (토스 호출 #1)

```python
try:
    bk = await toss.issue_billing_key(auth_key, customer_key)
except TossError as exc:
    raise PaymentFailedError(f"빌링키 발급 실패: {exc.message}", code=exc.code)
```
`authKey`(1회용)를 **영구 빌링키**(`billingKey`)로 교환한다. 카드 정보(`bk.card`)도 같이 받는다.
**체험이어도 빌링키는 필수** — 체험 만료 후 자동결제하려면 결제수단이 미리 등록돼 있어야 하기 때문.
발급 실패면 여기서 끝(구독 생성 안 함).

### (C) 구독 INSERT + PENDING 선커밋 (가장 중요한 안전장치)

```python
now = utcnow()
if trial:
    period_end = now + timedelta(days=plan.trial_days);  status = TRIAL
else:
    period_end = compute_period_end(now, plan.billing_cycle, plan.cycle_days);  status = ACTIVE

sub = Subscription(..., billing_key_encrypted=cipher.encrypt(bk.billing_key),
                   billing_key_hash=sha256_hex(bk.billing_key), card_info=bk.card,
                   status=status, current_period_start=now, current_period_end=period_end,
                   next_billing_at=period_end)        # 체험: 만료 시점 = 첫 정기 결제일
db.add(sub)
try:
    await db.flush()                                   # INSERT 시도 → sub.id 확보
except IntegrityError:                                 # 동시 요청 경쟁
    await db.rollback()
    await safe_delete_billing_key(toss, bk.billing_key)  # 방금 만든 키 정리
    raise ConflictError("이미 구독이 존재합니다")

payment = None
if amount > 0:                                         # 체험/무료/100%할인이면 결제행 없음
    payment = Payment(subscription_id=sub.id, order_id=new_order_id("f"),
                      amount=amount, payment_type=FIRST, status=PENDING,
                      idempotency_key=f"first-{sub.id}", requested_at=now,
                      kind=PaymentKind.SUBSCRIPTION,   # ★ 구독 결제 구분자
                      service_id=service.id,           # ★ 서비스 직접 참조
                      external_user_id=external_user_id)  # ★ 사용자 식별자
    db.add(payment)
await record_audit("subscription.create", actor_service_id=service.id, detail={...})
await db.commit()        # ★ 결제 전에 먼저 커밋한다
```

여기서 두 가지를 꼭 이해해야 한다:

- **빌링키는 암호화해서 저장**(`cipher.encrypt`)하고, 조회용 해시(`billing_key_hash`)도 둔다.
  카드 정보는 표시용(`card_info`).
- **`Payment`에 `kind`, `service_id`, `external_user_id` 설정**: 마이그레이션 `c3d4e5f6a7b8`
  이후 단건 결제(문서 11)와 구독 결제를 `kind` 컬럼으로 구분한다. 구독 결제는
  `kind=SUBSCRIPTION`, `service_id=service.id`, `external_user_id=external_user_id`를
  함께 저장해 `subscription_id` 없이도 서비스·사용자를 직접 조회할 수 있다.
- **1인 1구독의 진짜 방어선**: `flush()`가 DB의 **부분 유니크 인덱스**
  (`uq_subscriptions_one_per_user`, status가 열린 상태일 때만 적용)에 걸리면 `IntegrityError`.
  동시에 두 요청이 (A)의 사전 체크를 모두 통과해도 DB가 한쪽만 통과시킨다 → 다른 쪽은 롤백 +
  방금 발급한 빌링키 정리 + 409.
- **★ 결제 전 커밋(`db.commit()`)**: 실제 토스 결제를 호출하기 **전에** "구독 + PENDING 결제"를
  먼저 DB에 영구 저장한다. 왜?
  - 결제 직후 서버가 죽어도 **PENDING 기록이 남아** 나중에 정산 스윕(문서 07)이 결과를 확정할 수 있다.
  - 구독 슬롯(유니크)을 선점해 **중복 결제·중복 구독**을 막는다.
  - `idempotency_key=f"first-{sub.id}"`가 결정적이라, 같은 시도를 다시 보내도 토스가 멱등 처리한다.

### (D) 실제 결제 (토스 호출 #2) — 세 가지 결말

`amount > 0`일 때만(체험/무료는 결제 없이 바로 활성). `resolve_charge`로 호출한다.

```python
result = await resolve_charge(toss, billing_key=..., amount=amount,
                              order_id=payment.order_id, idempotency_key=payment.idempotency_key)
```

`resolve_charge`(`subscriptions.py:69`)의 정책: **결과는 셋 중 하나로만 수렴**한다.
```
charge() 성공            → ChargeResult (승인 확정)
charge() TossError       → 확정 실패(카드 거절 등)
charge() TossTimeoutError → 결과 불명 → orderId로 재조회 → DONE이면 성공, 아니면 여전히 불명
```

도메인 함수는 이 셋을 다음과 같이 처리한다:

**① 성공(DONE)**
```python
payment.status = DONE; payment.toss_payment_key = result.payment_key
payment.approved_at = utcnow(); payment.raw_response = result.raw
await db.commit()
# sub.status는 ACTIVE(또는 trial이면 TRIAL) 그대로 — 정상 구독
```

**② 확정 실패 — `TossError`(카드 거절 등)**
```python
deleted = await safe_delete_billing_key(toss, bk.billing_key)  # 키 정리 먼저
payment.status = FAILED; payment.failure_code/message = ...
sub.status = EXPIRED            # 첫 결제 실패 → 즉시 종료(재구독으로 다시 시도 가능)
sub.next_billing_at = None
if deleted: sub.billing_key_encrypted = None   # 삭제 성공 시에만 암호문 제거
await db.commit()
raise PaymentFailedError(f"첫 결제 실패: ...", code=exc.code)   # → 외부에 4xx
```
첫 결제 실패는 구독을 **EXPIRED로 즉시 종료**한다(미완성 구독을 남기지 않음). `_is_first_subscription`
규칙상 FAILED만 있는 구독은 "혜택 미소진"이라, 사용자가 다시 구독하면 첫구독 할인을 또 받는다.

**③ 결과 불명 — `TossTimeoutError`(절대 실패로 단정하지 않음)**
```python
await record_audit("subscription.first_payment_unresolved", ...)
await db.commit()
raise PaymentFailedError(PENDING_GRACE_MESSAGE, code="PAYMENT_UNRESOLVED", http_status=503)
```
타임아웃은 "돈이 빠졌는지 안 빠졌는지 모름"이다. 여기서 FAILED로 처리하면 **실제로는 승인된
결제를 놓치거나, 재시도 시 이중 청구**가 날 수 있다. 그래서:
- 결제는 **PENDING 유지**, 구독 슬롯도 유지(점유).
- 외부에는 **503 + "잠시 후 조회하세요"** 안내.
- 나중에 **갱신 배치의 PENDING 정산 스윕**(문서 07)이 토스에 재조회해 DONE/FAILED를 확정한다.

---

## 4. 체험(trial) 경로 요약

`trial=True`면 (C)까지만 일어나고 (D)는 없다:
- `amount=0` → PENDING 결제행을 만들지 않음.
- `status=TRIAL`, `current_period_end = now + 체험일수`, `next_billing_at = 체험 만료 시점`.
- 빌링키는 등록됨.
- → 가입 시 청구 0원. 체험 만료가 오면 **갱신 배치(문서 05)** 가 `next_billing_at <= now`인
  이 구독을 잡아 **상시 할인가(`plan_recurring_amount`)로 첫 자동결제** → ACTIVE 전환.

---

## 5. 전체 시퀀스

```
[외부 서버] POST /api/v1/subscriptions  (HMAC 헤더 + payload)
   │
   ▼ payment_rate_limit → authenticate_service
   │   API키 해시 / IP / 레이트리밋 / 타임스탬프 / HMAC 서명 / nonce  → Service 확정
   ▼ create_subscription (도메인)
   ├─(A) 입력검증 · 요금제(소유+ACTIVE) · 체험가능 · 1인1구독 사전체크 · 첫구독판정 · 금액결정
   ├─(B) 토스 issue_billing_key                         (실패→PaymentFailedError)
   ├─(C) Subscription INSERT(flush, 유니크 경쟁→409+키정리)
   │     amount>0면 Payment(PENDING) 생성, 감사기록
   │     ★ commit  (결제 전 내구성 선점)
   └─(D) amount>0면 resolve_charge:
          ├ DONE        → Payment=DONE, 구독 ACTIVE, commit → 201
          ├ TossError   → Payment=FAILED, 구독 EXPIRED, 키삭제, commit → 4xx
          └ Timeout     → PENDING 유지, commit, 503 (정산 스윕이 추후 확정)
   ▼
[외부 서버] 201 + SubscriptionResponse(status, access_allowed, ...)
   (체험이면 (D) 생략 — TRIAL로 즉시 201)
```

---

## 6. 다른 외부 API 엔드포인트(같은 파일)

구독 생성 외에 `api/v1/subscriptions.py`가 제공하는 것(상세 로직은 문서 06):
- `GET /subscriptions/{external_user_id}` — 최신 구독 조회(`get_latest_subscription`). `access_allowed` 확인용.
- `POST /subscriptions/{id}/cancel` — 해지 예약(만료일까지 유지).
- `POST /subscriptions/{id}/resume` — 해지 철회.
- `POST /subscriptions/{id}/change-card` — 카드(빌링키) 교체. 결제성 → `payment_rate_limit`.
- `POST /subscriptions/{id}/pay` — 정지(SUSPENDED) 구독 수동 결제 → 복구.

조회/취소/재개는 `authenticate_service`만, 결제가 일어나는 생성/카드변경/수동결제는
`payment_rate_limit`(추가 throttle)을 쓴다.

---

## 7. 예외 · 안전장치 정리

| 상황 | 처리 | 위치 |
|---|---|---|
| 금액 조작 시도 | 불가 — 요청에 금액 없음, 서버가 plan에서 계산 | 스키마/도메인 |
| 남의/보관된 요금제로 구독 | 404 | (A)① |
| 체험 미지원 요금제에 trial | 검증 에러 | (A)② |
| 1인 1구독 위반(순차) | 409 사전 체크 | (A)③ |
| 1인 1구독 위반(동시) | DB 부분 유니크 → IntegrityError → 409 + 빌링키 정리 | (C) |
| 빌링키 발급 실패 | `PaymentFailedError`(구독 생성 안 함) | (B) |
| 카드 거절 등 결제 실패 | 구독 EXPIRED, 키 삭제, 4xx | (D)② |
| 토스 타임아웃(결과 불명) | PENDING 유지, 503, 정산 스윕이 확정 | (D)③ |
| 결제 직후 서버 다운 | PENDING 기록 보존 → 다음 배치가 확정 | (C) 선커밋 |
| 중복 결제 | 결정적 order_id/idempotency_key로 토스 멱등 | (C)(D) |

**핵심 원칙 3가지**(이 시스템 전체에 반복):
1. **금액은 서버가 계산**(클라이언트 입력 불신).
2. **결제 전에 PENDING을 커밋**(크래시·타임아웃에도 추적 가능).
3. **타임아웃 ≠ 실패**(결과 불명으로 다뤄 이중결제/누락 방지).

---

## 8. 관련 테스트

- `tests/integration/test_subscription_create.py` — 정가/첫구독 할인/재구독, 체험(결제 0원),
  카드 거절→EXPIRED, 타임아웃→PENDING/503, 동시 생성 1인1구독, `actor_service_id` 기록 등.
- `tests/integration/test_trial_and_manual.py` — 체험 생성·만료 전환·수동 결제.
- `tests/e2e/test_full_flow.py` — 서비스 등록→요금제→**HMAC API로 구독 생성**→갱신까지 전 구간.
- `tests/security/*` — HMAC/IP/nonce/레이트리밋(문서 08).

---

## 9. 단건 결제(연결 문서)

구독 없이 1회성 결제가 필요하면 `POST /api/v1/payments`를 사용한다.
단건 결제는 plan 없이 요청값 `amount`를 그대로 쓰고, 빌링키를 보관하지 않으며,
`Payment.kind=ONE_OFF` / `subscription_id=NULL`로 기록된다.
상세는 [11-one-off-payment.md](11-one-off-payment.md) 참고.

---

## 10. 유지보수 체크리스트

1. **결제 흐름 수정은 극도로 조심**. "결제 전 커밋 → 토스 호출 → 결과 확정" 순서와
   "타임아웃은 PENDING 유지"를 절대 깨지 말 것. 깨면 이중결제/유실 위험.
2. **새 금액 규칙**은 `billing_math.py`에서만(문서 03). 여기서 직접 금액 계산하지 말 것.
3. **요청에 금액·상태 같은 필드를 추가하지 말 것**(서버 권위 유지). 새 입력이 필요하면
   `SubscriptionCreateRequest`에 추가하되 결제 금액에는 영향 주지 않게.
4. **새 결제성 엔드포인트**는 `Depends(payment_rate_limit)`로(일반 조회는 `authenticate_service`).
5. **토스 응답 신뢰 경계**: 2xx인데 본문 파싱 실패도 `TossTimeoutError`로 매핑돼 있다
   (결과 불명 취급) — 새 토스 호출을 추가할 때 이 규약을 따를 것.
6. 빌링키는 항상 **암호화 저장 + 실패 시 best-effort 삭제**(`safe_delete_billing_key`). 평문 보관 금지.
