# 07. 결제 정합성 (PENDING 정산 · 토스 웹훅)

> 결제 흐름의 **마지막 안전망**. 04·05·06에서 "타임아웃=결과 불명"으로 PENDING에 남겨둔 결제를
> 여기서 토스에 재조회해 **확정**한다(정산 스윕). 또 토스가 보내는 웹훅으로 외부 변화(빌링키 삭제,
> 결제 상태 변경)를 반영한다. 공통 원칙: **로컬/페이로드를 믿지 말고 토스에 다시 물어본다.**
>
> 선행: [04](04-subscription-create.md)·[05](05-renewals.md)·[06](06-subscription-manage.md)의 PENDING/타임아웃.

---

## 0. 한눈에 보기

두 개의 독립 메커니즘이 "결제 결과가 실제로 어떻게 됐는가"를 진실에 수렴시킨다.

| 메커니즘 | 트리거 | 진입점 | 무엇을 확정 |
|---|---|---|---|
| **PENDING 정산 스윕** | 갱신 배치(주기적, 문서 05) | `renewals._reconcile_pending_payments` | 타임아웃으로 남은 PENDING 결제 |
| **토스 웹훅** | 토스가 POST | `POST /api/v1/webhooks/toss` → `webhooks.handle_webhook` | 빌링키 삭제 / 결제 상태 변경 |

관련 파일: `app/services/renewals.py`(정산), `app/api/v1/webhooks.py`·`app/services/webhooks.py`(웹훅),
`app/models/webhook_event.py`(멱등 기록).

핵심 사상 한 줄: **"PENDING은 모름"이라는 상태를 만들어 두고(04~06), 나중에 토스에 재조회해
DONE/FAILED로 확정한다.** 절대 추측으로 확정하지 않는다.

---

## 1. PENDING 정산 스윕

### 1-1. 왜 필요한가
04(첫 결제), 05(갱신), 06(수동결제)에서 토스 호출이 **타임아웃**나면 결과를 알 수 없다.
이때 결제는 `PENDING`으로 남고 사용자에겐 503을 준다. 그 PENDING을 영원히 둘 수 없으니,
**갱신 배치가 돌 때마다** 충분히 오래된 PENDING을 토스에 재조회해 결말을 짓는다.

### 1-2. 스윕 진입 — `_reconcile_pending_payments` (`renewals.py`)

```python
PENDING_RECONCILE_GRACE = 10분
# ★ outerjoin: subscription_id가 NULL인 단건 결제도 누락 없이 수집
stuck = (PENDING 결제 LEFT OUTER JOIN Subscription) where requested_at <= now - 10분
for (payment, sub) in stuck:
    if (payment.payment_type != FIRST
            and sub is not None          # ★ 단건(sub=None)은 이 건너뜀 조건에서 제외
            and sub.status in _DUE_STATUSES):
        continue        # 갱신 풀(ACTIVE/PAST_DUE)의 RENEWAL/RETRY는 건드리지 않음
    _reconcile_one_payment(payment.id)   # 항목별 독립 처리
```

**소유권 경계(매우 중요)**: 갱신 풀에 있는 구독(ACTIVE/PAST_DUE 등 `_DUE_STATUSES`)의
RENEWAL/RETRY 결제는 **`_renew_one`이 같은 order_id로 자체 수렴**시킨다(문서 05). 정산 스윕이
같은 결제를 동시에 건드리면 충돌하므로, 스윕은 다음만 처리한다:
- **FIRST 결제**(구독 생성 시 결제 — _renew_one이 다루지 않음),
- 구독이 **갱신 풀을 떠난 경우**(CANCELED/EXPIRED 등 — _renew_one 대상 아님), 또는
- **단건 결제**(`subscription_id = NULL` — 구독과 무관, 아래 1-4).

`outerjoin`을 쓰는 이유: 단건 결제는 `subscription_id`가 NULL이라 inner join을 쓰면 쿼리 결과에서
아예 빠진다. outer join으로 `(payment, None)` 쌍이 나오게 해 수집한다.

10분 유예를 두는 이유: 방금 시작된 결제가 곧 응답할 수도 있으니 성급히 확정하지 않는다.

### 1-3. 한 건 확정 — `_reconcile_one_payment` (`renewals.py`)

```python
lock = acquire_lock(f"lock:reconcile:{payment_id}")   # 정산 전용 락
payment = db.get(Payment, payment_id, with_for_update=True)
if payment is None or payment.status != PENDING: return   # 이미 확정됨
# ★ subscription_id가 None(단건)이면 Subscription 조회 자체를 스킵
sub = (db.get(Subscription, payment.subscription_id)
       if payment.subscription_id else None)
if (payment_type != FIRST and sub is not None   # ★ sub이 None이면 갱신 풀 체크 불필요
        and sub.status in _DUE_STATUSES):
    return                                # 락 사이에 갱신 풀로 복귀 → _renew_one에 양보

found = await toss.get_payment_by_order_id(payment.order_id)   # ★ 토스에 재조회(진실원)
```

토스 응답에 따라 세 갈래:

**① DONE — 실제로 승인됐음**
```python
payment.status = DONE; payment.toss_payment_key/approved_at/raw_response = ...
# ★ sub이 None이 아닐 때만 고아 여부 판단(단건은 고아 개념 없음)
orphaned = (payment_type != FIRST and sub is not None
            and sub.status in (CANCELED, EXPIRED))
record_audit("payment.reconciled_done", detail={requires_review:True, ...} if orphaned else None)
commit
if orphaned:
    # 구독은 이미 끝났는데 돈은 승인됨 → 기간을 제공 못 한 결제
    email_sender.send(대표 담당자, "수동 확인 필요 — 취소된 구독의 갱신 결제 확정 ... 환불 검토")
```
- 결제는 DONE으로 확정. 단 **구독이 이미 CANCELED/EXPIRED인데 결제가 승인된 "고아 결제"** 라면
  기간을 제공하지 못한 돈이므로 **환불 검토 요청 메일**을 담당자에게 보낸다(자동 환불은 하지 않음 — 사람 판단).
- **단건 결제**(`sub=None`)는 고아 판단 없이 DONE 확정만 한다.

**② 토스에 기록 없음(None) — 유예 후에도 없음 → 미체결 확정**
```python
payment.status = FAILED; failure_code = "RECONCILE_NOT_FOUND"
# ★ sub이 None이 아닐 때만 구독 만료 처리(단건은 구독 없으므로 스킵)
if (payment_type == FIRST and sub is not None
        and sub.status == ACTIVE):    # 첫 결제가 끝내 미체결인데 구독은 ACTIVE
    sub.status = EXPIRED; sub.next_billing_at = None
    빌링키 삭제(best-effort) → 성공 시 암호문 제거
    record_audit("subscription.expired", reason="first_payment_reconcile_not_found")
record_audit("payment.reconciled_failed"); commit
```
- 10분 유예가 지나도 토스에 그 주문이 없으면 "결제 안 됨"으로 확정(FAILED).
- 첫 결제가 이렇게 실패로 확정되는데 구독이 ACTIVE로 떠 있으면(타임아웃으로 활성처럼 보였던 경우)
  **구독을 EXPIRED로 정리**(돈 안 받고 서비스 주는 상태 방지).
- **단건 결제**(`sub=None`)는 구독 상태 변경 없이 FAILED 확정만 한다.

**③ 비-DONE 진행 중(승인 중 등)**
- 아무것도 안 하고 다음 주기에 다시 확인(아직 결말 안 났으므로).

> 락(`lock:reconcile:{id}`) + 행잠금 + 처리 직전 재확인은 05의 `_renew_one`과 동일한 안전 패턴.

### 1-4. 단건 결제(subscription_id NULL) 처리

구독과 무관한 **단건(일반) 결제**는 `Payment.subscription_id = NULL`이다.
이 결제도 타임아웃 시 PENDING으로 남으며, 정산 스윕이 동일하게 처리한다.

처리 경로 요약:
- `_reconcile_pending_payments`: `outerjoin`으로 수집 → `sub is not None` 가드로 갱신 풀 건너뜀 조건에서 제외 → `_reconcile_one_payment` 호출.
- `_reconcile_one_payment`: `subscription_id is None`이면 Subscription 조회 없이 토스 재조회 → **DONE 또는 FAILED 확정만**(구독 상태 변경 없음).

단건 결제 자체의 생성/흐름은 [문서 11](11-one-off-payment.md)을 참조.

관련 테스트: `tests/integration/test_one_off_payment.py` — `test_reconcile_confirms_one_off`.

---

## 2. 토스 웹훅

### 2-1. 진입 — `POST /api/v1/webhooks/toss` (`api/v1/webhooks.py`)

```python
if webhook_ip_check_enabled and ip not in toss_webhook_allowed_ips:
    raise PermissionDeniedError      # 토스 IP만 허용(설정으로 on/off)
payload = await request.json()
tid = request.headers.get("tosspayments-webhook-transmission-id")
event = await handle_webhook(db, toss, email_sender, transmission_id=tid, payload=payload)
return {"status": event.status}
```
- 이 엔드포인트는 **HMAC 인증을 쓰지 않는다**(토스가 우리 서명 규약을 모름). 대신 **토스 IP 화이트리스트**로 막는다.
- 응답 상태코드가 중요하다: **200을 주면 토스는 "전달 성공"으로 보고 재전송하지 않는다.**
  그래서 "나중에 다시 받아야 하는" 일시 오류는 일부러 예외를 던져 200을 주지 않는다(아래 2-3).

### 2-2. 멱등 처리 — `handle_webhook` (`services/webhooks.py`)

```python
if not transmission_id:
    raise InputValidationError      # 멱등 식별 불가 → 거부(합성 ID는 위조 적재 위험)
existing = WebhookEvent where transmission_id == tid
if existing: return existing        # 중복 수신 → 멱등(이미 처리한 것 그대로 반환)
event = WebhookEvent(transmission_id, event_type, payload); db.add; flush
  except IntegrityError:            # 동시 중복 → 롤백 후 기존 행 반환(멱등)
```
- **`transmission_id`(토스 고유 전송 ID)가 멱등 키**. `WebhookEvent.transmission_id`가 unique라
  같은 웹훅이 여러 번 와도 한 번만 처리된다. 헤더가 없으면 거부(합성 ID를 만들면 위조 재전송이
  dedup을 우회해 무한 적재될 수 있으므로).

### 2-3. 처리 결과와 상태코드 정책

```python
try:
    if event_type == "BILLING_DELETED":          _handle_billing_deleted(...);  status=PROCESSED
    elif event_type == "PAYMENT_STATUS_CHANGED": _handle_payment_status_changed(...); status=PROCESSED
    else:                                         status=IGNORED          # 모르는 이벤트는 무시
except TossError:                # 일시 오류(토스 재조회 실패 등)
    rollback; raise              # → 200 아님 → 토스가 재전송(유실 방지)
except Exception:                # 영구 처리 불가
    status=FAILED                # → 200 반환(무한 재전송 방지), 운영자가 FAILED 점검
event.processed_at = now; commit
```

상태코드 정책(초급자 핵심):
- **일시 오류**(예: 토스 재조회 실패) → 예외를 다시 던져 **200을 주지 않음** → 토스가 재전송 → 다음에 성공.
- **영구 실패**(처리 불가) → `FAILED`로 기록하고 **200 반환**(무한 재전송 방지). 운영 reaper가 점검.
- **모르는 이벤트** → `IGNORED`(정상 200).

### 2-4. 빌링키 삭제 웹훅 — `_handle_billing_deleted`
토스에서 빌링키가 삭제되면(고객/카드사 사유 등):
- 해당 빌링키 해시로 **활성·미수·해지 구독**을 찾아, 담당자에게 **"카드 재등록 안내" 메일** 발송.
- 외부 페이로드 문자열은 `_sanitize`로 개행/제어문자 제거 후 메일/로그에 넣음(인젝션 방지).
- 구독 상태를 강제로 바꾸진 않는다(다음 갱신 결제가 자연히 실패→재시도 흐름으로 감).

### 2-5. 결제 상태 변경 웹훅 — `_handle_payment_status_changed`
```python
order_id = payload["data"]["orderId"]
payment = Payment where order_id == order_id        # 우리 주문 아니면 무시
verified = await toss.get_payment_by_order_id(order_id)   # ★ 페이로드 안 믿고 재조회
if verified is None: return                          # 토스에서 확인 불가 → 위조 의심, 무시
if verified.status == "CANCELED" and payment.status != CANCELED:
    payment.status = CANCELED; payment.raw_response = verified.raw   # 환불/취소 동기화
```
- **페이로드를 신뢰하지 않는다.** 거기서 `orderId`만 꺼내 **토스 API로 재조회**해 진짜 상태를 확인.
  위조 웹훅으로는 상태를 바꿀 수 없다(토스가 확인해줘야만 반영).
- 현재는 토스에서 CANCELED(취소/환불)된 경우 로컬 결제도 CANCELED로 동기화한다.

---

## 3. 두 메커니즘의 관계

```
결제 시도(04/05/06)
   ├ DONE      → 끝(확정)
   ├ TossError → FAILED(확정)
   └ Timeout   → PENDING(결과 불명) ─┐
                                     │  (시간 경과)
        ┌────────────────────────────┴───────────────────────────┐
        ▼                                                          ▼
[정산 스윕] 갱신 배치가 10분+ 된 PENDING을              [웹훅] 토스가 상태 변경을
 토스 재조회 → DONE/FAILED 확정                         알려옴 → orderId 재조회로 동기화
   (FIRST/풀이탈 결제만; 갱신풀은 _renew_one이 수렴)
```

- 둘 다 **"토스에 재조회"가 진실의 원천**이다(로컬·페이로드는 힌트일 뿐).
- 정산 스윕은 **우리가 능동적으로** 미해결을 청소하고, 웹훅은 **토스가 알려주는** 변화를 반영한다.
- 같은 결제를 정산 스윕과 `_renew_one`이 동시에 만지지 않도록 **소유권 경계**(1-2)가 있다.

---

## 4. 예외 · 안전장치

| 상황 | 처리 | 위치 |
|---|---|---|
| 타임아웃 PENDING이 영원히 남음 | 10분 후 스윕이 토스 재조회로 확정 | `_reconcile_*` |
| 갱신 결제를 스윕과 배치가 동시 처리 | 소유권 경계 + 락으로 분리 | `_reconcile_pending_payments` |
| 취소된 구독에 결제가 뒤늦게 DONE(고아) | DONE 확정 + 환불 검토 메일(자동환불 X) | `_reconcile_one_payment` |
| 첫 결제 미체결인데 구독 ACTIVE | 구독 EXPIRED + 빌링키 삭제 | `_reconcile_one_payment` |
| 단건 결제(subscription_id NULL) PENDING | outerjoin으로 수집, `sub is not None` 가드 → DONE/FAILED 확정만(구독 조작 없음) | `_reconcile_pending_payments`, `_reconcile_one_payment` |
| 웹훅 중복 수신 | transmission_id unique로 멱등 | `handle_webhook` |
| transmission_id 헤더 없음 | 거부(위조 적재 방지) | `handle_webhook` |
| 위조 웹훅 | orderId 토스 재조회로만 반영 → 무효 | `_handle_payment_status_changed` |
| 웹훅 일시 오류 | 200 안 줌 → 토스 재전송 | `handle_webhook` except TossError |
| 웹훅 영구 실패 | FAILED 기록 + 200(무한 재전송 방지) | `handle_webhook` except Exception |
| 비-토스 IP 웹훅 | IP 화이트리스트로 거부 | `toss_webhook` |
| 페이로드 문자열 인젝션 | `_sanitize`로 제어문자 제거 | `_handle_billing_deleted` |

---

## 5. 관련 테스트

- `tests/integration/test_renewals.py` — PENDING 정산: DONE 확정, NOT_FOUND→FAILED(+첫결제 만료),
  고아 결제 환불 검토 메일, 갱신 풀 소유권 경계(스윕이 안 건드림).
- `tests/integration/test_one_off_payment.py` — `test_reconcile_confirms_one_off`: 단건 결제
  PENDING 스윕이 subscription_id NULL 결제를 DONE으로 정확히 확정하는지 검증.
- `tests/integration/test_webhooks.py` — 멱등(중복 transmission_id), transmission_id 없음 거부,
  BILLING_DELETED 메일, PAYMENT_STATUS_CHANGED 재조회 동기화, 위조(토스 미확인) 무시,
  일시 오류 재전송 유도(예외), IP 화이트리스트.

---

## 6. 유지보수 체크리스트

1. **"토스 재조회로 확정" 원칙을 깨지 말 것.** 로컬 PENDING이나 웹훅 페이로드만 보고 상태를
   바꾸면 위조·유실에 취약해진다. 항상 `get_payment_by_order_id`로 확인.
2. **소유권 경계 유지**: 갱신 풀(`_DUE_STATUSES`)의 RENEWAL/RETRY는 스윕이 건드리지 않는다.
   새 결제 타입/상태를 추가하면 이 경계 조건(1-2)을 재검토.
3. **웹훅 상태코드 정책**: 일시 오류는 예외로 200을 막아 재전송 유도, 영구 실패는 FAILED+200.
   새 이벤트 핸들러를 추가할 때 이 try/except 구조 안에서 처리할 것.
4. **새 웹훅 이벤트 처리**: `handle_webhook`의 분기에 추가하고, 외부 페이로드는 항상 `_sanitize`/재조회.
   transmission_id 멱등은 공통이므로 그대로 활용.
5. **고아 결제·환불은 자동화하지 말 것**(현재). 돈 관련 비가역 동작은 담당자 메일로 사람 판단을 거친다.
6. **유예 시간(10분) 조정**은 `PENDING_RECONCILE_GRACE` 상수. 너무 짧으면 진행 중 결제를 성급히
   FAILED 처리할 위험이 있으니 신중히.
