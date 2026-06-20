# 12. 웹훅 처리 (토스페이먼츠 → 서버)

> **상호참조**:
> 구독 갱신 → [05. 구독 갱신·만료·재시도](05-subscription-renewal.md) |
> 단건 결제 → [07. 단건(일반) 결제 + 취소](07-one-off-payment.md) |
> 인증·IP 검증 → [03. 인증과 보안 공통](03-auth-and-security.md) |
> 테이블 구조 → [02. 데이터베이스](02-database.md)

---

## 1. 한 줄 요약

토스페이먼츠가 결제 상태 변경·빌링키 삭제 등 이벤트 발생 시 **서버로 HTTP POST를 밀어 보내는(push)** 것이 웹훅이다.
서버는 이 요청을 받아 **중복 방지 → 이벤트 타입 분기 → 토스 재조회 후 상태 확정 → DB 갱신** 순서로 처리한다.

---

## 2. 언제 실행되나 — 트리거

**트리거: 토스페이먼츠 서버가 자동으로 POST 요청을 보낸다.**

- 결제 상태가 변경될 때 (`PAYMENT_STATUS_CHANGED`)
- 자동결제 빌링키가 삭제될 때 (`BILLING_DELETED`)
- 그 밖에 토스 개발자센터에 등록한 이벤트 타입 발생 시

우리 서버는 요청을 **기다리지 않고 받는다**. 사람이 직접 트리거하지 않는다.

---

## 3. 요청 진입점

```
POST /api/v1/webhooks/toss
```

| 항목 | 내용 |
|------|------|
| 파일 | `app/api/v1/webhooks.py:28` |
| 라우트 함수 | `toss_webhook` |
| 인증 방식 | **서명 없음, IP 주소 화이트리스트만** |
| 필수 헤더 | `tosspayments-webhook-transmission-id` |
| Content-Type | `application/json` |

> **왜 서명 검증을 하지 않나?**
> 토스 공식 문서(`docs/toss/2.API/5.웹훅이벤트.md:36-44`)에 따르면 서명(`tosspayments-webhook-signature`)은 지급대행(`payout.changed`, `seller.changed`) 전용 헤더다. 일반 결제(`PAYMENT_STATUS_CHANGED`)와 빌링(`BILLING_DELETED`) 웹훅에는 서명이 없다.
> 대신 **IP 주소 화이트리스트**로 발신지를 검증하고, `PAYMENT_STATUS_CHANGED`는 **페이로드를 믿지 않고 토스 API 재조회**로 상태를 확정해 위조를 차단한다.

---

## 4. 단계별 처리 흐름

### 4.1 전체 흐름 도식

```
[토스 서버]
    │  POST /api/v1/webhooks/toss
    │  Header: tosspayments-webhook-transmission-id: whtrans_xxx
    │  Body: { "eventType": "...", "data": { ... } }
    ▼
[app/api/v1/webhooks.py:28]  toss_webhook()
    │
    ├─ (1) IP 검사 (webhook_ip_check_enabled=True일 때만)
    │       → 허용 IP 목록 외 → PermissionDeniedError → HTTP 403
    │
    ├─ (2) payload JSON 파싱, transmission_id 헤더 추출
    │
    └─ (3) handle_webhook() 호출
               [app/services/webhooks.py:55]
               │
               ├─ transmission_id 없음 → InputValidationError → HTTP 422
               │
               ├─ DB 조회: 동일 transmission_id 이미 있음 → 기존 행 반환(멱등)
               │
               ├─ WebhookEvent INSERT(status=RECEIVED) → flush
               │     동시 중복: IntegrityError → rollback → 기존 행 재조회
               │
               ├─ 이벤트 타입 분기
               │     "BILLING_DELETED"          → _handle_billing_deleted()
               │     "PAYMENT_STATUS_CHANGED"   → _handle_payment_status_changed()
               │     그 외                      → status=IGNORED
               │
               ├─ TossError 발생 시 → rollback + 재발생 → HTTP 5xx → 토스 재전송
               │
               ├─ 일반 Exception → status=FAILED → commit → HTTP 200 반환
               │     (무한 재전송 방지. 운영자 reaper가 FAILED 행 점검)
               │
               └─ 정상 → status=PROCESSED, processed_at 기록 → commit

    ▼
HTTP 200 {"status": "PROCESSED"} (또는 "IGNORED"/"FAILED")
```

### 4.2 IP 화이트리스트 검사

**파일**: `app/api/v1/webhooks.py:45-48`

```python
if settings.webhook_ip_check_enabled:
    ip = get_client_ip(request, settings)
    if ip not in settings.toss_webhook_allowed_ips:
        raise PermissionDeniedError("허용되지 않은 요청입니다")
```

- 설정 변수: `webhook_ip_check_enabled` (기본 `True`), `toss_webhook_allowed_ips`
- IP 목록은 `app/core/config.py:14-18`의 `TOSS_WEBHOOK_IPS` 상수를 기본값으로 사용

```
# app/core/config.py:14-18
TOSS_WEBHOOK_IPS = [
    "13.124.18.147", "13.124.108.35", "3.36.173.151", "3.38.81.32",
    "115.92.221.121", "115.92.221.122", "115.92.221.123",
    "115.92.221.125", "115.92.221.126", "115.92.221.127",
]
```

- 로컬 개발·테스트 환경에서는 `webhook_ip_check_enabled=False`로 두어 화이트리스트 없이 동작한다. (`app/core/config.py:62`)
- 리버스 프록시 환경이면 `trust_proxy=True`로 설정해야 `X-Forwarded-For` 헤더에서 실제 IP를 읽는다. (`app/api/deps.py:63-74`)

### 4.3 중복 방지 (멱등성 보장)

토스는 웹훅 전달에 **at-least-once** 방식을 사용한다. 즉, 동일 이벤트가 두 번 이상 올 수 있다.

중복을 막는 방법은 두 가지다.

| 단계 | 방법 | 코드 위치 |
|------|------|---------|
| 1단계 (빠른 체크) | `transmission_id`로 DB 조회, 이미 있으면 즉시 반환 | `app/services/webhooks.py:74-77` |
| 2단계 (동시성 처리) | 두 요청이 동시에 들어와 INSERT 경쟁 시 `IntegrityError` 포착 → rollback → 기존 행 반환 | `app/services/webhooks.py:82-91` |

`transmission_id`는 `webhook_events.transmission_id` 컬럼에 `UNIQUE` 제약이 걸려 있어 DB 수준에서도 중복 삽입을 차단한다. (`app/models/webhook_event.py:24`)

> **왜 transmission_id가 없는 요청은 거부하나?** (`app/services/webhooks.py:71-72`)
> 합성 ID를 만들면 헤더 없는 위조 재전송이 dedup을 우회해 무한 적재될 수 있다. 토스는 항상 이 헤더를 보내므로, 없으면 정상 요청이 아니다.

### 4.4 이벤트 타입 분기

**파일**: `app/services/webhooks.py:94-101`

```python
if event_type == "BILLING_DELETED":
    await _handle_billing_deleted(db, email_sender, payload)
    event.status = WebhookStatus.PROCESSED
elif event_type == "PAYMENT_STATUS_CHANGED":
    await _handle_payment_status_changed(db, toss, payload)
    event.status = WebhookStatus.PROCESSED
else:
    event.status = WebhookStatus.IGNORED
```

### 4.5 PAYMENT_STATUS_CHANGED 처리 — 페이로드 위조 방어

**파일**: `app/services/webhooks.py:155-183`

```
1. payload.data.orderId 추출
2. payments 테이블에서 order_id로 Payment 행 조회
   → 없으면 우리 주문이 아니므로 조용히 반환
3. toss.get_payment_by_order_id(order_id) 로 토스 API 재조회  ← 핵심!
   → 재조회 결과 None이면 위조 의심, 무시
4. 재조회 결과 status == "CANCELED" 이고 DB 상태가 아직 CANCELED가 아닐 때만
   Payment.status = CANCELED, canceled_amount, canceled_at, raw_response 갱신
```

> **핵심 보안 원칙**: 페이로드의 `data.status`를 직접 사용하지 않는다.
> 외부에서 온 입력은 신뢰 경계 밖이다. `orderId`만 취해 서버에서 직접 재조회한 결과로 상태를 확정한다.
> 재조회는 `app/toss/client.py:123-135`의 `get_payment_by_order_id()`를 사용한다.

현재 처리하는 상태 변경: **CANCELED만** (전액 취소 동기화)
`PARTIAL_CANCELED` 등 나머지 상태는 현재 미처리 — 새로 추가하려면 아래 [9. 유지보수 팁] 참고.

### 4.6 BILLING_DELETED 처리

**파일**: `app/services/webhooks.py:118-152`

자동결제에 사용하는 빌링키가 토스에서 삭제된 경우 서비스 담당자에게 알림 메일을 보낸다.

```
1. payload.data.billingKey 추출
2. sha256_hex(billingKey)로 해시 변환 후 cards.billing_key_hash 컬럼 조회
   (DB에 평문 빌링키 없음 — 해시로만 조회 가능 / 카드 보관함 전환 이후 cards 테이블 사용)
3. 조회 대상: card_id FK로 연결된 구독 중 status가 ACTIVE, PAST_DUE, CANCELED인 구독만
   - TRIAL, SUSPENDED, EXPIRED는 제외 (다음 갱신 결제가 없는 상태)
4. 서비스(Service) 조회 — service가 None이면 조용히 반환(데이터 불일치 방어)
5. 해당 구독의 서비스 담당자 이메일(service.manager_email)로 알림 메일 발송
6. payload.data.reason은 _sanitize()로 개행·제어문자 제거 후 메일 본문에 삽입
```

왜 ACTIVE·PAST_DUE·CANCELED 구독만 대상인가: 이 세 상태는 향후 정기 갱신 결제가 예약된 구독이다. 빌링키가 삭제되면 다음 갱신이 실패하므로 담당자가 사용자에게 카드 재등록을 안내해야 한다.

---

## 5. 사용하는 DB 테이블·컬럼

### 5.1 webhook_events (쓰기)

> 파일: `app/models/webhook_event.py:18-29` | 02-database.md 3.8절 참조

| 컬럼 | 타입 | 쓰기 타이밍 | 내용 |
|------|------|-----------|------|
| `id` | UUID | INSERT 시 | PK, uuid4() 자동 생성 |
| `transmission_id` | String(100) | INSERT 시 | 토스가 부여한 고유 전송 ID. UNIQUE 제약. |
| `event_type` | String(100) | INSERT 시 | `PAYMENT_STATUS_CHANGED`, `BILLING_DELETED` 등 |
| `payload` | JSONB | INSERT 시 | 토스 웹훅 원문 페이로드 전체 |
| `status` | String(20) | INSERT·UPDATE | 초기 `RECEIVED` → 처리 후 `PROCESSED`/`IGNORED`/`FAILED` |
| `received_at` | DateTime(tz) | INSERT 시 | DB 서버 시각(UTC), server_default=now() |
| `processed_at` | DateTime(tz) | UPDATE 시 | 처리 완료 시각(UTC), 미처리 시 NULL |

### 5.2 payments (갱신)

`PAYMENT_STATUS_CHANGED` → `CANCELED` 확정 시 아래 컬럼을 갱신한다.

| 컬럼 | 갱신 내용 |
|------|---------|
| `status` | `PaymentStatus.CANCELED` |
| `canceled_amount` | `payment.amount` (전액 취소로 간주) |
| `canceled_at` | `utcnow()` |
| `raw_response` | 토스 재조회 응답 원문 |

### 5.3 subscriptions (간접 참조)

`BILLING_DELETED` 처리 시 `billing_key_hash`로 구독을 조회만 한다. 구독 상태 자체를 직접 변경하지는 않는다.

### 5.4 services (읽기)

`BILLING_DELETED` 처리 시 `service.manager_email`을 읽어 알림 메일을 보낸다.

---

## 6. 상태 전이

### 6.1 WebhookEvent.status

**파일**: `app/models/enums.py:118-124`

```
수신
  │
  ▼
RECEIVED        ← INSERT 직후 (WebhookStatus.RECEIVED)
  │
  ├─ 정상 처리 → PROCESSED
  ├─ 알 수 없는 이벤트 타입 → IGNORED
  └─ 처리 불가(영구 오류) → FAILED
```

| 값 | 의미 |
|----|------|
| `RECEIVED` | 수신됨, 처리 전 (INSERT 직후 기본값) |
| `PROCESSED` | 정상 처리 완료 |
| `IGNORED` | 처리 대상이 아닌 이벤트 타입 — 기록만 남김 |
| `FAILED` | 처리 중 영구 오류 — 운영자 수동 점검 대상 |

`TossError` 발생 시에는 `status`가 `RECEIVED` 상태인 채로 **롤백**되어 행 자체가 남지 않는다. (`app/services/webhooks.py:102-107`)

### 6.2 재전송 정책 분기

| 예외 종류 | 처리 방식 | 이유 |
|----------|---------|------|
| `TossError` (일시적 오류) | rollback + 예외 재발생 → HTTP 5xx → 토스 재전송 | 일시 장애이므로 나중에 다시 시도하면 성공 가능 |
| 일반 `Exception` (영구 오류) | `status=FAILED` + commit → HTTP 200 반환 | 재전송해도 같은 실패 반복 — 토스 큐에서 제거하고 운영자가 수동 처리 |

토스의 재전송 정책(최대 7회, 최초 전송으로부터 3일 19시간): `docs/toss/1.가이드/4.더알아보기/1.웹훅연결하기.md:77-93` 참조.

---

## 7. 예외·엣지 케이스

| 상황 | 동작 | HTTP 응답 |
|------|------|---------|
| 토스 IP 외 발신 (webhook_ip_check_enabled=True) | `PermissionDeniedError` | 403 |
| `transmission_id` 헤더 없음 | `InputValidationError` | 422 |
| 동일 `transmission_id` 재수신 (중복) | 기존 행 반환, 재처리 안 함 | 200 |
| 동시 중복 수신 (race condition) | `IntegrityError` 포착 → 기존 행 반환 | 200 |
| 알 수 없는 이벤트 타입 | `status=IGNORED` 기록 | 200 |
| 토스 재조회 결과 `None` (위조 의심) | DB 상태 불변, 이벤트 PROCESSED 기록 | 200 |
| `TossError` (토스 API 일시 장애) | rollback, 예외 재발생 | 5xx → 토스 재전송 |
| 그 외 예외 (DB 오류 등) | `status=FAILED`, 운영자 점검 | 200 |
| `reason` 필드에 개행·제어문자 포함 | `_sanitize()`로 제거 후 메일 발송 | 200 |

### 엣지 케이스 상세

**1. `PAYMENT_STATUS_CHANGED`에서 우리 DB에 없는 `orderId`**
해당 결제가 다른 시스템의 주문이거나 위조된 것이다. `payment is None` 조건에서 조용히 반환한다. 이벤트는 `PROCESSED`로 기록된다. (`app/services/webhooks.py:173-175`)

**2. `BILLING_DELETED`에서 해당 빌링키를 가진 구독이 없음**
이미 만료·정지된 구독의 빌링키가 삭제된 경우 등이다. 메일을 보내지 않고 조용히 반환한다. 이벤트는 `PROCESSED`로 기록된다. (`app/services/webhooks.py:143-144`)

**2-1. `BILLING_DELETED`에서 `Service`가 DB에 없음**
구독의 `service_id`에 해당하는 서비스가 없는 경우(데이터 불일치·삭제 레이스 등)다. 조용히 반환해 메일 발송을 생략한다. 이벤트는 `PROCESSED`로 기록된다. (`app/services/webhooks.py` — `if service is None: return` 가드)

**3. `PARTIAL_CANCELED` 등 아직 처리하지 않는 상태 변경**
`PAYMENT_STATUS_CHANGED`의 `_handle_payment_status_changed`는 현재 `CANCELED`만 처리한다. 그 외 상태는 DB 변경 없이 이벤트만 `PROCESSED`로 기록된다. (`app/services/webhooks.py:179-183`)

---

## 8. 관련 테스트

**파일**: `tests/integration/test_webhooks.py`

| 테스트 함수 | 검증 내용 | 위치 |
|-----------|---------|------|
| `test_billing_deleted_notifies_manager` | BILLING_DELETED → 담당자 메일 발송 1건, status=PROCESSED | line 18 |
| `test_duplicate_transmission_processed_once` | 동일 transmission_id 두 번 → 이벤트 1건, 메일 1건 | line 31 |
| `test_unknown_event_ignored` | 알 수 없는 이벤트 → status=IGNORED | line 44 |
| `test_payment_status_changed_verified_by_refetch` | 재조회 CANCELED → payment.status=CANCELED, canceled_amount, canceled_at 갱신 | line 53 |
| `test_payment_status_changed_spoofed_payload_not_applied` | 재조회 None → payment.status 불변 (위조 방어 확인) | line 80 |
| `test_webhook_from_unallowed_ip_rejected` | IP 목록 외 발신 → HTTP 403 | line 102 |
| `test_webhook_without_transmission_id_rejected` | transmission-id 없음 → HTTP 422, 행 미생성 | line 115 |
| `test_payment_status_refetch_error_triggers_retry` | TossError 발생 → 예외 재발생, WebhookEvent 행 미생성 | line 126 |
| `test_billing_deleted_reason_sanitized` | reason 개행문자 → 메일 본문에 삽입 안 됨 | line 155 |

테스트 실행:
```bash
# 웹훅 관련 테스트만
pytest tests/integration/test_webhooks.py -v

# 전체 통합 테스트
pytest tests/integration/ -v
```

---

## 9. 유지보수 팁

### 9.1 새 이벤트 핸들러 추가하기

예를 들어 `DEPOSIT_CALLBACK` (가상계좌 입금 콜백)을 처리하고 싶다면:

1. `app/services/webhooks.py`에 `_handle_deposit_callback(db, payload)` 함수 추가
2. `handle_webhook` 함수의 이벤트 분기에 `elif event_type == "DEPOSIT_CALLBACK":` 추가 (`app/services/webhooks.py:94-101` 근처)
3. 토스 개발자센터에서 해당 웹훅 이벤트 등록 (아래 9.2 참고)
4. `tests/integration/test_webhooks.py`에 해당 이벤트 테스트 추가

```python
# app/services/webhooks.py 분기 추가 예시
elif event_type == "DEPOSIT_CALLBACK":
    await _handle_deposit_callback(db, payload)
    event.status = WebhookStatus.PROCESSED
```

### 9.2 토스 개발자센터에서 웹훅 URL 등록하기

1. [토스 개발자센터 웹훅 메뉴](https://developers.tosspayments.com/my/webhooks) 접속
2. **웹훅 등록하기** 클릭
3. 웹훅 URL 입력: `https://서버도메인/api/v1/webhooks/toss`
4. 수신할 이벤트 선택: `PAYMENT_STATUS_CHANGED`, `BILLING_DELETED` 등
5. 등록 후 전송 기록에서 성공/실패 확인 가능

> 로컬 개발 시에는 외부에서 접근 불가하므로 `ngrok`을 사용해 임시 공개 URL을 생성한다.
> 참고: `docs/toss/1.가이드/4.더알아보기/1.웹훅연결하기.md:43-58`

### 9.3 IP 목록 갱신하기

토스가 발신 IP를 추가·변경하면 `app/core/config.py:14-18`의 `TOSS_WEBHOOK_IPS` 상수를 갱신한다.
또는 `.env` 파일에서 `TOSS_WEBHOOK_ALLOWED_IPS=["1.2.3.4","5.6.7.8"]` 형태로 오버라이드할 수 있다.
최신 IP 목록은 토스 공식 문서(`docs/toss/2.API/4.방화벽,보안.md`)를 확인한다.

### 9.4 transmission_id로 이벤트 추적·디버깅

웹훅이 처리됐는지 확인하거나 문제를 추적할 때:

```sql
-- transmission_id로 이벤트 조회
SELECT id, event_type, status, received_at, processed_at, payload
FROM webhook_events
WHERE transmission_id = 'whtrans_xxxxx';

-- FAILED 이벤트 전체 조회
SELECT * FROM webhook_events
WHERE status = 'FAILED'
ORDER BY received_at DESC;

-- 특정 order_id와 연관된 결제 + 웹훅 이력
SELECT we.transmission_id, we.event_type, we.status, p.status AS payment_status
FROM webhook_events we
JOIN payments p ON we.payload -> 'data' ->> 'orderId' = p.order_id
WHERE p.order_id = 'order_xxx';
```

토스 개발자센터 웹훅 상세 페이지에서도 `transmission_id` 기준으로 전송 기록을 확인할 수 있다.

### 9.5 FAILED 이벤트 처리

`status=FAILED`인 행은 영구적으로 처리하지 못한 이벤트다. 원인은 `payload` 컬럼과 애플리케이션 로그에서 확인한다.

로그 예시:
```
ERROR payment.webhooks webhook 처리 실패(영구): PAYMENT_STATUS_CHANGED
```

로그 출력 코드: `app/services/webhooks.py:111` (`logger.exception`)

원인을 수정한 후 수동으로 재처리하려면 `handle_webhook`을 직접 호출하거나 Admin 스크립트를 별도 작성한다. `transmission_id`가 이미 DB에 있으므로 동일 transmission_id로 재전송해도 멱등 처리로 건너뛴다. 따라서 재처리 시에는 **새 transmission_id**로 수동 호출해야 한다.

### 9.6 로컬 개발 환경 설정

`.env` 파일에 다음을 추가해 IP 검사를 끈다:

```env
WEBHOOK_IP_CHECK_ENABLED=false
```

이후 `curl`이나 테스트 클라이언트로 자유롭게 웹훅 엔드포인트를 호출할 수 있다.

```bash
curl -X POST http://localhost:8000/api/v1/webhooks/toss \
  -H "Content-Type: application/json" \
  -H "tosspayments-webhook-transmission-id: test-tid-001" \
  -d '{"eventType":"BILLING_DELETED","createdAt":"2026-06-10T00:00:00.000000","data":{"billingKey":"bk_test","reason":"테스트"}}'
```
