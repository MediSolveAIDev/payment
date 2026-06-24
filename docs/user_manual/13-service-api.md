# 13. 서비스 연동 API

이 문서는 사내 외부 서비스(진료 앱·쇼핑몰 등)가 구독·결제 서버에 직접 호출하는 **REST API 전체 레퍼런스**입니다. 인증(HMAC 서명)부터 카드·구독·결제·조회, 그리고 서버가 서비스로 보내는 알림 수신까지 한곳에 정리했습니다.

> 쉽게 말하면, 이 문서는 "내 서비스 코드가 구독·결제 서버와 주고받는 약속(요청·응답 형식)"을 그대로 적어 둔 사전입니다.

> 함께 보기: [카드 기능 코드 흐름](14-feature-card.md) · [구독 기능 코드 흐름](15-feature-subscription.md) · [결제 기능 코드 흐름](16-feature-payment.md) · [서비스 알림](17-feature-notifications.md)

> 참고: 모든 외부 API는 `/api/v1` 접두어로 등록됩니다(`app/main.py`). 예: `POST /api/v1/cards`.

---

## 13.1 공통 규칙

- **요청/응답 형식**: JSON. 인증이 필요한 요청에는 아래 4개 서명 헤더를 반드시 포함합니다.
- **금액 보호**: 구독 금액은 서버가 요금제(Plan)에서 직접 계산하므로 클라이언트가 보낼 수 없습니다. 단건 결제만 클라이언트가 `amount`를 지정하며, 이때도 HMAC 본문 서명이 금액 변조를 차단합니다.
- **민감 정보 비노출**: 빌링키(billingKey) 등 결제 키 원문은 어떤 응답에도 포함되지 않습니다. 카드는 마스킹 정보만 반환됩니다.
- **사용자 기준 키**: 대부분의 경로는 `{external_user_id}`(외부 서비스 측 사용자 식별자)를 사용합니다. (서비스 + 사용자 당 카드 1장·구독 1개 규칙의 기준)
- **`external_user_id`<span style="color:#e5484d">(이메일)</span>는 반드시 이메일**: 전역 룰로 `external_user_id`<span style="color:#e5484d">(이메일)</span>에는 **이메일만** 허용합니다. 서버가 받는 즉시 **앞뒤 공백 제거 + 소문자**로 정규화해 저장·조회하므로(`app/core/identifiers.py`), 대소문자만 다른 값(`User@x.com` vs `user@x.com`)은 같은 사용자로 취급됩니다. 이메일 형식이 아니면 `422`로 거부됩니다. 경로(`/cards/{external_user_id}` 등)에 이메일을 넣을 때 `+` 같은 특수문자는 URL 인코딩하세요. (HMAC 서명은 클라이언트가 보낸 원본 경로 기준이므로 정규화와 무관하게 동작합니다.)

---

## 13.2 인증 — HMAC 서명

모든 인증 필요 요청은 **API 키 + IP 화이트리스트 + HMAC 서명** 3중 검증을 통과해야 합니다(`app/api/deps.py:48` `authenticate_service`). 검증 순서는 ① API 키 해시 대조 → ② IP 화이트리스트 → ③ 처리율 제한 → ④ 타임스탬프 윈도우 → ⑤ HMAC 서명 → ⑥ nonce 1회용 소비입니다.

### 13.2.1 필수 헤더 4개

| 헤더 | 예시 값 | 설명 |
|------|---------|------|
| `x-service-key` | `svc_abc123...` | 서비스 API 키 원문. 어드민에서 1회 발급(`svc_` 접두어). |
| `x-timestamp` | `1749520800` | 요청 시각 Unix 초(정수 문자열). 서버 시각과 ±300초 이내여야 합니다. |
| `x-nonce` | `a1b2c3d4e5f6...` | 요청마다 다른 랜덤 문자열(UUID hex 권장). 600초 내 재사용 불가. |
| `x-signature` | `fa3c7d8e...` | 아래 정준 문자열의 HMAC-SHA256 서명(hex). |

> 주의: 타임스탬프 허용 오차는 `hmac_timestamp_tolerance_seconds`(기본 300초), nonce 1회용 키 TTL은 `hmac_nonce_ttl_seconds`(기본 600초)입니다(`app/core/config.py`). 같은 nonce를 600초 내 재사용하면 401로 거부됩니다(재전송 방어).

### 13.2.2 정준 문자열(canonical string)과 서명 계산식

서버 구현은 `app/core/security.py:62` `sign_request`입니다. 5개 구성요소를 줄바꿈(`\n`)으로 이어 붙인 뒤 HMAC-SHA256으로 서명합니다.

```text
{METHOD 대문자}
{path}
{timestamp}
{nonce}
{sha256_hex(요청본문 bytes)}
```

- `path`는 쿼리스트링을 제외한 경로 부분(예: `/api/v1/cards`)입니다.
- 본문이 없는 요청(GET·본문 없는 POST)도 빈 바이트(`b""`)를 SHA-256 해시합니다(`e3b0c44298fc...`).
- `method`/`path`/`timestamp`/`nonce`에 개행 문자가 들어오면 서명 계산이 거부됩니다(필드 간 바이트 이동 공격 방어, `app/core/security.py:69`).

서명 값:

```text
x-signature = HMAC_SHA256(hmac_secret, canonical_string)   # hex 인코딩
```

### 13.2.3 Python 예시

```python
import hashlib, hmac, json, time, uuid
import requests

BASE = "http://127.0.0.1:8000"          # 구독·결제 서버 주소
API_KEY = "svc_xxx"                     # 어드민에서 발급한 API 키
HMAC_SECRET = "xxx"                     # 〃 HMAC 시크릿

def sign_request(secret, method, path, timestamp, nonce, body):
    """app/core/security.py:62 sign_request 와 동일한 알고리즘."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

def call(method, path, json_body=None):
    body = json.dumps(json_body).encode() if json_body is not None else b""
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex            # 요청마다 새로 — 절대 재사용 금지
    headers = {
        "x-service-key": API_KEY,
        "x-timestamp": ts,
        "x-nonce": nonce,
        "x-signature": sign_request(HMAC_SECRET, method, path, ts, nonce, body),
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    resp = requests.request(method, BASE + path, headers=headers,
                            data=body or None, timeout=30)
    if resp.status_code >= 400:
        err = resp.json()["error"]
        raise Exception(f"{err['code']}: {err['message']}")
    return resp.json()
```

> 주의(자주 하는 실수): ① METHOD는 대문자, ② `path` 앞 `/` 포함·쿼리스트링 제외, ③ 빈 body도 `sha256("")`로 해시, ④ JSON 본문은 서명에 쓴 바이트와 실제 전송 바이트가 동일해야 함(`json.dumps()` 결과 그대로 전송).

---

## 13.3 조회 API — 서비스·요금제 목록

연동을 시작할 때 필요한 **읽기 전용** 두 엔드포인트입니다. 서비스 목록은 키 입력 전 단계에서 호출할 수 있도록 **무인증**이고, 요금제 목록은 일반 HMAC 인증이 필요합니다.

| 메서드·경로 | 인증 | 용도 | 라우트 |
|-------------|------|------|--------|
| `GET /api/v1/services` | **무인증** | 등록된 서비스 목록(id·이름·상태)만 조회 | `app/api/v1/services.py:20` |
| `GET /api/v1/plans` | HMAC | 인증된 서비스의 **활성(ACTIVE)** 요금제 목록 조회 | `app/api/v1/plans.py:15` |

### 13.3.1 서비스 목록 조회 — `GET /api/v1/services`

API 키 입력 전 단계에서 서비스를 식별·선택하기 위한 용도입니다. **인증이 필요 없으며**, 키·시크릿·구독 등 민감정보는 절대 포함하지 않습니다(`id`·`name`·`status`만). 이름 오름차순으로 정렬해 반환합니다.

> 주의: 운영 환경에서 사내 서비스 구성 노출이 우려되면 `public_service_list_enabled=false`로 이 엔드포인트를 끌 수 있습니다(`app/core/config.py`, 기본 true). 끄면 존재 자체를 숨기기 위해 **404**를 반환합니다.

**응답 (200)** (`ServiceListResponse`, `app/schemas/api.py:278`)

| 필드 | 타입 | 설명 |
|------|------|------|
| `services` | array | 서비스 항목 배열(이름 오름차순) |
| `services[].id` | string | 서비스 ID |
| `services[].name` | string | 서비스 이름 |
| `services[].status` | string | 서비스 상태: `ACTIVE` \| `INACTIVE` |

```json
{
  "services": [
    {"id": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "name": "진료 앱", "status": "ACTIVE"}
  ]
}
```

### 13.3.2 요금제 목록 조회 — `GET /api/v1/plans`

인증된 서비스에 속한 **활성(status=ACTIVE) 요금제만** 반환합니다(비활성 요금제는 외부에 노출하지 않아, 이미 판매 종료된 요금제로 신규 구독을 요청하는 것을 막습니다). 응답의 `id`를 구독 생성(`13.5.1`)의 `plan_id`로 사용합니다. 요청 본문은 없습니다.

**응답 (200)** (`PlanListResponse` → `PlanResponse` 배열, `app/schemas/api.py:47`)

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | UUID | 요금제 ID. 구독 생성 시 `plan_id`로 사용 |
| `name` | string | 요금제 이름 |
| `price` | int | 정가(원) |
| `amount` | int | 실제 정기 청구 금액(원). 상시 할인 적용 후 값이며, 할인이 없으면 `price`와 동일 |
| `currency` | string | 통화 코드(예: KRW) |
| `billing_cycle` | string | 결제 주기: `YEAR` \| `MONTH` \| `WEEK` \| `DAY` \| `MINUTE` |
| `cycle_days` | int \| null | `DAY` 주기일 때의 실제 일수. 그 외 주기에서는 null |
| `cycle_minutes` | int \| null | `MINUTE` 주기일 때의 실제 분(5 이상). 그 외 null. 테스트용·비운영 전용 |
| `first_payment_type` | string | 첫 결제 혜택 유형: `NONE` \| `FREE` \| `DISCOUNT_AMOUNT` \| `DISCOUNT_PERCENT` |
| `first_payment_value` | int \| null | 첫 결제 할인 값(정액=원, 정률=%). 혜택 없으면 null |
| `trial_enabled` | bool | 체험 제공 여부. **true일 때만** 구독 생성에서 `trial=true` 가능 |
| `trial_days` | int \| null | 체험 일수. 체험 미제공 시 null |
| `auto_renew` | bool | 자동갱신 여부. false면 첫 주기 종료 후 자동결제 없이 만료 |
| `extra_info` | object | 서비스 측 요금제 부가 정보(key/value) |

```json
{
  "plans": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "name": "스탠다드 월간",
      "price": 10000,
      "amount": 9000,
      "currency": "KRW",
      "billing_cycle": "MONTH",
      "cycle_days": null,
      "cycle_minutes": null,
      "first_payment_type": "FREE",
      "first_payment_value": null,
      "trial_enabled": true,
      "trial_days": 7,
      "auto_renew": true,
      "extra_info": {}
    }
  ]
}
```

---

## 13.4 카드 API

구독·단건 결제 전에 **카드를 먼저 등록**해야 합니다. 빌링키는 등록된 카드(카드 보관함)에서 서버가 자동 조회하므로, 구독·결제 요청에는 카드 정보를 넣지 않습니다.

| 메서드·경로 | 인증 | 용도 | 라우트 |
|-------------|------|------|--------|
| `POST /api/v1/cards` | HMAC + 결제 제한 | 카드 등록 또는 교체(빌링키 발급) | `app/api/v1/cards.py:40` |
| `GET /api/v1/cards/{external_user_id}` | HMAC | 등록 카드 마스킹 정보 조회(없으면 404) | `app/api/v1/cards.py:83` |
| `DELETE /api/v1/cards/{external_user_id}` | HMAC | 카드·빌링키 삭제(204) | `app/api/v1/cards.py:114` |

### 13.4.1 카드 등록 / 교체 — `POST /api/v1/cards`

(service, external_user_id)당 1장을 유지하며, 카드가 이미 있으면 기존 행을 교체하고 이전 빌링키를 best-effort 삭제합니다. 응답에 billingKey는 절대 포함되지 않습니다.

**요청 본문** (`CardRegisterRequest`, `app/schemas/api.py:305`)

| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| `external_user_id`<span style="color:#e5484d">(이메일)</span> | string | 이메일, 1–255자 | 외부 서비스 측 사용자 식별자(이메일·소문자 정규화) |
| `customer_key` | string | 2–300자 | 토스 customerKey(고객 식별자, 최소 2자) |
| `auth_key` | string | 1–300자 | 토스 결제창에서 발급받은 1회용 authKey(빌링키 발급에 사용) |

```json
{
  "external_user_id": "user@example.com",
  "customer_key": "cust-123",
  "auth_key": "toss_auth_key_xxx"
}
```

> 중요: `customer_key`와 `auth_key`는 이 API를 호출하기 **전에 서비스 클라이언트(앱/웹)에서 토스 결제창(빌링 인증)**으로 얻습니다. 결제 서버가 발급하는 값이 아닙니다. 흐름은 다음과 같습니다.
>
> 1. 서비스가 사용자별로 정한 `customerKey`(중복 없는 고객 식별자)로 **토스 SDK 빌링 인증창**(`requestBillingAuth`)을 띄웁니다.
> 2. 사용자가 카드 인증을 마치면, 토스가 지정한 successUrl로 **1회용 `authKey`**를 돌려줍니다.
> 3. 서비스 **서버**가 이 `customerKey`/`authKey`를 받아 `POST /api/v1/cards`로 전달하면, 결제 서버가 토스에 **빌링키 발급**을 요청해 카드 보관함에 암호화 저장합니다.
>
> `authKey`는 **1회용**이라 빌링키 발급에 한 번 쓰면 재사용할 수 없습니다(재발급은 인증창부터 다시). 토스 결제창(빌링) 연동 자체는 `docs/toss/3.SDK`·`docs/toss/1.가이드` 또는 [토스페이먼츠 개발자 문서](https://docs.tosspayments.com)를 참고하세요.

**응답 (201)** (`CardResponse`, `app/schemas/api.py:326`)

| 필드 | 타입 | 설명 |
|------|------|------|
| `external_user_id`<span style="color:#e5484d">(이메일)</span> | string | 외부 서비스 측 사용자 식별자 |
| `card` | object \| null | 카드 마스킹 정보(issuerCode·number 등 표시용). 정보 없으면 null |

```json
{
  "external_user_id": "user@example.com",
  "card": {"issuerCode": "61", "number": "123456******1234"}
}
```

### 13.4.2 카드 조회 — `GET /api/v1/cards/{external_user_id}`

응답 형식은 등록과 동일(`CardResponse`)합니다. 등록된 카드가 없으면 404를 반환합니다.

### 13.4.3 카드 삭제 — `DELETE /api/v1/cards/{external_user_id}`

성공 시 본문 없이 **204 No Content**를 반환합니다.

> 주의: billing-active 상태(TRIAL·ACTIVE·PAST_DUE·SUSPENDED·EXTENDED)의 구독이 이 카드를 사용 중이면 **409(CONFLICT)** 로 삭제가 거부됩니다. 카드가 없으면 404입니다.

---

## 13.5 구독 API

> 중요: 구독 생성 전에 반드시 `POST /api/v1/cards`로 카드를 먼저 등록해야 합니다. 구독 요청에는 카드/빌링키 정보를 넣지 않으며, 서버가 등록된 카드에서 빌링키를 조회합니다(`app/api/v1/subscriptions.py:80`).

| 메서드·경로 | 인증 | 용도 | 라우트 |
|-------------|------|------|--------|
| `POST /api/v1/subscriptions` | HMAC + 결제 제한 | 구독 생성(trial 가능) | `app/api/v1/subscriptions.py:61` |
| `GET /api/v1/subscriptions/{external_user_id}` | HMAC | 최근 구독 조회(없으면 404) | `app/api/v1/subscriptions.py:143` |
| `POST /api/v1/subscriptions/{external_user_id}/cancel` | HMAC | 취소 예약(만료일에 자동 종료) | `app/api/v1/subscriptions.py:167` |
| `POST /api/v1/subscriptions/{external_user_id}/resume` | HMAC | 취소 예약 철회(재개) | `app/api/v1/subscriptions.py:189` |
| `POST /api/v1/subscriptions/{external_user_id}/pay` | HMAC + 결제 제한 | 정지(SUSPENDED) 구독 수동 결제 복구 | `app/api/v1/subscriptions.py:92` |
| `POST /api/v1/subscriptions/{external_user_id}/add-days` | HMAC | 사용일 추가(만료일·다음 결제일 연장) | `app/api/v1/subscriptions.py:118` |

### 13.5.1 구독 생성 — `POST /api/v1/subscriptions`

**요청 본문** (`SubscriptionCreateRequest`, `app/schemas/api.py:17`)

| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| `external_user_id`<span style="color:#e5484d">(이메일)</span> | string | 이메일, 1–255자 | 외부 서비스 측 사용자 식별자(이메일·소문자 정규화, 서비스+사용자 당 구독 1개 기준) |
| `plan_id` | UUID | - | 구독할 요금제 ID(요금제 목록 응답의 id) |
| `trial` | bool | 기본 false | true이면 체험 시작. 요금제 `trial_enabled=true`일 때만 허용(아니면 422) |

```json
{
  "external_user_id": "user@example.com",
  "plan_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "trial": false
}
```

> 참고: 금액 필드가 없습니다. 서버가 요금제에서 계산하므로 클라이언트가 금액을 조작할 수 없습니다(`app/schemas/api.py:37`).

**응답 (201)** (`SubscriptionResponse`, `app/schemas/api.py:87`)

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | UUID | 구독 ID |
| `external_user_id`<span style="color:#e5484d">(이메일)</span> | string | 외부 서비스 측 사용자 식별자 |
| `plan_id` | UUID | 구독한 요금제 ID |
| `plan_name` | string | 구독한 요금제 이름 |
| `status` | string | TRIAL \| ACTIVE \| PAST_DUE \| SUSPENDED \| CANCELED \| EXPIRED |
| `access_allowed` | bool | **서비스 접근 허용 여부**. TRIAL/ACTIVE/PAST_DUE/CANCELED=true, SUSPENDED/EXPIRED=false |
| `current_period_start` | datetime | 현재 결제 주기 시작 시각 |
| `current_period_end` | datetime | 현재 결제 주기 종료(만료) 시각 |
| `next_billing_at` | datetime \| null | 다음 자동결제 예정 시각. 해지 예약·만료 시 null |
| `card` | object \| null | 등록 카드 마스킹 정보. 미등록 시 null |
| `retry_count` | int | PAST_DUE 상태에서의 결제 재시도 횟수 |

```json
{
  "id": "aabbccdd-1111-2222-3333-444455556666",
  "external_user_id": "user@example.com",
  "plan_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "plan_name": "스탠다드 월간",
  "status": "ACTIVE",
  "access_allowed": true,
  "current_period_start": "2026-06-10T03:00:00Z",
  "current_period_end": "2026-07-10T03:00:00Z",
  "next_billing_at": "2026-07-10T02:55:00Z",
  "card": {"issuerCode": "61", "number": "123456******1234"},
  "retry_count": 0
}
```

> 중요: 외부 서비스는 `access_allowed` 값으로 사용자의 서비스 접근을 판단하세요(`app/schemas/api.py:101`). 상태 문자열을 직접 해석하기보다 이 불리언 한 개를 쓰는 것이 안전합니다.

### 13.5.2 조회 / 취소 / 재개

- **조회**: 가장 최근 구독을 `SubscriptionResponse`로 반환합니다. 구독이 없으면 404.
- **취소 예약**: 즉시 삭제가 아니라 **만료일이 되면 자동 종료**됩니다. 취소 예약 후에도 만료 전까지는 `access_allowed=true`(CANCELED).
- **재개**: 취소 예약을 철회해 원래 상태로 복귀합니다. 셋 다 요청 본문이 없고 응답은 `SubscriptionResponse`입니다.

### 13.5.3 수동 결제(정지 구독 복구) — `POST /.../pay`

정지(SUSPENDED) 구독의 미수금을 즉시 결제합니다. 성공 시 ACTIVE로 복귀하고 기준일이 리셋됩니다. 토스 청구가 발생하므로 결제 전용 처리율 제한이 적용됩니다. 요청 본문 없음, 응답은 갱신된 `SubscriptionResponse`.

### 13.5.4 사용일 추가 — `POST /.../add-days`

이용 중(ACTIVE·EXTENDED·PAST_DUE) 구독의 만료일·다음 결제일을 `days`만큼 미룹니다(상태는 변경하지 않음). 토스 결제 호출이 없으므로 일반 HMAC 인증으로 충분합니다.

**요청 본문** (`UsageDaysRequest`, `app/schemas/api.py:160`)

| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| `days` | int | 1–3650 | 추가할 사용일수 |

```json
{ "days": 30 }
```

> 주의: 대상 상태(ACTIVE·EXTENDED·PAST_DUE)가 아니면 **409(CONFLICT)**, 구독이 없으면 404를 반환합니다.

---

## 13.6 결제 API (단건/1회성)

구독과 무관한 1회성 결제입니다. 단건 결제도 **사전 등록된 카드**(`POST /cards`)를 사용하며 빌링키는 서버가 카드 보관함에서 자동 조회합니다.

| 메서드·경로 | 인증 | 용도 | 라우트 |
|-------------|------|------|--------|
| `POST /api/v1/payments` | HMAC + 결제 제한 | 단건 결제 생성(즉시 청구) | `app/api/v1/payments.py:65` |
| `POST /api/v1/payments/{order_id}/cancel` | HMAC + 결제 제한 | 단건 결제 취소(환불, 수수료 공제) | `app/api/v1/payments.py:102` |
| `GET /api/v1/payments/{external_user_id}` | HMAC | 결제 내역 조회(최신순 최대 50건) | `app/api/v1/payments.py:37` |

### 13.6.1 단건 결제 생성 — `POST /api/v1/payments`

**요청 본문** (`OneOffPaymentRequest`, `app/schemas/api.py:129`)

| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| `external_user_id`<span style="color:#e5484d">(이메일)</span> | string | 1–255자 | 결제 대상 사용자 식별자 |
| `order_id` | string | 6–64자 | 주문 ID. **서비스 내 고유**(타 서비스와는 중복 가능). 같은 order_id 재시도는 기존 결제 반환(멱등) |
| `order_name` | string | 1–100자 | 결제창에 표시되는 주문명 |
| `amount` | int | 0 초과, 1억원 이하 | 결제 금액(원). 클라이언트가 지정하며 HMAC 본문 서명이 변조를 차단 |

```json
{
  "external_user_id": "user@example.com",
  "order_id": "order-20260610-0001",
  "order_name": "프리미엄 1회 이용권",
  "amount": 10000
}
```

> 주의: 카드 미등록 시 404를 반환합니다. 타임아웃(결과 불명) 시 결제는 PENDING으로 유지되어 이중 결제를 방지합니다(`app/api/v1/payments.py:87`).

응답은 `PaymentResponse`(아래 13.6.4)입니다.

### 13.6.2 단건 결제 취소 — `POST /api/v1/payments/{order_id}/cancel`

서비스 취소 정책에 따라 환불(수수료 공제)합니다.

**요청 본문** (`OneOffCancelRequest`, `app/schemas/api.py:170`)

| 필드 | 타입 | 제약 | 설명 |
|------|------|------|------|
| `reason` | string | 1–200자, 기본 "사용자 취소" | 취소 사유. 토스 취소 API의 cancelReason으로 전달 |

```json
{ "reason": "고객 요청으로 취소" }
```

> 주의: 서비스 정책이 취소 비허용(`cancellation_enabled=false`)이거나 결제가 완료(DONE) 상태가 아니면 오류를 반환합니다. 취소 성공 시 `status=CANCELED`와 `canceled_amount`/`cancel_fee`가 채워진 결과를 반환합니다.

### 13.6.3 결제 내역 조회 — `GET /api/v1/payments/{external_user_id}`

해당 사용자의 결제 내역을 최신순 최대 50건 반환합니다. **구독 정기결제와 단건(ONE_OFF) 결제를 모두 포함**합니다. `Payment.service_id`로 범위가 격리되어 다른 서비스의 결제는 보이지 않습니다.

```json
{
  "payments": [ /* PaymentResponse 객체 배열 */ ]
}
```

### 13.6.4 결제 응답(`PaymentResponse`)과 취소/환불 필드

`PaymentResponse`(`app/schemas/api.py:183`)는 결제 결과 + 서비스 취소 정책에서 만들어집니다. `toss_payment_key`·`raw_response` 등 내부 필드는 노출되지 않습니다.

| 필드 | 타입 | 설명 |
|------|------|------|
| `order_id` | string | 주문 ID |
| `amount` | int | 실제 청구된 금액(원) |
| `status` | string | PENDING \| DONE \| FAILED \| CANCELED |
| `kind` | string | SUBSCRIPTION(구독 정기) \| ONE_OFF(단건) |
| `payment_type` | string | FIRST \| RENEWAL \| RETRY \| ONE_OFF |
| `failure_code` | string \| null | 실패 코드. status=FAILED일 때만 값 존재 |
| `failure_message` | string \| null | 실패 사유 메시지. status=FAILED일 때만 값 존재 |
| `requested_at` | datetime | 결제 요청 시각 |
| `approved_at` | datetime \| null | 승인 시각. 실패·대기 중에는 null |
| `receipt_url` | string \| null | **토스 매출전표(영수증) URL**. 카드결제(DONE)는 보통 존재, 가상계좌·실패·대기·과거 미보유 건은 null. 새 탭으로 열어 영수증을 보여줄 수 있음 |
| `cancelable` | bool | **지금 취소 가능 여부**. 단건(ONE_OFF)·완료(DONE)·미취소·서비스 취소허용일 때만 true |
| `cancel_fee_percent` | int | 서비스 취소 수수료율(%) |
| `cancel_fee` | int | 취소 시 차감 수수료(원). 취소 가능 결제는 **예상액**, 이미 취소된 결제는 **실제 차감액** |
| `cancel_refund_amount` | int | 환불액(원). 취소 가능 결제는 **예상액**, (부분/전액) 취소된 결제는 **실제 누적 환불액** |
| `canceled_amount` | int | **실제 환불된 누적 금액(원)**. 어드민 부분취소 시 status는 DONE이지만 이 값이 0보다 큼 |
| `net_amount` | int | 실수령(순) 금액(원) = `amount − canceled_amount`. 부분취소 반영 |

> 참고: 취소 수수료는 실제 취소 처리와 동일한 계산을 공유합니다 → `cancel_fee = amount × cancel_fee_percent ÷ 100`(내림), `cancel_refund_amount = amount − cancel_fee`. 따라서 결제 내역만으로 "지금 취소하면 얼마가 빠지고 얼마가 환불되는지"를 화면에 미리 안내할 수 있습니다.

> 중요(부분취소 반영): 관리자가 어드민에서 단건 결제를 **부분취소**하면 `status`는 `DONE`을 유지한 채 `canceled_amount`만 누적됩니다. 외부 서비스는 `status == "CANCELED"`만으로 취소를 판정하지 말고 **`canceled_amount`/`net_amount`로 실제 환불·실수령을 표시**하세요. 이미 (부분)취소된 결제는 `cancelable=false`라 외부에서 추가 취소할 수 없습니다.

```json
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
  "receipt_url": "https://dashboard.tosspayments.com/receipt/...",
  "cancelable": true,
  "cancel_fee_percent": 10,
  "cancel_fee": 1000,
  "cancel_refund_amount": 9000,
  "canceled_amount": 0,
  "net_amount": 10000
}
```

---

## 13.7 서비스 알림 수신(아웃고잉 웹훅)

구독·결제·카드·요금제 상태가 바뀌면, 서버가 서비스 상세에 등록된 **알림 URL**로 JSON 알림을 POST합니다. (어드민 → 서비스 상세 → '서비스 알림 URL'에 등록, 비우면 끔)

- **best-effort(fire-and-forget)**: 알림 전송은 백그라운드 단발 POST(타임아웃 5초)로 처리되며, **실패해도 재시도하지 않습니다**(결제·구독 본 처리에는 영향 없음, 로그만 남김). 수신 측 전달 보장·멱등 처리 규약은 [서비스 알림](17-feature-notifications.md)을 반드시 참고하세요.
- **헤더**: `Content-Type: application/json` + `X-Event`(이벤트 이름) + `X-Signature`/`X-Timestamp`/`X-Nonce`(서명 3종)으로 보냅니다.
- **서명**: 서비스의 HMAC 시크릿으로 서명합니다. API 호출 서명과 **완전히 동일한 방식**입니다(`X-Event`는 서명 대상이 아니며 라우팅 편의용 힌트입니다).

### 13.7.1 payload 구조

```json
{
  "EVENT": "payment.one_off",
  "subscribe_id": "",        // 구독 ID(구독 이벤트만)
  "order_id": "...",         // 결제 주문번호(결제 이벤트만)
  "PRE_STATUS": "",          // 이전 상태(상태 변화 시)
  "STATUS": "DONE",          // 새 상태
  "service_name": "...",
  "email": "...",            // 관련 사용자(external_user_id)
  "date": "YYYY-MM-DD HH:MM:SS",  // KST
  "DESC": "금액 등 상세 설명"
}
```

> 참고: 없는 값은 빈 문자열입니다. 요금제 이벤트는 사용자 비귀속이라 `subscribe_id`/`order_id`/`email`이 빈값이고 `DESC`에 요금제명·상세가 담깁니다.

### 13.7.2 이벤트 목록(EVENT)

| 상황 | EVENT |
|------|-------|
| 새로운 구독자 발생 | `subscription.created` |
| 구독 상태 변화(취소·재개·미수·정지·만료·수동결제복구) | `subscription.status_changed` |
| 구독 자동결제 발생 | `subscription.renewed` |
| 관리자 강제 구독취소 | `subscription.force_canceled` |
| 만료일 연장 | `subscription.extended` |
| 카드 등록 / 변경 / 삭제 | `card.registered` / `card.replaced` / `card.deleted` |
| 관리자 카드 활성화 / 비활성화 | `card.activated` / `card.deactivated` |
| 사용자 일반결제 | `payment.one_off` |
| 사용자 일반결제 취소 | `payment.one_off_canceled` |
| 관리자 일반결제 취소(전액/부분) | `payment.one_off_admin_canceled` |
| 요금제 활성화 / 비활성화 / 삭제 | `plan.activated` / `plan.archived` / `plan.deleted` |
| 요금제 사용일 추가 | `plan.bonus_days` |
| 테스트 알림(어드민 버튼) | `notification.test` |

상수 정의: `app/notifications/service_notify.py`.

### 13.7.3 서명 검증(수신 측)

받은 알림이 진짜 서버에서 온 것인지 아래 방식으로 검증합니다(API 호출 서명과 동일).

```text
canonical = "POST\n{path}\n{X-Timestamp}\n{X-Nonce}\n{sha256_hex(body)}"
X-Signature == HMAC_SHA256(service_hmac_secret, canonical)
```

- `path`는 알림 URL의 경로 부분(예: `https://svc/hooks/notify` → `/hooks/notify`)입니다.
- `body`는 받은 요청의 원문 바이트 그대로(파싱 전)를 SHA-256 해시합니다.

```python
import hashlib, hmac

def verify_notification(secret, path, headers, body_bytes):
    """서버가 보낸 알림 서명을 검증한다 — 13.2.2 sign_request와 동일."""
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    canonical = "\n".join([
        "POST", path, headers["X-Timestamp"], headers["X-Nonce"], body_hash,
    ])
    expected = hmac.new(secret.encode(), canonical.encode(),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, headers["X-Signature"])
```

> 참고: 수신 데모는 `sample_service`의 `POST /notify`(서명 검증 후 저장)와 `/notifications`(받은 알림 목록) 화면에 있습니다. 어드민의 '테스트 알림 전송' 버튼으로 연결을 즉시 확인할 수 있습니다.

### 13.7.4 토스 웹훅 수신 — `POST /api/v1/webhooks/toss`

> 참고: 이 엔드포인트는 **토스페이먼츠 → 결제 서버** 방향의 결제 이벤트 수신용입니다. 연동 서비스가 직접 호출하지 않습니다(참고용으로만 기재).

| 메서드·경로 | 인증 | 용도 | 라우트 |
|-------------|------|------|--------|
| `POST /api/v1/webhooks/toss` | **무인증**(IP 검증 선택) | 토스 결제 이벤트 수신·처리 | `app/api/v1/webhooks.py:29` |

- **인증 없음**: HMAC 4헤더를 쓰지 않습니다. 대신 `webhook_ip_check_enabled=true`이면 `toss_webhook_allowed_ips` 화이트리스트로 발신 IP를 검증하며(미포함 시 **403**), 프로덕션에서는 반드시 켜야 합니다.
- **중복 방지**: `tosspayments-webhook-transmission-id` 헤더로 동일 이벤트 재전송(at-least-once)을 Redis로 1회 처리합니다.
- **응답 (200)** (`WebhookAck`): 처리 상태만 반환합니다.

```json
{ "status": "DONE" }
```

---

## 13.8 오류 응답 형식과 상태 코드

모든 API 오류는 아래 공통 형식으로 반환됩니다(`ErrorResponse`, `app/schemas/api.py:299`).

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "인증에 실패했습니다"
  }
}
```

| `code` | HTTP | 의미 |
|--------|------|------|
| `UNAUTHORIZED` | 401 | API 키 불일치, HMAC 서명 오류, 타임스탬프 초과, nonce 재사용 |
| `PAYMENT_FAILED` | 402 | 토스 결제 승인 실패 |
| `FORBIDDEN` | 403 | IP 화이트리스트 미포함 |
| `NOT_FOUND` | 404 | 구독·요금제·카드·서비스 등 리소스 없음 |
| `CONFLICT` | 409 | 구독 중복 생성, 사용 중 카드 삭제, add-days 대상 상태 아님 등 |
| `VALIDATION_ERROR` | 422 | 필드 검증 실패 또는 비즈니스 규칙 위반(trial 불가 등) |
| `RATE_LIMITED` | 429 | 분당 요청 한도 초과(일반 120/분, 결제 20/분) |
| `SERVER_DISABLED` | 503 | 킬스위치 — 어드민에서 서버 비활성화됨 |
| `DOMAIN_ERROR` | 400 | 기타 비즈니스 규칙 위반 |
| `INTERNAL_ERROR` | 500 | 예상하지 못한 서버 오류 |

> 주의: 결제 전용 엔드포인트(카드 등록·구독 생성·수동 결제·단건 결제·단건 취소)는 일반 한도(120/분) 위에 결제 전용 추가 한도(20/분)가 더 적용됩니다(`payment_rate_limit`, `app/api/deps.py:115`). 429가 나오면 1분 후 재시도하세요.

---

## 13.9 처음부터 끝까지 — 최소 연동 예제

13.2.3의 `call()` 헬퍼가 있다고 가정하고 **카드 등록 → 구독 생성 → 알림 수신**까지 잇는 최소 흐름입니다. `auth_key`는 13.4.1처럼 **클라이언트 토스 결제창에서 먼저 받아 둔** 값입니다.

```python
EXT_USER = "user@example.com"

# ① 카드 등록 — 클라이언트 토스 결제창에서 받은 customer_key/auth_key 전달
card = call("POST", "/api/v1/cards", {
    "external_user_id": EXT_USER,
    "customer_key": "cust-123",
    "auth_key": "toss_auth_key_xxx",   # 1회용(빌링키 발급에 한 번만 사용)
})

# ② 구독 생성 — 등록된 카드로 서버가 첫 결제(체험이면 생략) 후 구독 생성
sub = call("POST", "/api/v1/subscriptions", {
    "external_user_id": EXT_USER,
    "plan_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "trial": False,
})
print(sub["status"], sub["access_allowed"])   # 예: ACTIVE True

# ③ (서버 자동) 만료일이 되면 등록 카드로 자동결제·연장 — 서비스 호출 없음
# ④ 상태가 바뀔 때마다 서버가 '알림 URL'로 POST → 아래 수신 핸들러가 처리
```

수신 측(서비스 서버)은 13.7.3의 `verify_notification`으로 서명을 검증한 뒤 자기 DB를 갱신합니다. 단건 결제·취소·구독 취소/재개·내역 조회는 같은 `call()`로 **경로만 바꿔** 호출하면 됩니다(13.5·13.6 표 참고).

> 팁: 동작하는 전체 예제는 `sample_service/`에 있습니다 — 카드 등록 화면, `POST /notify`(서명 검증 후 저장), `/notifications`(받은 알림 목록)까지 한 흐름으로 따라갈 수 있습니다.

---

> 함께 보기: 카드 흐름 → [카드 기능 코드 흐름](14-feature-card.md) · 구독 흐름 → [구독 기능 코드 흐름](15-feature-subscription.md) · 결제 흐름 → [결제 기능 코드 흐름](16-feature-payment.md) · 알림 → [서비스 알림](17-feature-notifications.md)
