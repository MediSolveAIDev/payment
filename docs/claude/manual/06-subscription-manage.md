# 06. 구독 취소 · 재개 · 카드 변경 · 수동 결제

> 04에서 만든 구독을, 사용자/외부 서비스가 **생애 도중에 조작**하는 동작들.
> 대부분 외부 API(`/api/v1`)로 들어오고, 강제 취소만 Admin에서 한다.
> 결제가 일어나는 동작(수동결제·카드변경)은 04·05의 "PENDING 선커밋 / 타임아웃=불명" 패턴을 그대로 따른다.
>
> 선행: [04-subscription-create.md](04), [05-renewals.md](05)(상태 전이·재시도).

---

## 0. 한눈에 보기

| 동작 | HTTP / 호출 | 라우트 | 서비스 함수 | 결제? |
|---|---|---|---|---|
| 해지 예약 | `POST /api/v1/subscriptions/{uid}/cancel` | `cancel_subscription`(api) | `cancel_subscription` | ✕ |
| 해지 철회(재개) | `POST /api/v1/subscriptions/{uid}/resume` | `resume_subscription`(api) | `resume_subscription` | ✕ |
| 카드 변경 | `POST /api/v1/subscriptions/{uid}/change-card` | `change_card`(api) | `change_card` | ✕(빌링키만) |
| 수동 결제 | `POST /api/v1/subscriptions/{uid}/pay` | `manual_pay`(api) | `manual_charge_subscription` | **○** |
| 강제 취소 | `POST /admin/subscriptions/{id}/force-cancel` | `subscription_force_cancel` | `force_cancel_subscription` | ✕ |

- `{uid}` = `external_user_id`(외부 서비스의 사용자 ID). 외부 API는 사용자 단위로 구독을 다룬다.
- **인증**: 조회/취소/재개는 `authenticate_service`, **결제성(카드변경·수동결제)** 은 `payment_rate_limit`(문서 04·08).
- **행위자 구분**: 서비스 함수들은 `actor_type`("SERVICE" 기본 / "USER")을 받아 감사로그에
  `actor_service_id` 또는 `actor_user_id`를 채운다(문서 10). 외부 API 경로는 SERVICE, Admin 경로는 USER.

관련 파일: `app/api/v1/subscriptions.py`(엔드포인트), `app/services/subscriptions.py`(로직),
`app/admin/routes/subscriptions.py`(강제취소).

---

## 1. 해지 예약 — `cancel_subscription`

```python
# services/subscriptions.py:252
sub = (TRIAL/ACTIVE/PAST_DUE 중 하나인 그 사용자의 구독)
if 없음:
    이미 CANCELED면 ConflictError("이미 취소된 구독입니다") 아니면 NotFoundError
was_trial = (status == TRIAL)
sub.status = CANCELED
sub.next_billing_at = None              # 자동결제 중지
if was_trial:
    sub.current_period_end = utcnow()   # 체험 취소는 '즉시 만료'
record_audit("subscription.cancel", detail={"trial": was_trial}); commit
```

핵심 개념:
- **해지 = 즉시 종료가 아니라 "예약"**. 일반 구독은 `CANCELED`가 돼도 `current_period_end`까지는
  접근이 유지된다(`CANCELED` ∈ `ACCESS_ALLOWED_STATUSES`, 문서 00). 기간이 지나면 갱신 배치의
  `_expire_canceled`가 EXPIRED로 종단 처리(문서 05).
- **자동결제만 끈다**(`next_billing_at=None`) — 더는 청구되지 않음.
- **체험 취소는 예외**: 받은 결제가 없으므로 `current_period_end=now`로 두어 **즉시 만료** 경로를 탄다.
- 이미 취소된 구독을 또 취소하면 친절히 409.

> "해지하면 환불되나?"가 아니라 "다음 결제를 막고 기간 끝까지 쓴다"가 이 시스템의 해지 의미다.

---

## 2. 해지 철회(재개) — `resume_subscription`

```python
# subscriptions.py:357
sub = (status == CANCELED 인 그 사용자의 구독)   # 없으면 NotFoundError
if sub.current_period_end <= now:
    raise ConflictError("만료된 구독은 재개할 수 없습니다")   # 이미 기간 끝남
if sub.retry_count > 0:                 # 취소 전에 미수가 있었으면
    sub.status = PAST_DUE; sub.next_billing_at = now    # 즉시 재시도 큐로
else:
    sub.status = ACTIVE; sub.next_billing_at = current_period_end   # 정상 복귀
record_audit("subscription.resume"); commit
```

- **기간이 남아있는 CANCELED만** 되살릴 수 있다(이미 EXPIRED면 새 구독을 만들어야 함).
- 미수(`retry_count > 0`) 이력이 있으면 `PAST_DUE`로 복귀시키고 `next_billing_at=now` → 다음 배치가
  곧바로 결제 재시도(문서 05 `_renew_one`). 미수가 없으면 그냥 `ACTIVE`로.
- 결제 없음(상태 복원만).

---

## 3. 카드 변경 — `change_card`

```python
# subscriptions.py:372  (결제성 → payment_rate_limit)
_validate_inputs(customer_key, external_user_id)
sub = get_open_subscription(...)            # 열린 구독(EXPIRED 제외). 없으면 NotFoundError
bk = await toss.issue_billing_key(auth_key, customer_key)   # 새 빌링키 발급(실패→PaymentFailedError)
old_key = decrypt(sub.billing_key_encrypted)                # 기존 키 백업
sub.billing_key_encrypted = encrypt(bk.billing_key)         # 새 키로 교체(암호화)
sub.billing_key_hash = sha256_hex(bk.billing_key)
sub.customer_key = customer_key
sub.card_info = bk.card
if sub.status == PAST_DUE:
    sub.next_billing_at = utcnow()          # 새 카드로 즉시 재시도
record_audit("subscription.change_card"); commit
if old_key:
    await safe_delete_billing_key(toss, old_key)   # 기존 키 best-effort 삭제(커밋 후)
```

- **여기서 결제는 일어나지 않는다** — 결제수단(빌링키)만 교체. 그래서 `change-card`는 결제성으로
  분류되지만 실제 청구는 없다(빌링키 발급 자체가 토스 호출이라 throttle 대상).
- **순서가 중요**: 새 키를 **먼저 저장(commit)** 한 뒤 기존 키를 삭제한다. 반대로 하면
  "기존 키는 지웠는데 새 키 저장 실패"로 결제수단이 사라질 수 있다. 삭제는 실패해도 무방(best-effort).
- **PAST_DUE(미수) 상태에서 카드 변경 시 즉시 재시도**(`next_billing_at=now`) → 새 카드로 바로 회수 시도.
- `CANCELED` 구독에도 허용(만료 전 재개를 위한 카드 갱신). 이때 `next_billing_at`은 None 유지라 과금 안 됨.

---

## 4. 수동 결제 — `manual_charge_subscription` (결제 발생)

정지(`SUSPENDED`) 또는 결제 실패중(`PAST_DUE`)인 구독을 사용자가 **직접 결제해 되살리는** 동작.
`SUSPENDED`는 자동 재시도가 소진돼 정지된 상태, `PAST_DUE`는 자동 재시도 진행 중(실패중)인
상태다. 두 경우 모두 이 엔드포인트로 사람이 직접 결제할 수 있다.

```python
# subscriptions.py:301  (결제성 → payment_rate_limit)
# SUSPENDED(정지) 또는 PAST_DUE(실패중) 구독을 허용한다.
sub = await db.scalar(select(Subscription).where(
    Subscription.service_id == service.id,
    Subscription.external_user_id == external_user_id,
    Subscription.status.in_((SubscriptionStatus.SUSPENDED,
                             SubscriptionStatus.PAST_DUE))))
if sub is None:
    raise NotFoundError("정지/미수 상태의 구독을 찾을 수 없습니다")  # 없으면 NotFoundError
if sub.billing_key_encrypted is None:
    raise PaymentFailedError("등록된 결제수단이 없습니다...", code="NO_BILLING_KEY")
plan = ...; amount = plan_recurring_amount(plan)     # 상시 할인가
order_id = new_order_id("m")                          # manual 접두사
payment = Payment(..., payment_type=RETRY, status=PENDING, idempotency_key=f"manual-{order_id}",
                  kind=PaymentKind.SUBSCRIPTION,      # ★ 구독 결제 종류 명시
                  service_id=service.id,              # ★ 서비스 추적용
                  external_user_id=external_user_id)  # ★ 사용자 추적용
db.add(payment); await db.commit()                    # ★ 결제 전 PENDING 선커밋(04 원칙)

result = await resolve_charge(toss, ...)               # 결제 시도
  ├ TossTimeoutError → manual_pay_unresolved 기록, 503 (PENDING 유지, 정산 스윕이 확정)
  ├ TossError        → Payment=FAILED, manual_pay_failed 기록, 4xx (구독은 SUSPENDED 유지)
  └ 성공:
       payment=DONE
       sub.status = ACTIVE                             # 정지 해제, 복귀
       sub.current_period_start = now                  # ★ 기준일을 '결제 시점'으로 리셋
       sub.current_period_end = compute_period_end(now, cycle, cycle_days)
       sub.next_billing_at = current_period_end
       sub.retry_count = 0; sub.suspended_at = None
       record_audit("subscription.manual_pay"); commit
```

- **SUSPENDED 또는 PAST_DUE에서만** 동작한다.
  EXPIRED가 되면 빌링키가 삭제돼 수동결제 불가 → 새 구독 필요.
- 결제 실패는 04·05와 동일한 3분기(성공/TossError/타임아웃).
  실패해도 **구독은 원래 상태(SUSPENDED 또는 PAST_DUE) 유지**
  (자동 재시도와 달리 추가 재시도 예약을 하지 않음 — 사용자가 다시 시도).
- **성공 시 기준일 리셋**: 자동 갱신의 `_advance_period`(직전 종료일부터 이어붙임)와 달리,
  수동 결제는 **결제한 시점부터 새 주기**가 시작된다(정지로 끊겼던 기간을 보상하지 않음).

---

## 5. 강제 취소 — `force_cancel_subscription` (Admin)

운영자가 Admin 화면에서 구독을 강제로 취소(`POST /admin/subscriptions/{id}/force-cancel`).

```python
# subscriptions.py:408
sub = db.get(Subscription, subscription_id)
if 없음 or (service_scope is not None and sub.service_id not in service_scope):
    raise NotFoundError                       # 스코프 밖이면 없는 것처럼
if sub.status not in (ACTIVE, PAST_DUE):
    raise ConflictError("취소할 수 없는 상태입니다")
sub.status = CANCELED; sub.next_billing_at = None
record_audit("subscription.force_cancel", actor_type="USER", actor_user_id=...)
```

- 라우트(`admin/routes/subscriptions.py`)는 `require_any` + `validate_csrf` 후 호출, `service_scope`로
  **SERVICE_MANAGER는 담당 서비스만** 취소 가능(문서 02 스코프).
- 외부 API의 `cancel`과 결과는 같지만(CANCELED 예약) **행위자가 USER**(관리자)로 기록되고,
  `ACTIVE/PAST_DUE`에서만 허용한다(체험 즉시만료 같은 분기는 없음).

---

## 6. 상태 전이 요약(이 문서가 일으키는 전이)

```
TRIAL/ACTIVE/PAST_DUE ──cancel──▶ CANCELED ──resume(기간 남음)──▶ ACTIVE 또는 PAST_DUE
                                     │
                                     └─기간 종료(배치)──▶ EXPIRED   (문서 05)
ACTIVE/PAST_DUE ──force-cancel(admin)──▶ CANCELED
SUSPENDED/PAST_DUE ──manual_pay 성공──▶ ACTIVE(기준일 리셋)
열린 구독 ──change-card──▶ (상태 유지, 빌링키 교체; PAST_DUE면 즉시 재시도 예약)
ACTIVE(auto_renew=False, next_billing=None) ──기간 종료(배치)──▶ EXPIRED  (문서 05·13)
```

> **`auto_renew=False` 구독과 이 문서의 관계**: `cancel_subscription`과 달리 "자동결제 안함"
> 구독은 취소 없이도 기간 종료 시 EXPIRED로 이어진다. `next_billing_at=None`으로 생성(구독 생성
> 시 `plan.auto_renew=False`면 자동 설정)되며, 갱신 배치의 `_expire_non_renewing`이 처리한다.
> 이 구독은 자동결제를 예약하지 않으므로 `resume_subscription`이나 `cancel_subscription`을
> 호출해도 동작이 달라지지 않는다(next_billing_at=None 유지). 자세한 배치 경로는 문서 05 참조.

접근 허용(`access_allowed`) 관점: CANCELED·PAST_DUE는 **여전히 접근 허용**(기간 내),
SUSPENDED·EXPIRED는 차단. 외부 서비스는 응답의 `access_allowed`만 보면 된다.

---

## 7. 예외 · 안전장치

| 상황 | 처리 | 위치 |
|---|---|---|
| 이미 취소된 구독 재취소 | 409 "이미 취소된 구독입니다" | cancel |
| 체험 취소 | 즉시 만료 경로(period_end=now) | cancel |
| 만료된 구독 재개 | 409 "만료된 구독은 재개할 수 없습니다" | resume |
| 카드 변경 중 빌링키 발급 실패 | `PaymentFailedError`(기존 키 유지) | change_card |
| 카드 교체 순서 | 새 키 저장(commit) → 기존 키 삭제(best-effort) | change_card |
| 수동결제 대상이 SUSPENDED/PAST_DUE 아님/결제수단 없음 | 404 `"정지/미수 상태의 구독을 찾을 수 없습니다"` / `NO_BILLING_KEY` | manual_charge |
| 수동결제 타임아웃 | PENDING 유지, 503, 정산 스윕(문서 07) | manual_charge |
| 강제취소 스코프 밖 | 404(없는 것처럼) | force_cancel |
| 강제취소 불가 상태 | 409 | force_cancel |

결제성 동작(수동결제)은 04·05와 **완전히 같은 3원칙**(서버 금액 계산, PENDING 선커밋, 타임아웃=불명)을 따른다.

---

## 8. 관련 테스트

- `tests/integration/test_subscription_manage.py` — cancel/resume/change_card, 상태 가드, 미수 재개,
  카드 교체 순서/기존키 삭제, 강제취소 스코프.
- `tests/integration/test_trial_and_manual.py` — 체험 취소 즉시만료, 수동결제 복구/기준일 리셋,
  수동결제 실패/타임아웃.
- `tests/e2e/test_admin_operations.py` — Admin 강제취소(행위자 USER 기록).

---

## 9. 유지보수 체크리스트

1. **결제성 동작(manual_pay, 향후 추가분)** 은 반드시 04·05의 3원칙을 따를 것
   (PENDING 선커밋 → resolve_charge → 타임아웃은 PENDING 유지).
2. **금액은 `plan_recurring_amount`**(문서 03). 수동결제/카드변경에서 임의 금액 받지 말 것.
3. **빌링키 교체는 "새 키 저장 후 기존 키 삭제" 순서** 고정. 삭제는 best-effort(`safe_delete_billing_key`).
4. **행위자 구분**(`actor_type`): 외부 API 경로=SERVICE, Admin 경로=USER. 감사로그 추적성을 위해
   새 호출 경로를 추가할 때 올바른 `actor_type`/`actor_service_id`/`actor_user_id`를 넘길 것(문서 10).
5. **상태 가드를 명시적으로**: 각 동작이 허용하는 시작 상태(cancel=TRIAL/ACTIVE/PAST_DUE,
   resume=CANCELED+기간남음, **manual_pay=SUSPENDED 또는 PAST_DUE** 등)를 함수 안에서 확인. 새 상태 추가 시 재검토.
6. **자동 갱신과의 경계**: 수동결제가 성공해 ACTIVE가 되면 갱신 배치가 다시 관리한다 —
   `next_billing_at`을 올바로 세팅해야 다음 자동결제가 정상 작동(문서 05).
