# 15. 외부 API 레퍼런스 + 샘플 서비스 연동

> **샘플 서비스 UI 개편(2026-06-11)**: `sample_service`는 Centurion 디자인이 적용된
> 서비스팀 제공용 샘플로 개편되었다. 모든 화면 하단의 **「개발자 노트」** 패널이
> 해당 화면이 호출하는 API·구현 함수·핵심 규칙을 보여준다 — 연동 학습은 화면을
> 직접 따라가는 것이 가장 빠르다. 코드 재사용 안내는 `sample_service/README.md`
> "내 서비스에 가져다 쓰는 법" 참고.


> **상호참조**: 인증 공통 → [03. 인증과 보안 공통](03-auth-and-security.md) |
> 구독 생성 상세 → [04. 구독 생성](04-subscription-create.md) |
> 구독 관리(취소·재개·수동결제·카드변경) → [06. 구독 관리](06-subscription-manage.md) |
> 단건 결제 상세 → [07. 단건(일반) 결제 + 취소](07-one-off-payment.md) |
> 웹훅 처리 → [12. 웹훅 처리](12-webhooks.md) |
> 서비스 등록·키 발급 → [09. 서비스 등록](09-services-registry.md)

---

## 1. 이 문서가 하는 일

사내 다른 서비스(예: 진료 앱, 쇼핑몰)를 구독결제 서버에 **실제로 연동**할 때 필요한
모든 것을 한곳에 모았습니다.

- 외부 서비스가 호출할 수 있는 **API 엔드포인트 전체 목록**과 각 엔드포인트의 정확한 요청·응답 형식
- **HMAC 인증 헤더를 직접 만드는 방법** (curl 예시 + Python 예시)
- **샘플 서비스(`sample_service/`)** 가 어떻게 이 API를 호출하는지
- **에러 응답 형식**과 각 코드가 무슨 뜻인지
- 새 API를 추가할 때 따라야 하는 **규칙과 절차**

---

## 2. 외부 API 전체 목록

모든 외부 API 경로는 `app/main.py:78`에서 `/api/v1` 접두어로 등록됩니다.

```
app.include_router(api_v1_router, prefix="/api/v1")
```

라우터 파일: `app/api/v1/__init__.py:1-10`

---

### 2-1. 서비스 목록 조회

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `GET /api/v1/services` |
| **인증** | 없음(무인증) |
| **용도** | 서버에 등록된 서비스의 id·이름·상태 목록 조회. 키 입력 전 단계(화면 최초 진입)에서 호출. |
| **라우트 파일:줄** | `app/api/v1/services.py:15-20` |

#### 요청 헤더
없음(인증 헤더 불필요).

#### 응답 예시
```json
{
  "services": [
    {"id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "name": "진료앱", "status": "ACTIVE"},
    {"id": "8d14a2c1-1111-2222-3333-abc123456789", "name": "쇼핑몰", "status": "ACTIVE"}
  ]
}
```

> **주의**: 키·시크릿·구독 등 민감 정보는 절대 포함되지 않습니다(`app/api/v1/services.py:3-4`).

---

### 2-2. 요금제 목록 조회

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `GET /api/v1/plans` |
| **인증** | HMAC(`authenticate_service`) |
| **용도** | 인증된 서비스에 속한 **활성(ACTIVE) 요금제** 목록 반환. 비활성 요금제는 외부에 노출하지 않음. |
| **라우트 파일:줄** | `app/api/v1/plans.py:14-24` |
| **응답 스키마** | `PlanResponse` (`app/schemas/api.py:44-78`) |

#### 응답 예시
```json
{
  "plans": [
    {
      "id": "d1e2f3a4-0000-1111-2222-333344445555",
      "name": "스탠다드 월간",
      "price": 9900,
      "amount": 7900,
      "currency": "KRW",
      "billing_cycle": "MONTH",
      "cycle_days": null,
      "first_payment_type": "DISCOUNT_AMOUNT",
      "first_payment_value": 2000,
      "trial_enabled": true,
      "trial_days": 7,
      "auto_renew": true,
      "extra_info": {"feature": "premium"}
    }
  ]
}
```

**`amount` vs `price`**: `price`는 정가이고 `amount`는 상시 할인 적용 후 실제 정기 청구 금액입니다(`app/schemas/api.py:47-57`). `billing_cycle`이 `"DAY"`일 때만 `cycle_days`에 값이 있고 나머지는 `null`입니다.

---

### 2-3. 구독 생성

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/subscriptions` |
| **인증** | HMAC + 결제 추가 제한(`payment_rate_limit`) |
| **용도** | 신규 구독 생성. authKey로 빌링키를 발급하고 구독 레코드를 생성. `trial=true`이면 결제 없이 체험 시작. |
| **라우트 파일:줄** | `app/api/v1/subscriptions.py:41-61` |
| **요청 스키마** | `SubscriptionCreateRequest` (`app/schemas/api.py:17-31`) |
| **응답 스키마** | `SubscriptionResponse` (`app/schemas/api.py:81-111`) |

#### 요청 본문
```json
{
  "external_user_id": "user@example.com",
  "plan_id": "d1e2f3a4-0000-1111-2222-333344445555",
  "auth_key": "토스_결제창에서_받은_authKey_값",
  "customer_key": "cust-uuid-hex",
  "trial": false
}
```

| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| `external_user_id` | string | 1–255자 | 외부 서비스 사용자 식별자(서비스+사용자 중복 구독 방지에 사용) |
| `plan_id` | UUID | - | 구독할 요금제 ID |
| `auth_key` | string | 1–300자 | 토스 결제창 authKey(일회용, 빌링키 발급에 사용) |
| `customer_key` | string | 2–300자 | 토스 customerKey |
| `trial` | bool | - | `true`이면 체험 구독. 요금제가 `trial_enabled=true`일 때만 허용 |

> **금액 필드 없음**: 서버가 Plan에서 직접 계산합니다. 클라이언트가 금액을 전달할 수 없어 조작이 불가능합니다(`app/schemas/api.py:31`).

#### 응답 예시
```json
{
  "id": "aabbccdd-1111-2222-3333-444455556666",
  "external_user_id": "user@example.com",
  "plan_id": "d1e2f3a4-0000-1111-2222-333344445555",
  "plan_name": "스탠다드 월간",
  "status": "ACTIVE",
  "access_allowed": true,
  "current_period_start": "2026-06-10T03:00:00Z",
  "current_period_end": "2026-07-10T03:00:00Z",
  "next_billing_at": "2026-07-10T02:55:00Z",
  "card": {"brand": "Visa", "last4": "1234"},
  "retry_count": 0
}
```

**`access_allowed`**: 외부 서비스가 이 사용자의 서비스 접근을 허용할지 판단하는 **핵심 필드**입니다. `TRIAL/ACTIVE/PAST_DUE/CANCELED=true`, `SUSPENDED/EXPIRED=false`(`app/schemas/api.py:85`).

---

### 2-4. 구독 조회

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `GET /api/v1/subscriptions/{external_user_id}` |
| **인증** | HMAC(`authenticate_service`) |
| **용도** | 외부 사용자 ID로 가장 최근 구독 조회. 구독 없으면 404. |
| **라우트 파일:줄** | `app/api/v1/subscriptions.py:82-97` |
| **응답 스키마** | `SubscriptionResponse` (`app/schemas/api.py:81-111`) |

#### 응답 예시
구독 생성 응답과 동일한 형식입니다(2-3절 참조).

---

### 2-5. 구독 취소

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/subscriptions/{external_user_id}/cancel` |
| **인증** | HMAC(`authenticate_service`) |
| **용도** | 구독을 **취소 예약**. 즉시 삭제가 아니라 만료일이 되면 자동 종료. |
| **라우트 파일:줄** | `app/api/v1/subscriptions.py:100-112` |
| **요청 본문** | 없음 |
| **응답 스키마** | `SubscriptionResponse` (`app/schemas/api.py:81-111`) |

---

### 2-6. 구독 재개

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/subscriptions/{external_user_id}/resume` |
| **인증** | HMAC(`authenticate_service`) |
| **용도** | 취소 예약된 구독을 원래 상태로 복귀(CANCELED → ACTIVE). |
| **라우트 파일:줄** | `app/api/v1/subscriptions.py:115-127` |
| **요청 본문** | 없음 |
| **응답 스키마** | `SubscriptionResponse` (`app/schemas/api.py:81-111`) |

---

### 2-7. 수동 결제

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/subscriptions/{external_user_id}/pay` |
| **인증** | HMAC + 결제 추가 제한(`payment_rate_limit`) |
| **용도** | SUSPENDED(정지) 또는 PAST_DUE(미수) 구독의 미수금을 수동으로 즉시 결제. 성공 시 ACTIVE 복귀. |
| **라우트 파일:줄** | `app/api/v1/subscriptions.py:64-79` |
| **요청 본문** | 없음 |
| **응답 스키마** | `SubscriptionResponse` (`app/schemas/api.py:81-111`) |

---

### 2-8. 카드 변경

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/subscriptions/{external_user_id}/change-card` |
| **인증** | HMAC + 결제 추가 제한(`payment_rate_limit`) |
| **용도** | 구독에 연결된 카드(빌링키) 교체. 새 카드 authKey로 신규 빌링키 발급 후 기존 빌링키 삭제. |
| **라우트 파일:줄** | `app/api/v1/subscriptions.py:130-148` |
| **요청 스키마** | `CardChangeRequest` (`app/schemas/api.py:34-42`) |
| **응답 스키마** | `SubscriptionResponse` (`app/schemas/api.py:81-111`) |

#### 요청 본문
```json
{
  "auth_key": "새_카드_등록_후_받은_authKey",
  "customer_key": "cust-uuid-hex"
}
```

---

### 2-8b. 구독 사용일 추가 (외부 서비스 요청)

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/subscriptions/{external_user_id}/add-days` |
| **인증** | HMAC(`authenticate_service`) — 토스 호출 없음(날짜만 변경) |
| **용도** | 외부 서비스가 자기 사용자 구독에 **사용일(N일)을 추가**. 만료일·다음 결제일을 +N일, **상태는 변경하지 않음**. |
| **대상 상태** | 이용 중(`ACTIVE`·`EXTENDED`·`PAST_DUE`)만 가능. 그 외는 **409(CONFLICT)**, 구독 없으면 **404** |
| **요청 스키마** | `UsageDaysRequest` — `{ "days": 30 }` (1~3650) |
| **응답 스키마** | `SubscriptionResponse` (만료일·다음 결제일이 미뤄진 구독) |

`next_billing_at`이 없는 구독(자동갱신 없음 등)은 그대로 유지된다. 어드민 "요금제 사용일추가(보너스)"와 동일한 의미를 구독 단위로 제공한다. 감사로그 `subscription.usage_added`(SERVICE 행위자)에 사용자·일수·만료일 전→후를 기록한다.

#### 요청 본문
```json
{ "days": 30 }
```

---

### 2-9. 단건 결제 생성

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/payments` |
| **인증** | HMAC + 결제 추가 제한(`payment_rate_limit`) |
| **용도** | 구독과 무관한 1회성 결제. 빌링키 발급 후 즉시 청구, 결제 완료 즉시 빌링키 삭제. |
| **라우트 파일:줄** | `app/api/v1/payments.py:44-70` |
| **요청 스키마** | `OneOffPaymentRequest` (`app/schemas/api.py:114-128`) |
| **응답 스키마** | `PaymentResponse` (`app/schemas/api.py:140-157`) |

#### 요청 본문
```json
{
  "external_user_id": "user@example.com",
  "order_id": "order-abc-001",
  "order_name": "의료비 납부",
  "amount": 15000,
  "auth_key": "토스_결제창에서_받은_authKey_값",
  "customer_key": "cust-uuid-hex"
}
```

| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| `external_user_id` | string | 1–255자 | 결제 대상 사용자 식별자 |
| `order_id` | string | 6–64자 | 주문 ID. **서비스 내 고유**(타 서비스와 중복 가능 — 감사 Phase 2). 같은 order_id 재시도는 기존 결제 반환(멱등) |
| `order_name` | string | 1–100자 | 결제창에 표시되는 주문명 |
| `amount` | int | gt=0, le=100,000,000 | 결제 금액(원). 단건은 **클라이언트가 직접 지정**(HMAC 서명이 보호). 1억원 초과 거부 |
| `auth_key` | string | 1–300자 | 토스 결제창 authKey |
| `customer_key` | string | 2–300자 | 토스 customerKey |

#### 응답 예시
```json
{
  "order_id": "order-abc-001",
  "amount": 15000,
  "status": "DONE",
  "kind": "ONE_OFF",
  "payment_type": "ONE_OFF",
  "failure_code": null,
  "failure_message": null,
  "requested_at": "2026-06-10T03:00:00Z",
  "approved_at": "2026-06-10T03:00:01Z"
}
```

---

### 2-10. 결제 내역 조회

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `GET /api/v1/payments/{external_user_id}` |
| **인증** | HMAC(`authenticate_service`) |
| **용도** | 외부 사용자의 결제 내역 최대 50건(최신순). **구독 정기결제 + 단건(ONE_OFF) 결제 모두 포함.** |
| **라우트 파일** | `app/api/v1/payments.py` (`list_payments`) |
| **응답 스키마** | `PaymentResponse` 목록 (`app/schemas/api.py`) — 취소 수수료 필드 포함 |

> **범위 격리**: `Payment.service_id`로 필터하므로 다른 서비스의 결제는 보이지 않는다. 구독 결제와 단건 결제를 한 응답에 함께 반환한다(이전에는 Subscription INNER JOIN으로 단건이 제외됐으나, **취소 가능한 단건 결제의 수수료 안내를 위해 포함하도록 변경**).

#### 취소 수수료 필드 (서비스의 취소 화면 노출용)

각 결제에는 "지금 취소하면 수수료가 얼마이고 얼마가 환불되는지"를 안내하는 필드가 함께 반환된다. 서비스는 이 값으로 취소 화면에 수수료를 노출할 수 있다.

| 필드 | 설명 |
|------|------|
| `cancelable` | 지금 취소 가능 여부. 단건(ONE_OFF)·완료(DONE)·**미취소**·서비스 취소허용일 때만 `true` |
| `cancel_fee_percent` | 서비스 취소 수수료율(%) |
| `cancel_fee` | 취소 시 차감 수수료(원). 취소 가능 결제는 **예상액**, 이미 취소된 결제는 **실제 차감액** |
| `cancel_refund_amount` | 환불액(원). 취소 가능 결제는 **예상액**, (부분/전액) 취소된 결제는 **실제 누적 환불액** |
| `canceled_amount` | **실제 환불된 누적 금액(원).** 어드민 부분취소 시 `status`는 `DONE`이지만 이 값이 0보다 크다 |
| `net_amount` | 실수령(순) 금액(원) = `amount − canceled_amount`. 부분취소 반영 |

계산식은 실제 취소 처리와 동일한 `compute_cancel_fee()`(`app/services/billing_math.py`)를 공유한다 → `fee = amount × fee_percent // 100`(내림), `refund = amount − fee`.

> **부분취소 반영(중요):** 관리자가 어드민에서 일반결제를 **부분취소**하면 `status`는 `DONE`을 유지한 채 `canceled_amount`만 누적된다. 따라서 외부 서비스는 `status == "CANCELED"`만으로 취소를 판정하지 말고 **`canceled_amount`/`net_amount`로 실제 환불·실수령을 표시**해야 한다(샘플서비스 `history_view`가 이 방식). 이미 부분취소된 결제는 `cancelable=false`라 외부에서 추가 취소할 수 없다.

#### 응답 예시
```json
{
  "payments": [
    {
      "order_id": "order-20260610-0001",
      "amount": 10000,
      "status": "DONE",
      "kind": "ONE_OFF",
      "payment_type": "ONE_OFF",
      "failure_code": null,
      "failure_message": null,
      "requested_at": "2026-06-10T02:55:00Z",
      "approved_at": "2026-06-10T02:55:01Z",
      "cancelable": true,
      "cancel_fee_percent": 10,
      "cancel_fee": 1000,
      "cancel_refund_amount": 9000,
      "canceled_amount": 0,
      "net_amount": 10000
    }
  ]
}
```

---

### 2-11. 단건 결제 취소

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/payments/{order_id}/cancel` |
| **인증** | HMAC + 결제 추가 제한(`payment_rate_limit`) |
| **용도** | 단건 결제 취소(환불). 서비스 취소 정책(`cancellation_enabled=false`)이면 차단. |
| **라우트 파일:줄** | `app/api/v1/payments.py:73-89` |
| **요청 스키마** | `OneOffCancelRequest` (`app/schemas/api.py:130-137`) |
| **응답 스키마** | `PaymentResponse` (`app/schemas/api.py:140-157`) |

#### 요청 본문
```json
{
  "reason": "고객 요청으로 취소"
}
```

`reason` 기본값은 `"사용자 취소"`. 최대 200자(토스 API 제한).

---

### 2-12. 웹훅 수신

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/webhooks/toss` |
| **인증** | 토스 IP 화이트리스트(조건부, `webhook_ip_check_enabled=true`일 때) |
| **용도** | 토스페이먼츠가 결제 이벤트를 서버로 푸시. 중복 방지는 Redis `transmission_id`로 처리. |
| **라우트 파일:줄** | `app/api/v1/webhooks.py:28-53` |

#### 웹훅 페이로드 (토스가 보내는 형식)
토스 공식 문서(`docs/toss/`) 참조. 서버는 `request.json()`으로 본문 전체를 읽어 `handle_webhook`에 전달합니다.

#### 응답 예시
```json
{"status": "DONE"}
```

> 로컬 개발 환경에서는 `settings.webhook_ip_check_enabled=False`로 설정하면 IP 화이트리스트 검사를 건너뜁니다(`app/api/v1/webhooks.py:45-48`).

---

### 2-13. 카드 등록/교체

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `POST /api/v1/cards` |
| **인증** | HMAC + 결제 추가 제한(`payment_rate_limit`) — 빌링키 발급이 수반되므로 |
| **용도** | 사용자 카드(빌링키)를 등록하거나 기존 카드를 교체. 응답에 billingKey는 포함하지 않음. |
| **라우트 파일** | `app/api/v1/cards.py` |
| **요청 스키마** | `CardRegisterRequest` (`app/schemas/api.py`) |
| **응답 스키마** | `CardResponse` (마스킹 카드 정보만) |

#### 요청 본문
```json
{
  "external_user_id": "user-123",
  "customer_key": "cust-123",
  "auth_key": "toss_auth_key_xxx"
}
```

#### 응답 예시 (201)
```json
{
  "external_user_id": "user-123",
  "card": {"issuerCode": "61", "number": "123456******1234"}
}
```

> 상세 흐름은 [16. 카드 보관함](16-card-vault.md) 참고.

---

### 2-14. 카드 조회

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `GET /api/v1/cards/{external_user_id}` |
| **인증** | HMAC(`authenticate_service`) |
| **용도** | 등록된 카드의 마스킹 정보를 조회. 없으면 404. |
| **라우트 파일** | `app/api/v1/cards.py` |
| **응답 스키마** | `CardResponse` |

---

### 2-15. 카드 삭제

| 항목 | 내용 |
|------|------|
| **메서드·경로** | `DELETE /api/v1/cards/{external_user_id}` |
| **인증** | HMAC(`authenticate_service`) |
| **용도** | 등록된 카드 및 빌링키 삭제. billing-active 구독이 참조 중이면 409. |
| **라우트 파일** | `app/api/v1/cards.py` |
| **응답 상태** | 204 No Content |

---

### 엔드포인트 요약표

| # | 메서드 | 경로 | 인증 | 파일:줄 |
|---|--------|------|------|---------|
| 1 | GET | `/api/v1/services` | 없음 | `services.py:15` |
| 2 | GET | `/api/v1/plans` | HMAC | `plans.py:14` |
| 3 | POST | `/api/v1/subscriptions` | HMAC+결제 | `subscriptions.py:41` |
| 4 | GET | `/api/v1/subscriptions/{external_user_id}` | HMAC | `subscriptions.py:82` |
| 5 | POST | `/api/v1/subscriptions/{external_user_id}/cancel` | HMAC | `subscriptions.py:100` |
| 6 | POST | `/api/v1/subscriptions/{external_user_id}/resume` | HMAC | `subscriptions.py:115` |
| 7 | POST | `/api/v1/subscriptions/{external_user_id}/pay` | HMAC+결제 | `subscriptions.py:64` |
| 8 | POST | `/api/v1/subscriptions/{external_user_id}/change-card` | HMAC+결제 | `subscriptions.py:130` |
| 9 | POST | `/api/v1/payments` | HMAC+결제 | `payments.py:44` |
| 10 | GET | `/api/v1/payments/{external_user_id}` | HMAC | `payments.py:22` |
| 11 | POST | `/api/v1/payments/{order_id}/cancel` | HMAC+결제 | `payments.py:73` |
| 12 | POST | `/api/v1/webhooks/toss` | IP(조건부) | `webhooks.py:28` |
| 13 | POST | `/api/v1/cards` | HMAC+결제 | `cards.py` |
| 14 | GET | `/api/v1/cards/{external_user_id}` | HMAC | `cards.py` |
| 15 | DELETE | `/api/v1/cards/{external_user_id}` | HMAC | `cards.py` |

**인증 종류**:
- **없음**: 헤더 불필요
- **HMAC**: `authenticate_service` — API키 + IP + HMAC 서명 3중 인증(`app/api/deps.py:77`)
- **HMAC+결제**: `payment_rate_limit` — HMAC 인증 위에 결제 전용 추가 처리율 제한(`app/api/deps.py:141`)

---

## 3. HMAC 인증 방법

> 상세 원리는 [03. 인증과 보안 공통](03-auth-and-security.md)을 먼저 보세요.
> 여기서는 **실제 코드 예시**를 중심으로 설명합니다.

### 3-1. 필요한 헤더 4개

모든 인증이 필요한 요청에 반드시 아래 4개 헤더를 포함해야 합니다(`app/api/deps.py:87-92`).

| 헤더 | 예시 값 | 설명 |
|------|---------|------|
| `x-service-key` | `svc_abc123...` | 서비스 API 키 원문 (어드민에서 1회 발급) |
| `x-timestamp` | `1749520800` | 요청 시각 Unix 초(정수). 서버 시각과 ±300초 이내여야 합니다 |
| `x-nonce` | `a1b2c3d4e5f6...` | 요청마다 다른 랜덤 문자열. UUID hex 권장. 600초 내 재사용 불가 |
| `x-signature` | `fa3c7d8e...` | HMAC-SHA256 서명 (아래에서 계산 방법 설명) |

### 3-2. 서명 계산 방법

**정준 문자열(canonical string)** 을 만들고, 이를 HMAC-SHA256으로 서명합니다.
서버 측 구현: `app/core/security.py:62-75`

```
{METHOD 대문자}\n
{path}\n
{timestamp}\n
{nonce}\n
{sha256_hex(요청본문 bytes)}
```

예: `POST /api/v1/subscriptions`에 JSON 본문을 보낼 때

```
POST
/api/v1/subscriptions
1749520800
a1b2c3d4e5f6789012345678
e3b0c44298fc1c14...  ← sha256(본문 bytes)
```

5줄을 `\n`으로 이어붙인 뒤 `HMAC-SHA256(hmac_secret, 위 문자열)` 을 hex 문자열로 인코딩한 것이 `x-signature` 값입니다.

### 3-3. Python 예시 — 실제 동작 코드

아래는 `sample_service/shop/payment_client.py:19-68`의 실제 구현입니다.
이 코드가 우리 서버의 `app/core/security.py:62-75` `sign_request`와 동일한 알고리즘으로 서명합니다.

```python
# sample_service/shop/payment_client.py:19-68
import hashlib
import hmac
import json
import time
import uuid
import requests

PAYMENT_API_BASE = "http://127.0.0.1:8000"  # 구독서버 주소

def sign_request(secret: str, method: str, path: str,
                 timestamp: str, nonce: str, body: bytes) -> str:
    """HMAC-SHA256 서명 계산 — 서버 app/core/security.py:62 와 동일 알고리즘."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

def _request(method: str, path: str, json_body: dict | None = None,
             api_key: str = "svc_xxx", hmac_secret: str = "xxx") -> dict:
    """HMAC 인증 헤더를 추가해 구독서버에 HTTP 요청을 보낸다."""
    body = b""
    if json_body is not None:
        body = json.dumps(json_body).encode()
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex          # 요청마다 새 UUID — 절대 재사용 금지
    headers = {
        "x-service-key": api_key,
        "x-timestamp": timestamp,
        "x-nonce": nonce,
        "x-signature": sign_request(hmac_secret, method, path,
                                    timestamp, nonce, body),
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    resp = requests.request(method, PAYMENT_API_BASE + path,
                            headers=headers, data=body or None, timeout=30)
    if resp.status_code >= 400:
        err = resp.json()["error"]
        raise Exception(f"{err['code']}: {err['message']}")
    return resp.json()
```

### 3-4. curl 예시 — 구독 조회

```bash
# 변수 설정
API_KEY="svc_abc123def456ghi789"
HMAC_SECRET="your_hmac_secret_here"
BASE="http://127.0.0.1:8000"
EXTERNAL_USER_ID="user@example.com"

# 타임스탬프·nonce
TS=$(date +%s)
NONCE=$(python3 -c "import uuid; print(uuid.uuid4().hex)")

# body hash (GET은 빈 body)
BODY_HASH=$(echo -n "" | sha256sum | cut -d' ' -f1)

# 정준 문자열 만들기 (5줄, \n으로 구분)
CANONICAL="GET\n/api/v1/subscriptions/${EXTERNAL_USER_ID}\n${TS}\n${NONCE}\n${BODY_HASH}"

# HMAC 서명
SIG=$(printf "$CANONICAL" | openssl dgst -sha256 -hmac "$HMAC_SECRET" | cut -d' ' -f2)

# 요청
curl -s \
  -H "x-service-key: $API_KEY" \
  -H "x-timestamp: $TS" \
  -H "x-nonce: $NONCE" \
  -H "x-signature: $SIG" \
  "${BASE}/api/v1/subscriptions/${EXTERNAL_USER_ID}"
```

> **서명 디버깅 체크리스트** (자주 실수하는 부분):
> 1. `METHOD`가 대문자인지 확인 (`GET`, `POST` 등)
> 2. `path` 앞에 `/` 있는지, 쿼리 파라미터는 포함하지 않는지
> 3. 빈 body도 `sha256("")`으로 해시해야 함 (`e3b0c44298fc...`)
> 4. `Content-Type: application/json`일 때 body가 `json.dumps()` 결과의 bytes인지
> 5. `timestamp`가 Unix 초(정수 문자열)인지

---

## 4. 샘플 서비스(`sample_service/`) 연동 가이드

### 4-1. 샘플 서비스가 무엇인가

`sample_service/`는 **별도 git 저장소를 가진 독립 Django 프로젝트**입니다.
구독결제 서버와 **같은 디렉토리에 서브디렉토리로 포함**되어 있지만 `.git`이 별도로 존재합니다.

```
payment_system/
├── app/               ← 구독결제 서버 (FastAPI)
└── sample_service/    ← 데모 외부 서비스 (Django)  ← 별도 git repo
    └── .git/          ← 독립 저장소
```

이 샘플은 "외부 서비스가 우리 API를 어떻게 호출하면 되는가"를 **실제 동작하는 코드**로 보여줍니다.

### 4-2. 전체 흐름 한눈에 보기 (새 흐름: 이메일→서비스→카드→구독/결제)

```
[브라우저]
    │
    ▼
(1) /login — 이메일 선택(첫 단계, 서비스 불필요)
    │  → SampleUser.get_or_create(email) — 세션에 user_id 저장
    │  → 기존 등록 이메일 목록(빠른 선택 버튼) + 직접 입력 폼 제공
    │
    ▼
(2) /services — 서비스 선택(로그인 필수, 2단계)
    │  → GET /api/v1/services (무인증) 으로 서비스 목록 가져옴
    │  → api_key + hmac_secret 입력 → ServiceCredential DB 저장
    │  → 저장된 키가 있으면 [선택] 버튼으로 즉시 활성화
    │
    ▼
(3) /card — 카드 등록 + 보유 카드 조회(3단계)
    │  → GET /api/v1/cards/{uid} — 보유 카드 조회(없으면 "등록 없음" 표시)
    │  → 토스 SDK requestBillingAuth() → /billing/success → POST /api/v1/cards
    │  → 카드 등록 후: [요금제 구독 →] [일반 결제 →] 버튼 표시
    │
    ▼
(4) /plans — 요금제 목록 → /subscribe/<plan_id> — 구독 확인/생성
    │  → GET /api/v1/plans (HMAC 인증)
    │  → POST /api/v1/subscriptions (등록 카드로 즉시 구독, authKey 불필요)
    │
    ├─ /pay — 일반(단건) 결제
    │  → POST /api/v1/payments (등록 카드로 즉시 결제, authKey 불필요)
    │
    ▼
(5) /my — 구독 조회·취소·재개·수동결제
    │  → GET /api/v1/subscriptions/{uid}
    │  → POST /api/v1/subscriptions/{uid}/cancel 등
    │
    ▼
(6) /history — 결제 내역
    │  → GET /api/v1/payments/{uid} (구독 + 단건 결제, 취소 수수료 포함)
    └─ + OneOffRecord (로컬 단건 내역) ⊕ 서버 취소 수수료/환불 예정액 결합
       → 단건 결제 표에 '취소 수수료'·'실제 환불액' 컬럼, 취소 버튼 confirm에도 금액 안내
```

> **게이트(_gate) 새 순서**: 보호 뷰는 ① 로그인 없으면 `/login`, ② 서비스 없으면 `/services`, ③ 둘 다 있으면 통과. 이전(서비스→로그인)과 순서가 바뀌었다.  
> **루트(/)**: 세션 상태에 따라 `/login` → `/services` → `/card` 중 적절한 단계로 자동 라우팅.  
> **로그아웃**: 세션 삭제 후 `/login`으로 복귀(이전 `/` 였음).

> **샘플 서비스의 취소 수수료 노출(요청 반영)**: `history_view`는 `get_payments()`로 받은 단건(ONE_OFF) 결제의 `cancel_fee`/`cancel_refund_amount`/`cancel_fee_percent`를 `order_id`로 로컬 `OneOffRecord`에 매칭해 화면에 표시한다. 결제 내역 표에 "취소 수수료 / 실제 환불액" 컬럼이 추가되고, 취소 버튼을 누르면 `confirm()`에 "수수료 N원 차감 후 M원 환불"이 안내된다. 단건 결제 직후 결과 화면(`result.html`)에도 동일한 안내가 표시된다.

### 4-3. 핵심 파일 구조

| 파일 | 역할 |
|------|------|
| `sample_service/shop/payment_client.py` | 구독서버 API 호출 클라이언트. `sign_request` + `_request` 구현 |
| `sample_service/shop/views.py` | Django 뷰 — `payment_client` 함수를 호출해 API 연동 |
| `sample_service/shop/models.py` | `ServiceCredential`·`SampleUser`·`OneOffRecord` 모델 |
| `sample_service/shop/urls.py` | URL 라우팅 |
| `sample_service/.env` | API 키·HMAC 시크릿·토스 클라이언트 키 설정 |
| `sample_service/.env.example` | 설정 예시 파일 |

### 4-4. `payment_client.py` 구조 이해

`sample_service/shop/payment_client.py:36-68`의 `_request()` 함수가 **모든 API 호출의 기반**입니다.

```python
# 핵심 구조 (sample_service/shop/payment_client.py:36-68)
def _request(method, path, json_body=None, creds=None):
    # creds=(api_key, hmac_secret) 지정 시 그 키로 서명
    # None이면 settings.SERVICE_API_KEY / SERVICE_HMAC_SECRET 폴백
    api_key, hmac_secret = creds if creds else (settings.SERVICE_API_KEY,
                                                 settings.SERVICE_HMAC_SECRET)
    body = json.dumps(json_body).encode() if json_body else b""
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    headers = {
        "x-service-key": api_key,
        "x-timestamp": timestamp,
        "x-nonce": nonce,
        "x-signature": sign_request(hmac_secret, method, path,
                                    timestamp, nonce, body),
    }
    ...
```

각 API 기능별로 `_request`를 감싸는 헬퍼 함수가 있습니다:

| 함수 | 호출 API |
|------|----------|
| `list_services()` (line 71) | `GET /api/v1/services` |
| `get_plans(creds)` (line 81) | `GET /api/v1/plans` |
| `create_subscription(...)` (line 86) | `POST /api/v1/subscriptions` |
| `get_subscription(uid, creds)` (line 110) | `GET /api/v1/subscriptions/{uid}` |
| `cancel(uid, creds)` (line 115) | `POST /api/v1/subscriptions/{uid}/cancel` |
| `resume(uid, creds)` (line 121) | `POST /api/v1/subscriptions/{uid}/resume` |
| `manual_pay(uid, creds)` (line 127) | `POST /api/v1/subscriptions/{uid}/pay` |
| `change_card(uid, ...)` (line 133) | `POST /api/v1/subscriptions/{uid}/change-card` |
| `add_usage_days(uid, days, creds)` | `POST /api/v1/subscriptions/{uid}/add-days` (구독 사용일 추가) |
| `create_one_off_payment(...)` (line 95) | `POST /api/v1/payments` |
| `get_payments(uid, creds)` (line 152) | `GET /api/v1/payments/{uid}` |
| `cancel_one_off_payment(order_id, ...)` (line 141) | `POST /api/v1/payments/{order_id}/cancel` |

### 4-5. `ServiceCredential` 모델 — 키 저장 구조

`sample_service/shop/models.py:10-27`

```python
class ServiceCredential(models.Model):
    service_id  = models.CharField(max_length=64, unique=True)  # 결제서버 서비스 UUID
    name        = models.CharField(max_length=100)              # 표시용 이름
    api_key     = models.CharField(max_length=128)              # x-service-key 헤더용
    hmac_secret = models.CharField(max_length=128)              # x-signature 계산용
    created_at  = models.DateTimeField(auto_now_add=True)
```

저장된 키는 **세션의 `service_id`** 로 조회하고, `(api_key, hmac_secret)` 튜플을 `payment_client._request(creds=...)` 에 전달합니다(`sample_service/shop/views.py:38-44`).

### 4-6. .env 설정

`sample_service/.env.example`을 복사해 `.env`로 만든 뒤 채웁니다:

```env
DJANGO_SECRET_KEY=change-me
DEBUG=True
PAYMENT_API_BASE=http://127.0.0.1:8000   # 구독서버 주소
TOSS_CLIENT_KEY=test_ck_ex6BJGQOVD9YZDN6jvwqrW4w2zNb
```

`SERVICE_API_KEY`/`SERVICE_HMAC_SECRET`는 **`.env`에 두지 않습니다** — 서비스 키·HMAC 시크릿은 실행 후 `/services` 화면에서 서비스를 골라 입력하면 `ServiceCredential`에 저장되어 이후 모든 호출에 쓰입니다(여러 서비스 전환 가능). 코드에는 `settings.SERVICE_API_KEY` 폴백이 남아 있으나 기본값이 빈 문자열이라, 인증이 필요 없는 엔드포인트(서비스 목록)에만 영향이 없습니다.

### 4-7. 셋업 및 실행

```bash
# 1. 구독서버 어드민에서 서비스 등록 (http://127.0.0.1:8000/admin)
#    - 서비스 관리 > 서비스 등록
#    - 허용 IP에 127.0.0.1 입력
#    - 등록 후 API 키 / HMAC Secret 복사

# 2. 샘플 서비스 설정
cd sample_service
cp .env.example .env   # PAYMENT_API_BASE 등 채우기 (서비스 키·HMAC은 실행 후 /services 화면에서 입력)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate

# 3. 두 서버 동시 구동
# 터미널 1 — 구독서버
cd payment_system && .venv/bin/uvicorn app.main:app --port 8000
# 터미널 2 — 샘플 서비스
.venv/bin/python manage.py runserver 8001

# 4. 브라우저에서 http://127.0.0.1:8001 접속
```

---

## 5. 공통 에러 응답 형식

모든 API 에러는 아래 형식으로 반환됩니다(`app/api/errors.py:32-52`).

```json
{
  "error": {
    "code": "에러_코드",
    "message": "사람이 읽을 수 있는 설명"
  }
}
```

### 5-1. 도메인 에러 코드 목록

`app/core/errors.py`에 정의된 에러 클래스와 HTTP 상태 코드입니다.

| `code` | HTTP | 의미 | 파일:줄 |
|--------|------|------|---------|
| `UNAUTHORIZED` | 401 | API 키 불일치, HMAC 서명 오류, 타임스탬프 초과, nonce 재사용 | `errors.py:48` |
| `FORBIDDEN` | 403 | IP 화이트리스트 미포함 | `errors.py:58` |
| `NOT_FOUND` | 404 | 구독·요금제·서비스 등 리소스 없음 | `errors.py:31` |
| `CONFLICT` | 409 | 동일 서비스+사용자에 구독 이미 존재 등 중복 생성 | `errors.py:38` |
| `VALIDATION_ERROR` | 422 | Pydantic 필드 검증 실패 또는 비즈니스 규칙 위반 | `errors.py:68` |
| `RATE_LIMITED` | 429 | 분당 요청 한도 초과(일반 120/분, 결제 20/분) | `errors.py:79` |
| `PAYMENT_FAILED` | 402 | 토스 결제 승인 실패 | `errors.py:89` |
| `TOSS_KEY_NOT_CONFIGURED` | 422 | 서비스에 토스 시크릿 키 미설정 — 결제 불가 | `errors.py:112` |
| `SERVER_DISABLED` | 503 | 킬스위치 — 어드민에서 서버 비활성화됨 | `errors.py:100` |
| `DOMAIN_ERROR` | 400 | 기타 비즈니스 규칙 위반 | `errors.py:10` |
| `INTERNAL_ERROR` | 500 | 예상하지 못한 서버 오류 | `errors.py:47-52` |

**422 에러 응답 예시** (`app/api/errors.py:39-45`):
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "요청 형식이 올바르지 않습니다: amount, external_user_id"
  }
}
```

### 5-2. 결제 실패 코드(`failure_code`) 의미

결제가 `status=FAILED`이면 `failure_code`에 토스 에러 코드 또는 우리 서버 내부 코드가 들어갑니다.
매핑 파일: `app/admin/payment_error_labels.py:12`

| `failure_code` | 의미 |
|----------------|------|
| `REJECT_CARD_COMPANY` | 카드사에서 결제 승인을 거절했습니다 |
| `REJECT_CARD_PAYMENT` | 한도 초과 또는 잔액 부족으로 결제가 거절되었습니다 |
| `INVALID_STOPPED_CARD` | 정지된 카드입니다 |
| `INVALID_CARD_LOST_OR_STOLEN` | 분실 또는 도난 신고된 카드입니다 |
| `BELOW_MINIMUM_AMOUNT` | 최소 결제금액 미만입니다(신용카드 100원·계좌 200원 이상) |
| `EXCEED_MAX_AMOUNT` | 거래금액 한도를 초과했습니다 |
| `EXCEED_MAX_MONTHLY_PAYMENT_AMOUNT` | 당월 결제 가능 금액(100만원)을 초과했습니다 |
| `PROVIDER_ERROR` | 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해야 합니다 |
| `NO_BILLING_KEY` | 등록된 결제수단(빌링키)이 없습니다. 카드를 다시 등록해야 합니다 |
| `CANCEL_DISABLED` | 이 서비스는 결제 취소가 허용되지 않습니다 |
| `PAYMENT_UNRESOLVED` | 결제 결과가 아직 확인되지 않았습니다(타임아웃). 정산 스윕이 재확인합니다 |
| `SERVER_DISABLED` | 결제서버가 일시 비활성화(점검) 상태입니다 |

> 매핑에 없는 코드가 나오면 `payment_error_meaning()`이 빈 문자열을 반환하고 `failure_message` 원문을 사용합니다(`app/admin/payment_error_labels.py:51-55`). 새 코드가 나오면 딕셔너리에 추가하세요.

### 5-3. 클라이언트에서 에러 처리하기

`sample_service/shop/payment_client.py:26-67`의 `PaymentAPIError` 패턴을 참고하세요:

```python
# sample_service/shop/payment_client.py:26-67
class PaymentAPIError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status   # HTTP 상태 코드
        self.code = code       # error.code 문자열
        self.message = message # error.message 문자열

# 에러 파싱 방법
if resp.status_code >= 400:
    err = resp.json()["error"]
    raise PaymentAPIError(resp.status_code, err["code"], err["message"])
```

특히 **401 에러**는 키가 변경된 경우이므로 키 재입력 화면으로 안내해야 합니다
(`sample_service/shop/views.py:70-84`).

---

## 6. 유지보수 팁

### 6-1. 새 API 엔드포인트 추가 절차

1. **스키마 정의** → `app/schemas/api.py`에 Request/Response Pydantic 모델 추가
2. **라우트 추가** → 관련 `app/api/v1/*.py`에 `@router.post/get(...)` 함수 추가
   - 읽기 전용: `Depends(authenticate_service)`
   - 결제 관련: `Depends(payment_rate_limit)`
   - 무인증 공개: 헤더 없음(신중히 결정)
3. **서비스 레이어** → `app/services/` 하위 파일에 비즈니스 로직 구현
4. **테스트** → `tests/integration/` 에 통합 테스트 추가
5. **문서 갱신** → 이 파일(15) 및 관련 기능 문서 업데이트

**예시**: 요금제 상세 조회 엔드포인트를 추가한다면:
```python
# app/schemas/api.py 에 추가
class PlanDetailResponse(BaseModel):
    ...

# app/api/v1/plans.py 에 추가
@router.get("/plans/{plan_id}")
async def get_plan(plan_id: uuid.UUID,
                   service: Service = Depends(authenticate_service),
                   db: AsyncSession = Depends(get_db)):
    ...
```

### 6-2. 버저닝 — v1 유지 방침

현재 모든 외부 API는 `/api/v1/` 접두어를 사용합니다(`app/main.py:78`).

- 기존 클라이언트가 있는 엔드포인트를 **변경할 때는** 절대 경로나 요청 형식을 바꾸지 않습니다.
- 호환성이 깨지는 변경이 필요하면 `/api/v2/`를 추가하고 `app/api/v1/__init__.py`와 동일한 방식으로 `app/api/v2/__init__.py`를 만들어 `app/main.py`에 두 번째로 등록합니다.
- 현재는 v1만 존재하므로 v2 라우터 파일은 아직 없습니다.

### 6-3. 키 전달 방법 — 외부 서비스 담당자에게

상세 절차는 [09. 서비스 등록](09-services-registry.md)을 보세요. 요약하면:

1. 어드민 콘솔 `/admin/services` → 서비스 등록
2. 등록 직후 화면에 **API 키(svc_ 접두어)** 와 **HMAC Secret** 이 **1회만** 표시됩니다.
3. 이 두 값을 복사해 담당자에게 안전한 채널로 전달합니다(화면을 닫으면 다시 볼 수 없음).
4. 담당자는 받은 키를 외부 서비스의 환경변수 또는 `ServiceCredential` DB에 저장합니다.

> **API 키 원문은 서버 DB에 저장되지 않습니다.** DB에는 SHA-256 해시만 있습니다(`app/models/service.py`, `app/core/security.py:45`). 잃어버리면 어드민에서 키를 재발급해야 합니다(구 키는 즉시 무효화).

### 6-4. OpenAPI(Swagger) 문서 — HTTP Basic 인증

`/docs`(Swagger UI)와 `/openapi.json`은 **`SWAGGER_ID`/`SWAGGER_PW`가 둘 다 설정된 경우에만** 노출되며, 접속 시 HTTP Basic 인증(브라우저 로그인 팝업)을 요구합니다. 하나라도 비어 있으면 환경과 무관하게 **404**입니다. 기본 docs 라우트는 끄고 `app/main.py`의 `_register_protected_docs()`가 인증을 건 커스텀 라우트를 등록합니다(`secrets.compare_digest`로 비교).

```python
# main.py: 자격증명이 모두 설정된 경우에만 docs 라우트 등록
docs_url=None, redoc_url=None, openapi_url=None   # 기본 라우트 비활성화
_register_protected_docs(app, app_settings)        # Basic 인증 + 커스텀 /docs·/openapi.json
```

Swagger 화면 자체도 **자체 사용 설명서 수준으로 보강**되어 있습니다:
- 문서 상단(`API_DESCRIPTION`)에 인증 헤더 4종, HMAC 서명 알고리즘(canonical string), Python 예제, 에러 코드 표, 사용 흐름을 안내
- 태그(services/plans/subscriptions/payments/webhooks)별 설명(`openapi_tags`)
- 요청/응답 스키마 필드 설명·예시(`Field(description=, examples=)`)와 엔드포인트별 `response_model`·`responses`(401/403/404/409/422/429 등)

단, 외부 API 엔드포인트는 HMAC 서명 헤더가 필요해 Swagger UI 기본 "Try it out"으로는
서명을 자동 계산할 수 없습니다. 위의 curl 또는 Python 예시로 헤더를 직접 만들어 호출하세요.

### 6-5. 처리율 제한 확인

| 대상 | 한도 | 설정 위치 |
|------|------|-----------|
| 일반 API (읽기/취소 등) | 120/분 | `app/core/config.py` `rate_limit_per_minute` |
| 결제 API (구독생성/수동결제/카드변경/단건결제) | 20/분 | `app/core/config.py` `rate_limit_payment_per_minute` |

한도 초과 시 `429 RATE_LIMITED` 응답이 반환됩니다. 결제 테스트 중 429가 나오면
1분 기다렸다가 다시 시도하세요.

---

## 참고 — 관련 파일 빠른 찾기

| 역할 | 파일 |
|------|------|
| 외부 API 엔드포인트 (구독) | `app/api/v1/subscriptions.py` |
| 외부 API 엔드포인트 (결제) | `app/api/v1/payments.py` |
| 외부 API 엔드포인트 (서비스 목록) | `app/api/v1/services.py` |
| 외부 API 엔드포인트 (요금제) | `app/api/v1/plans.py` |
| 외부 API 엔드포인트 (웹훅) | `app/api/v1/webhooks.py` |
| 라우터 등록 | `app/api/v1/__init__.py`, `app/main.py:78` |
| 요청/응답 스키마 | `app/schemas/api.py` |
| 인증 Depends | `app/api/deps.py` |
| HMAC 서명 계산 | `app/core/security.py:62-75` |
| 에러 코드 정의 | `app/core/errors.py` |
| 에러 핸들러 | `app/api/errors.py` |
| 결제 실패 코드 설명 | `app/admin/payment_error_labels.py` |
| 샘플 서비스 API 클라이언트 | `sample_service/shop/payment_client.py` |
| 샘플 서비스 뷰 | `sample_service/shop/views.py` |
| 샘플 서비스 모델 | `sample_service/shop/models.py` |
| 샘플 서비스 .env 예시 | `sample_service/.env.example` |
