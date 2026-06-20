# 06. 구독 취소·재개·수동결제·카드변경

> **상호참조**: 구독 생성 → [04](04-subscription-create.md) | 자동 갱신·배치 → [05](05-subscription-renewal.md) | HMAC 인증 → [03](03-auth-and-security.md) | 테이블 구조 → [02](02-database.md)

---

## 한 줄 요약

이미 존재하는 구독을 **취소 예약·재개·수동결제·카드 교체**하는 4가지 라이프사이클 동작을 다룬다.  
외부 서비스(API)와 어드민 화면(강제 취소) 두 진입점이 있다.

---

## 1. 구독 취소 (cancel)

### 1-1. 진입점

| 항목 | 내용 |
|---|---|
| **외부 API 엔드포인트** | `POST /api/v1/subscriptions/{external_user_id}/cancel` |
| **라우트 함수** | `app/api/v1/subscriptions.py:101` — `cancel_subscription` |
| **인증** | `authenticate_service` — HMAC 3중 인증, 결제 API 호출 없음 |
| **서비스 함수** | `app/services/subscriptions.py:278` — `cancel_subscription` |

### 1-2. 처리 흐름

```
POST /api/v1/subscriptions/{external_user_id}/cancel
  └─ authenticate_service (app/api/deps.py:77)
       ├─ 킬스위치 확인 → 서비스 조회 → IP 검사 → 레이트 리밋 → 타임스탬프 → HMAC → nonce
  └─ cancel_subscription (app/services/subscriptions.py:278)
       ├─ 1) status IN (TRIAL, ACTIVE, PAST_DUE) 구독 SELECT
       ├─ 2) 이미 CANCELED이면 ConflictError("이미 취소된 구독입니다")
       ├─ 3) 구독 없으면 NotFoundError("구독을 찾을 수 없습니다")
       ├─ 4) sub.status = CANCELED
       ├─ 5) sub.next_billing_at = None  ← 자동 갱신 차단
       ├─ 6) TRIAL이면 sub.current_period_end = utcnow()  ← 즉시 만료 예약
       ├─ 7) record_audit(action="subscription.cancel")
       └─ 8) db.commit()
```

### 1-3. DB 변경

| 테이블 | 컬럼 | 변경 내용 |
|---|---|---|
| `subscriptions` | `status` | `ACTIVE`·`TRIAL`·`PAST_DUE` → `CANCELED` |
| `subscriptions` | `next_billing_at` | `NULL` (자동 갱신 중지) |
| `subscriptions` | `current_period_end` | TRIAL 취소 시에만 → `utcnow()` (즉시 만료) |
| `audit_logs` | `action` | `"subscription.cancel"` |

### 1-4. 상태 전이

```
ACTIVE  ──→ CANCELED
TRIAL   ──→ CANCELED (current_period_end = now, 체험 기간 즉시 소멸)
PAST_DUE ──→ CANCELED (next_billing_at=None → 재시도 중단)
```

취소는 **즉시 삭제가 아니다**. `current_period_end`까지 서비스 접근이 유지된다(`access_allowed=true`).  
배치가 `current_period_end` 도래 후 `EXPIRED`로 전환한다(05 문서 참조).

### 1-5. 왜 즉시 종료가 아닌가?

취소 시 `status`를 `CANCELED`로만 바꾸고 `current_period_end`를 그대로 두는 이유:  
사용자가 이미 결제한 기간에 대해 환불 없이 서비스를 계속 이용할 권리가 있기 때문이다.  
`next_billing_at = None`으로 다음 자동결제만 막고, 만료일이 되면 배치(`renewals.py`)가 EXPIRED 처리한다.  
TRIAL은 첫 결제가 아직 일어나지 않았으므로 즉시 만료해도 환불 문제가 없다.

---

## 2. 어드민 강제 취소 (force_cancel)

### 2-1. 진입점

| 항목 | 내용 |
|---|---|
| **어드민 엔드포인트** | `POST /admin/subscriptions/{sub_id}/force-cancel` |
| **라우트 함수** | `app/admin/routes/subscriptions.py:164` — `subscription_force_cancel` |
| **인증** | `require_any` — 세션 쿠키 + CSRF 토큰 |
| **서비스 함수** | `app/services/subscriptions.py:460` — `force_cancel_subscription` |

### 2-2. 처리 흐름

```
POST /admin/subscriptions/{sub_id}/force-cancel
  └─ require_any (app/admin/deps.py:102) — 세션 확인 (SYSTEM_ADMIN 또는 SERVICE_MANAGER)
  └─ validate_csrf (app/admin/deps.py:105) — CSRF 토큰 검증
  └─ force_cancel_subscription (app/services/subscriptions.py:460)
       ├─ 1) sub = db.get(Subscription, subscription_id)
       ├─ 2) service_scope 검사:
       │       service_scope=None   → SYSTEM_ADMIN → 모든 구독 가능
       │       service_scope=[...] → SERVICE_MANAGER → 목록 밖이면 NotFoundError
       ├─ 3) status NOT IN (ACTIVE, PAST_DUE) → ConflictError("취소할 수 없는 상태입니다")
       ├─ 4) sub.status = CANCELED
       ├─ 5) sub.next_billing_at = None
       ├─ 6) record_audit(actor_type="USER", action="subscription.force_cancel")
       └─ 7) db.commit()
  └─ saved_redirect("/admin/subscriptions/{sub_id}", "구독이 해지되었습니다")
```

### 2-3. service_scope 규칙

`service_scope`는 `app/admin/deps.py:113`의 `service_scope(ctx)` 함수가 반환한다.  
- `SYSTEM_ADMIN` → `ctx.service_ids = None` → `service_scope = None` → 전체 구독 접근  
- `SERVICE_MANAGER` → `ctx.service_ids = [uuid1, uuid2, ...]` → 담당 서비스 구독만 접근

강제 취소 허용 상태: `ACTIVE`, `PAST_DUE`만 허용 (`TRIAL`, `CANCELED`, `SUSPENDED`, `EXPIRED` 불가).

### 2-4. DB 변경

외부 API 취소와 동일하되 감사 로그 행위자가 다르다.

| 테이블 | 컬럼 | 변경 |
|---|---|---|
| `subscriptions` | `status` | → `CANCELED` |
| `subscriptions` | `next_billing_at` | → `NULL` |
| `audit_logs` | `actor_type` | `"USER"` (관리자) |
| `audit_logs` | `actor_user_id` | 로그인한 관리자 UUID |
| `audit_logs` | `action` | `"subscription.force_cancel"` |

---

## 3. 구독 재개 (resume)

### 3-1. 진입점

| 항목 | 내용 |
|---|---|
| **외부 API 엔드포인트** | `POST /api/v1/subscriptions/{external_user_id}/resume` |
| **라우트 함수** | `app/api/v1/subscriptions.py:115` — `resume_subscription` |
| **인증** | `authenticate_service` — 결제 API 호출 없음 |
| **서비스 함수** | `app/services/subscriptions.py:385` — `resume_subscription` |

### 3-2. 처리 흐름

```
POST /api/v1/subscriptions/{external_user_id}/resume
  └─ authenticate_service
  └─ resume_subscription (app/services/subscriptions.py:385)
       ├─ 1) status == CANCELED 구독 SELECT
       ├─ 2) 없으면 NotFoundError("취소된 구독이 없습니다")
       ├─ 3) current_period_end <= now → ConflictError("만료된 구독은 재개할 수 없습니다")
       ├─ 4a) retry_count > 0 (미수금 있음):
       │       sub.status = PAST_DUE
       │       sub.next_billing_at = now  ← 즉시 재시도 예약
       ├─ 4b) retry_count == 0 (정상):
       │       sub.status = ACTIVE
       │       sub.next_billing_at = sub.current_period_end
       │       plan.auto_renew == False → sub.next_billing_at = None  ← 갱신 없음
       ├─ 5) record_audit(action="subscription.resume")
       └─ 6) db.commit()
```

### 3-3. DB 변경

| 테이블 | 컬럼 | 변경 내용 |
|---|---|---|
| `subscriptions` | `status` | `CANCELED` → `ACTIVE` 또는 `PAST_DUE` |
| `subscriptions` | `next_billing_at` | 재개 경로에 따라 설정 (아래 표) |
| `audit_logs` | `action` | `"subscription.resume"` |

### 3-4. 상태 전이

| 조건 | 전이 | next_billing_at |
|---|---|---|
| `retry_count == 0` & `auto_renew=True` | `CANCELED → ACTIVE` | `current_period_end` |
| `retry_count == 0` & `auto_renew=False` | `CANCELED → ACTIVE` | `None` (만료 후 종료) |
| `retry_count > 0` | `CANCELED → PAST_DUE` | `now` (즉시 배치 재시도) |
| `current_period_end <= now` | 재개 불가 | — |

### 3-5. auto_renew=False 동작

`plan.auto_renew=False`인 요금제는 재개해도 자동 갱신을 예약하지 않는다.  
`next_billing_at = None`이 되어 기간 만료 후 배치가 EXPIRED로 전환한다.  
코드: `app/services/subscriptions.py:413`

---

## 4. 수동결제 (manual pay)

### 4-1. 진입점

| 항목 | 내용 |
|---|---|
| **외부 API 엔드포인트** | `POST /api/v1/subscriptions/{external_user_id}/pay` |
| **라우트 함수** | `app/api/v1/subscriptions.py:64` — `manual_pay` |
| **인증** | `payment_rate_limit` — HMAC 3중 인증 + 결제 전용 레이트 리밋 |
| **서비스 함수** | `app/services/subscriptions.py:307` — `manual_charge_subscription` |

### 4-2. 처리 흐름

```
POST /api/v1/subscriptions/{external_user_id}/pay
  └─ payment_rate_limit (app/api/deps.py:141)
       ├─ authenticate_service (HMAC 3중 인증)
       └─ 결제 전용 카운터(rlp:{service.id}:{window}) 확인
  └─ manual_charge_subscription (app/services/subscriptions.py:307)
       ├─ 1) status IN (SUSPENDED, PAST_DUE) 구독 SELECT
       ├─ 2) 없으면 NotFoundError("정지/미수 상태의 구독을 찾을 수 없습니다")
       ├─ 3) billing_key_encrypted IS NULL → PaymentFailedError(code="NO_BILLING_KEY")
       ├─ 4) amount = plan_recurring_amount(plan)  ← 상시 할인가
       ├─ 5) Payment(status=PENDING, type=RETRY, order_id="m{uuid}") 생성
       ├─ 6) db.commit()  ← 결제 전 내구성 확보
       ├─ 7) billing_key = cipher.decrypt(sub.billing_key_encrypted)
       ├─ 8) resolve_charge(toss, ...)  ← 토스 charge API 호출
       │     ├─ TossTimeoutError → 감사 로그 + db.commit() + PaymentFailedError(503)
       │     └─ TossError → payment=FAILED + 감사 로그 + db.commit() + PaymentFailedError(402)
       ├─ 9) 성공:
       │       payment.status = DONE
       │       payment.toss_payment_key = result.payment_key
       │       payment.approved_at = utcnow()
       │       sub.status = ACTIVE  ← SUSPENDED/PAST_DUE → ACTIVE
       │       sub.current_period_start = now
       │       sub.current_period_end = compute_period_end(now, ...)  ← 기준일 리셋
       │       sub.next_billing_at = sub.current_period_end
       │       sub.retry_count = 0
       │       sub.suspended_at = None
       ├─ 10) record_audit(action="subscription.manual_pay")
       └─ 11) db.commit()
```

### 4-3. DB 변경

| 테이블 | 컬럼 | 변경 내용 |
|---|---|---|
| `subscriptions` | `status` | `SUSPENDED` 또는 `PAST_DUE` → `ACTIVE` |
| `subscriptions` | `current_period_start` | 결제 시점으로 리셋 |
| `subscriptions` | `current_period_end` | `compute_period_end(now, ...)` |
| `subscriptions` | `next_billing_at` | `current_period_end` |
| `subscriptions` | `retry_count` | `0` |
| `subscriptions` | `suspended_at` | `NULL` |
| `payments` | 신규 행 삽입 | `kind=SUBSCRIPTION`, `payment_type=RETRY`, 결과 반영 |
| `audit_logs` | `action` | `"subscription.manual_pay"` 또는 실패 시 `"subscription.manual_pay_failed"` |

### 4-4. 기준일(Billing Anchor) 리셋이란?

수동결제 성공 시 `current_period_start`를 결제 시점으로 재설정한다.  
예: 매월 1일 과금이었다가 15일에 수동결제 성공 → 다음 자동결제는 다음 달 15일.  
이는 `app/services/subscriptions.py:373`에서 `compute_period_end(now, plan.billing_cycle, plan.cycle_days)`를 다시 계산하기 때문이다.

### 4-5. 허용 상태

`SUSPENDED`(자동결제 완전 중단)와 `PAST_DUE`(재시도 중) 두 상태 모두 허용한다.  
`ACTIVE`, `TRIAL`, `CANCELED`, `EXPIRED` 구독에 호출하면 NotFoundError(404).

---

## 5. 카드 변경 (change-card)

### 5-1. 진입점

| 항목 | 내용 |
|---|---|
| **외부 API 엔드포인트** | `POST /api/v1/subscriptions/{external_user_id}/change-card` |
| **요청 바디** | `CardChangeRequest` — `auth_key`, `customer_key` (`app/schemas/api.py:34`) |
| **라우트 함수** | `app/api/v1/subscriptions.py:130` — `change_card` |
| **인증** | `payment_rate_limit` — 새 빌링키 발급(토스 API 호출) 수반 |
| **서비스 함수** | `app/services/subscriptions.py:423` — `change_card` |

### 5-2. 처리 흐름

```
POST /api/v1/subscriptions/{external_user_id}/change-card
  Body: { "auth_key": "...", "customer_key": "..." }
  └─ payment_rate_limit
  └─ change_card (app/services/subscriptions.py:423)
       ├─ 1) _validate_inputs(customer_key, external_user_id)
       │       customer_key 형식 검사 (CUSTOMER_KEY_RE: [A-Za-z0-9\-_=.@]{2,300})
       ├─ 2) get_open_subscription(db, ...) — EXPIRED 제외 '열린' 구독 조회
       ├─ 3) 없으면 NotFoundError("구독을 찾을 수 없습니다")
       ├─ 4) toss.issue_billing_key(auth_key, customer_key)
       │       TossError → PaymentFailedError("빌링키 발급 실패")
       ├─ 5) old_key = cipher.decrypt(sub.billing_key_encrypted)  ← 기존 키 보관
       ├─ 6) sub.billing_key_encrypted = cipher.encrypt(bk.billing_key)  ← 새 키 저장
       ├─ 7) sub.billing_key_hash = sha256_hex(bk.billing_key)
       ├─ 8) sub.customer_key = customer_key
       ├─ 9) sub.card_info = bk.card
       ├─ 10) PAST_DUE 상태이면 sub.next_billing_at = utcnow()  ← 새 카드로 즉시 재시도
       ├─ 11) record_audit(action="subscription.change_card")
       ├─ 12) db.commit()  ← 새 키 저장 완료
       └─ 13) safe_delete_billing_key(toss, old_key)  ← 기존 키 삭제(베스트 에포트)
```

### 5-3. DB 변경

| 테이블 | 컬럼 | 변경 내용 |
|---|---|---|
| `subscriptions` | `billing_key_encrypted` | 새 빌링키 AES-GCM 암호문 |
| `subscriptions` | `billing_key_hash` | 새 빌링키 SHA-256 해시 |
| `subscriptions` | `customer_key` | 새 customer_key |
| `subscriptions` | `card_info` | 새 카드 마스킹 정보 (JSONB) |
| `subscriptions` | `next_billing_at` | PAST_DUE 상태일 때만 → `utcnow()` |
| `audit_logs` | `action` | `"subscription.change_card"` |

### 5-4. 기존 빌링키 삭제 — 베스트 에포트

기존 빌링키 삭제는 `safe_delete_billing_key(toss, old_key)` 로 `db.commit()` **이후**에 호출한다.  
삭제 실패해도 새 키는 이미 저장되어 구독이 정상 작동한다.  
실패 시 `WARN` 로그를 남기고 토스에 키가 잔존한다(운영자가 수동 정리 가능).  
코드: `app/services/payment_utils.py:24`

### 5-5. CANCELED 구독에도 허용

`change_card`는 `get_open_subscription`을 사용하므로 `CANCELED` 구독도 조회 대상에 포함된다.  
만료 전 재개를 위해 카드를 갱신할 용도로 허용한다.  
이 경우 `next_billing_at`은 None으로 유지되어 즉시 과금되지 않는다.

---

## 6. 인증 비교: authenticate_service vs payment_rate_limit

| 항목 | `authenticate_service` | `payment_rate_limit` |
|---|---|---|
| **사용 엔드포인트** | cancel, resume, GET | create, pay, change-card |
| **레이트 리밋 키** | `rl:{service_id}:{window}` | `rlp:{service_id}:{window}` |
| **한도 설정** | `settings.rate_limit_per_minute` | `settings.rate_limit_payment_per_minute` |
| **이유** | 상태만 변경, 토스 API 미호출 | 빌링키 발급·청구 = 외부 API 호출 포함 |
| **코드** | `app/api/deps.py:77` | `app/api/deps.py:141` |

`payment_rate_limit`은 내부적으로 `authenticate_service`를 `Depends`로 포함한다.  
따라서 결제성 엔드포인트는 일반 레이트 리밋 + 결제 전용 레이트 리밋을 **모두** 통과해야 한다.

---

## 7. 상태 전이 요약표

```
TRIAL ──(cancel)──────────────────────────────────→ CANCELED ──(기간만료)──→ EXPIRED
  │                                                      │
  └──(배치/next_billing_at 도래)──→ ACTIVE              └──(resume/기간 내)──→ ACTIVE
                                     │                                          │
                             (결제실패 반복)                             (resume/미수금 있음)
                                     ↓                                          ↓
                                  PAST_DUE ──(cancel)──→ CANCELED          PAST_DUE
                                     │    ←────────────────────(resume)────────┘
                                     │
                            (retry 한도 초과)
                                     ↓
                                 SUSPENDED ──(manual_pay 성공)──→ ACTIVE
                                           ──(기간만료/대기일 초과)──→ EXPIRED
```

| 현재 상태 | cancel | resume | manual_pay | change_card |
|---|---|---|---|---|
| TRIAL | O (즉시 만료) | X | X | O |
| ACTIVE | O | X | X | O |
| PAST_DUE | O | X | O | O (+ 즉시 재시도) |
| SUSPENDED | X (ConflictError) | X | O | O |
| CANCELED | X (ConflictError) | O (기간 내) | X | O |
| EXPIRED | X (NotFoundError) | X | X | X (NotFoundError) |

---

## 8. 예외·엣지 케이스

### 8-1. 404 — 구독 없음

| 동작 | 발생 조건 | 오류 메시지 |
|---|---|---|
| cancel | status가 TRIAL·ACTIVE·PAST_DUE 구독 없음 | `"구독을 찾을 수 없습니다"` |
| resume | status가 CANCELED 구독 없음 | `"취소된 구독이 없습니다"` |
| manual_pay | SUSPENDED·PAST_DUE 구독 없음 | `"정지/미수 상태의 구독을 찾을 수 없습니다"` |
| change_card | OPEN 상태 구독 없음 | `"구독을 찾을 수 없습니다"` |
| force_cancel | 구독 없음 또는 서비스 스코프 밖 | `"구독을 찾을 수 없습니다"` |

코드: `app/core/errors.py:31` — `NotFoundError` (HTTP 404)

### 8-2. 409 — 상태 충돌

| 동작 | 발생 조건 | 코드 |
|---|---|---|
| cancel | 이미 CANCELED | `"이미 취소된 구독입니다"` |
| resume | `current_period_end <= now` | `"만료된 구독은 재개할 수 없습니다"` |
| force_cancel | status가 ACTIVE·PAST_DUE 아님 | `"취소할 수 없는 상태입니다"` |

코드: `app/core/errors.py:38` — `ConflictError` (HTTP 409)

### 8-3. NO_BILLING_KEY — 빌링키 없음

수동결제 시 `billing_key_encrypted is None`이면 발생한다.  
`PaymentFailedError("등록된 결제수단이 없습니다. 카드를 다시 등록해주세요.", code="NO_BILLING_KEY")`  
코드: `app/services/subscriptions.py:326`  
해결: 먼저 `change-card`로 새 카드를 등록한 뒤 다시 시도한다.

### 8-4. 결제 실패 (TossError)

수동결제·카드변경의 빌링키 발급 시 토스가 4xx를 반환하면 `TossError`가 발생한다.  
서비스 레이어가 `PaymentFailedError`로 변환해 HTTP 402를 반환한다.  
수동결제 결제 실패 시 구독 상태는 `SUSPENDED`로 유지된다(복귀 없음).

### 8-5. 결제 결과 불명 (TossTimeoutError)

토스 API가 65초 이내 응답을 주지 않으면 `TossTimeoutError`가 발생한다.  
`resolve_charge`가 `order_id`로 재조회를 시도한다(`app/services/payment_utils.py:38`).  
- 재조회 성공(DONE) → 정상 처리
- 재조회 실패 또는 미확인 → `PaymentFailedError(code="PAYMENT_UNRESOLVED", http_status=503)`  
  구독·결제는 `PENDING` 상태로 유지(이중결제 방지). 배치 정산 스윕이 추후 처리.

### 8-6. 레이트 리밋 초과

`pay`, `change-card` 엔드포인트는 분당 결제 전용 한도(`rate_limit_payment_per_minute`)를 초과하면 HTTP 429.  
`cancel`, `resume`은 일반 한도(`rate_limit_per_minute`)만 적용된다.

### 8-7. 강제 취소 서비스 스코프

`SERVICE_MANAGER`가 자신의 담당 서비스 밖의 구독을 강제 취소하려 하면  
`force_cancel_subscription`이 `NotFoundError`(404)를 반환한다.  
403을 사용하지 않는 것은 구독 존재 여부를 노출하지 않기 위해서다.  
코드: `app/services/subscriptions.py:471`

---

## 9. 관련 테스트

### 9-1. tests/integration/test_subscription_manage.py

| 테스트 함수 | 검증 내용 |
|---|---|
| `test_cancel_active_subscription` (line 18) | ACTIVE → CANCELED, next_billing_at=None |
| `test_cancel_past_due_stops_retries` (line 27) | PAST_DUE → CANCELED, 재시도 중단 |
| `test_cancel_already_canceled_conflicts` (line 37) | 이중 취소 → ConflictError |
| `test_cancel_nonexistent_not_found` (line 46) | 없는 구독 → NotFoundError |
| `test_resume_before_period_end` (line 52) | CANCELED → ACTIVE, next_billing_at 복원 |
| `test_resume_no_auto_renew_keeps_no_next_billing` (line 63) | auto_renew=False 재개 → next_billing_at=None |
| `test_resume_canceled_past_due_resumes_retry` (line 75) | retry_count>0 → PAST_DUE + 즉시 재시도 |
| `test_resume_after_period_end_conflicts` (line 88) | 만료 후 재개 → ConflictError |
| `test_change_card` (line 100) | 정상 카드 변경, 기존 키 삭제 확인 |
| `test_change_card_on_past_due_schedules_immediate_retry` (line 112) | PAST_DUE + 카드변경 → next_billing_at=now |
| `test_change_card_issue_failure_keeps_old_key` (line 123) | 빌링키 발급 실패 → 기존 키 유지 |
| `test_change_card_survives_old_key_delete_failure` (line 150) | 기존 키 삭제 실패해도 새 키 저장 성공 |

### 9-2. tests/integration/test_trial_and_manual.py

| 테스트 함수 | 검증 내용 |
|---|---|
| `test_trial_cancel_is_immediate` (line 47) | TRIAL 취소 → current_period_end=now |
| `test_manual_pay_revives_suspended_and_resets_anchor` (line 61) | SUSPENDED 수동결제 → ACTIVE + 기준일 리셋 |
| `test_manual_pay_requires_suspended` (line 84) | ACTIVE 구독 → NotFoundError |
| `test_manual_pay_allows_past_due` (line 93) | PAST_DUE 수동결제 허용 (요청 012) |
| `test_manual_pay_failure_keeps_suspended` (line 105) | 결제 실패 → SUSPENDED 유지 |
| `test_recurring_discount_manual_pay_uses_discounted` (line 153) | 수동결제에 상시 할인가 적용 확인 |

### 9-3. 테스트 실행 방법

```bash
# 통합 테스트 — 구독 관리 전체
pytest tests/integration/test_subscription_manage.py -v

# 체험·수동결제 테스트
pytest tests/integration/test_trial_and_manual.py -v

# 두 파일 함께
pytest tests/integration/test_subscription_manage.py tests/integration/test_trial_and_manual.py -v
```

테스트는 `FakeTossClient`(`app/toss/fake.py`)를 사용해 실제 토스 API 호출 없이 실행된다.

---

## 10. 유지보수 팁

### 10-1. 수동결제 허용 상태를 바꾸려면

`app/services/subscriptions.py:318`의 `.in_((SubscriptionStatus.SUSPENDED, SubscriptionStatus.PAST_DUE))`에  
허용할 상태를 추가하거나 제거한다.  
예: TRIAL 상태에서도 수동결제를 허용하려면 `SubscriptionStatus.TRIAL`을 튜플에 추가한다.  
변경 후 `test_manual_pay_allows_past_due`를 참고해 테스트 케이스를 추가한다.

### 10-2. 카드 변경 흐름 디버깅

카드 변경 실패 패턴과 확인 위치:

| 실패 단계 | 현상 | 확인 위치 |
|---|---|---|
| 빌링키 발급 실패 | HTTP 402, 기존 키 유지 | `audit_logs` 없음 (commit 전 실패) |
| 기존 키 삭제 실패 | 구독은 정상, WARN 로그 | `app/services/payment_utils.py:33` 로그 |
| PAST_DUE + 변경 | next_billing_at이 미래로 안 당겨짐 | `subscriptions.next_billing_at` 확인 |

기존 키 삭제 실패 시 빌링키 해시(`billing_key_hash`)를 이용해 토스 어드민에서 수동 삭제 가능하다.

### 10-3. 취소가 즉시 종료가 아닌 이유

**비즈니스 설계 원칙**: 사용자는 이미 결제한 기간에 대한 서비스 이용 권리가 있다.  
`status=CANCELED` + `next_billing_at=None`으로 자동결제만 막고, `current_period_end`까지 접근을 허용한다.  
만료 처리는 배치(`app/services/renewals.py`)가 `_expire_canceled`로 담당한다(05 문서 참조).  
즉시 종료가 필요한 경우(예: 이용 약관 위반)는 별도 처리 로직이 필요하다—현재 구현에는 없다.

### 10-4. 재개 후 미수금 처리

`retry_count > 0`인 CANCELED 구독을 재개하면 `PAST_DUE`로 전환되고 `next_billing_at = now`가 된다.  
다음 배치 실행 시 `_renew_one`이 이 구독을 처리해 결제를 재시도한다.  
사용자에게 결제 실패 사유를 미리 안내하고 카드 변경(`change-card`)을 유도한 뒤 재개하는 것이 좋다.

### 10-5. 감사 로그 조회

모든 동작은 `audit_logs` 테이블에 기록된다. SQL로 특정 구독의 이력을 조회한다:

```sql
SELECT actor_type, action, detail, created_at
FROM audit_logs
WHERE target_type = 'subscription'
  AND target_id = '<구독 UUID>'
ORDER BY created_at DESC;
```

action 값:
- `subscription.cancel` — 외부 API 취소
- `subscription.force_cancel` — 어드민 강제 취소
- `subscription.resume` — 재개
- `subscription.manual_pay` — 수동결제 성공
- `subscription.manual_pay_failed` — 수동결제 실패
- `subscription.manual_pay_unresolved` — 수동결제 결과 불명
- `subscription.change_card` — 카드 변경

### 10-6. SUSPENDED 구독 수동결제 후 next_billing_at 확인

수동결제 성공 후 `next_billing_at`이 올바르게 설정됐는지 확인한다:

```sql
SELECT status, current_period_start, current_period_end, next_billing_at, retry_count
FROM subscriptions
WHERE id = '<구독 UUID>';
```

`status = 'ACTIVE'`, `retry_count = 0`, `next_billing_at = current_period_end`여야 정상이다.
