# 15. 서비스 알림(아웃고잉 웹훅) 기능

구독·결제·카드·요금제의 상태가 바뀌면, 본 시스템은 서비스가 등록한 `notification_url`로 JSON을 **POST**한다. 이를 "서비스 알림(아웃고잉 웹훅)"이라 부른다. 이 문서는 이벤트 종류, payload·서명 구조, best-effort 발송, 어드민 테스트 버튼, 샘플 수신 흐름을 코드와 함께 추적한다.

> 쉽게 말하면 "우리 쪽에서 어떤 일이 생겼을 때, 서비스가 알려준 주소로 서명된 JSON을 한 번 쏴 주는" 것이다. 받아도 그만 안 받아도 본 처리는 안 깨진다.

> 함께 보기: [서비스 API](11-service-api.md), [일반결제·취소·정산](14-feature-payment.md)

## 15.1 기능 개요·관련 파일

### 핵심 성질
1. **best-effort(fire-and-forget)** — 실제 POST는 백그라운드 태스크로 보내고, 실패해도 본 처리(결제·구독)에는 영향을 주지 않는다(로그만 남김).
2. **HMAC 서명** — 서비스의 기존 HMAC 시크릿(`hmac_secret_encrypted`)을 재사용해 `X-Signature`/`X-Timestamp`/`X-Nonce` 헤더로 보낸다(수신 측이 진위 검증 가능).
3. **URL 미등록이면 발송 안 함** — `notification_url`이 비어 있으면 아무것도 보내지 않는다.

### 관련 파일

| 역할 | 파일 |
| --- | --- |
| 이벤트 상수·payload 구성·발송기(Notifier) | `app/notifications/service_notify.py` |
| 알림 URL 저장·테스트 버튼 라우트 | `app/admin/routes/services.py` |
| HMAC 서명 함수 | `app/core/security.py`(`sign_request`) |
| 이벤트 발생 지점(구독/결제/카드/요금제) | `app/services/subscriptions.py`, `renewals.py`, `payments.py`, `cards.py`, `plans.py` |
| 샘플 수신기(검증·저장·표시) | `sample_service/shop/views.py` |

## 15.2 이벤트 17종(+테스트 1종)

`app/notifications/service_notify.py:29`~`46`에 이벤트 식별자 상수가 정의돼 있다. 이 값은 payload의 `EVENT` 필드와 `X-Event` 헤더에 들어간다.

| 분류 | 상수 | EVENT 값 |
| --- | --- | --- |
| 구독 | `EVENT_SUBSCRIPTION_CREATED` | `subscription.created` |
| 구독 | `EVENT_SUBSCRIPTION_STATUS` | `subscription.status_changed` |
| 구독 | `EVENT_SUBSCRIPTION_RENEWED` | `subscription.renewed` |
| 구독 | `EVENT_SUBSCRIPTION_FORCE_CANCELED` | `subscription.force_canceled` |
| 구독 | `EVENT_SUBSCRIPTION_EXTENDED` | `subscription.extended` |
| 카드 | `EVENT_CARD_REGISTERED` | `card.registered` |
| 카드 | `EVENT_CARD_REPLACED` | `card.replaced` |
| 카드 | `EVENT_CARD_DELETED` | `card.deleted` |
| 카드 | `EVENT_CARD_ACTIVATED` | `card.activated` |
| 카드 | `EVENT_CARD_DEACTIVATED` | `card.deactivated` |
| 결제 | `EVENT_PAYMENT_ONE_OFF` | `payment.one_off` |
| 결제 | `EVENT_PAYMENT_ONE_OFF_CANCELED` | `payment.one_off_canceled` |
| 결제 | `EVENT_PAYMENT_ONE_OFF_ADMIN_CANCELED` | `payment.one_off_admin_canceled` |
| 요금제 | `EVENT_PLAN_ACTIVATED` | `plan.activated` |
| 요금제 | `EVENT_PLAN_ARCHIVED` | `plan.archived` |
| 요금제 | `EVENT_PLAN_DELETED` | `plan.deleted` |
| 요금제 | `EVENT_PLAN_BONUS_DAYS` | `plan.bonus_days` |
| 테스트 | `EVENT_TEST` | `notification.test` |

> 참고: 표의 데이터 이벤트는 17종(구독 5·카드 5·결제 3·요금제 4)이고, 여기에 어드민 "테스트 알림 전송" 버튼이 쓰는 `notification.test`가 더해진다.

## 15.3 payload 구조(EVENT 포함)

`app/notifications/service_notify.py:49`의 `build_payload`가 고정 구조의 dict를 만든다. 없는 값은 빈 문자열로 채운다(키는 항상 존재).

```python
# app/notifications/service_notify.py:53
return {
    "EVENT": event,                  # 이벤트 식별자
    "subscribe_id": subscribe_id or "",
    "order_id": order_id or "",
    "PRE_STATUS": pre_status or "",
    "STATUS": status or "",
    "service_name": service.name,
    "email": email or "",
    "date": kst_format(utcnow(), "%Y-%m-%d %H:%M:%S"),  # 발생 시각(KST)
    "DESC": desc or "",
}
```

| 키 | 의미 |
| --- | --- |
| `EVENT` | 이벤트 식별자(위 표 값) |
| `subscribe_id` / `order_id` | 구독 ID / 주문 ID(해당될 때만) |
| `PRE_STATUS` / `STATUS` | 변경 전/후 상태 |
| `service_name` | 서비스명 |
| `email` | 대상 외부 사용자(이메일/식별자) |
| `date` | 발생 시각(KST, `YYYY-MM-DD HH:MM:SS`) |
| `DESC` | 사람이 읽는 설명 |

## 15.4 발송 흐름(HttpServiceNotifier.send) — best-effort

`app/notifications/service_notify.py:86`의 `send`가 발송을 구성한다. 전체가 `try/except`로 감싸져 **payload 구성·서명·스케줄 어떤 예외도 본 처리를 막지 않는다**.

1. **URL 확인** — `notification_url`이 없으면 즉시 반환(발송 안 함) (`service_notify.py:91`~`93`).
2. **payload 구성** — `build_payload(...)` 후 `json.dumps(..., ensure_ascii=False)`로 바이트 본문 생성 (`service_notify.py:94`~`97`).
3. **서명** — 서비스의 HMAC 시크릿을 복호화하고, 타임스탬프·nonce를 만들어 `sign_request`로 서명 (`service_notify.py:99`~`105`).

```python
# app/notifications/service_notify.py:99
secret = self._cipher.decrypt(service.hmac_secret_encrypted)
ts = str(int(utcnow().timestamp()))
nonce = secrets.token_hex(16)
path = urlsplit(url).path or "/"
sig = sign_request(secret, "POST", path, ts, nonce, body)
headers = {"Content-Type": "application/json", "X-Event": event,
           "X-Signature": sig, "X-Timestamp": ts, "X-Nonce": nonce}
```

4. **fire-and-forget** — 실제 POST는 `asyncio.create_task(self._post(...))`로 백그라운드에 띄운다. 태스크 참조를 set에 보관해 GC를 막는다 (`service_notify.py:107`~`109`).
5. **예외 흡수** — 구성 단계 실패는 `logger.warning`만 남긴다(`service_notify.py:110`). 백그라운드 `_post`는 HTTP 4xx/5xx·네트워크 오류 모두 로그만 남기고 흡수한다(`service_notify.py:113`~`121`).

### 서명 규칙(sign_request)

`app/core/security.py:62`. canonical string은 개행으로 연결된 `METHOD\nPATH\nTIMESTAMP\nNONCE\nSHA256(body)`이며, HMAC-SHA256(시크릿)으로 서명한다. 각 구성요소에 개행이 있으면 거부한다(필드 간 바이트 이동 공격 차단).

```python
# app/core/security.py:73
body_hash = hashlib.sha256(body).hexdigest()
message = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
```

> 중요: 서명 본문은 `path`(URL의 경로 부분)와 `body`를 포함한다. 수신 측은 자신이 받은 경로·본문으로 같은 식을 계산해 `X-Signature`와 비교해야 한다.

### 이벤트 발생 지점

각 이벤트는 도메인 서비스에서 `notifier.send(...)`로 emit된다. 결제 3종은 `app/services/payments.py`에서, 구독/카드/요금제 이벤트는 각각 `subscriptions.py`·`renewals.py`·`cards.py`·`plans.py`에서 발생한다. 예: 단건결제 성공 시

```python
# app/services/payments.py:194
await notifier.send(service, event=EVENT_PAYMENT_ONE_OFF, order_id=payment.order_id,
                    status=payment.status, email=external_user_id,
                    desc=f"일반결제 {amount:,}원({order_name})")
```

## 15.5 알림 URL 등록 + 어드민 테스트 버튼

### URL 저장

`app/admin/routes/services.py:416`의 `services_notification_url`. CSRF 검증 후 폼 `notification_url`을 받는다.

- 값이 있으면 `http://` / `https://`로 시작해야 한다. 아니면 `?error=`로 안내 후 리다이렉트 (`services.py:428`~`431`).
- 빈 값이면 `service.notification_url = None`(알림 끔)으로 저장 (`services.py:436`).
- 감사 로그 `service.notification_url_updated`(old/new URL 기록) (`services.py:437`).

### 테스트 발송(동기)

`app/admin/routes/services.py:445`의 `services_notification_test`. 일반 `send`와 달리 **동기**로 보내 수신 측 응답을 운영자에게 즉시 토스트로 보여준다.

```python
# app/admin/routes/services.py:459
ok, detail = await notifier.send_test(service)
if ok:
    return saved_redirect(f"/admin/services/{service_id}",
                          f"테스트 알림을 전송했습니다 ({detail})")
return RedirectResponse(
    f"/admin/services/{service_id}?error={quote(f'테스트 알림 전송 실패: {detail}')}",
    status_code=303)
```

`send_test`(`app/notifications/service_notify.py:123`)는 `EVENT=notification.test`, `STATUS=TEST`인 payload를 만들어 동일하게 서명한 뒤 **즉시 POST**하고 `(성공여부, 상세문자열)`을 반환한다.

| 결과 | 반환 |
| --- | --- |
| URL 미등록 | `(False, "알림 URL이 등록되어 있지 않습니다")` |
| 수신 응답 < 400 | `(True, "수신 측 응답 HTTP {코드}")` |
| 수신 응답 ≥ 400 | `(False, "수신 측 응답 오류 HTTP {코드}")` |
| 네트워크 예외 | `(False, "전송 실패: ...")` |

## 15.6 샘플 수신(sample_service)

샘플 서비스(`sample_service/shop/views.py`)가 수신·검증 예시를 보여준다.

1. **서명 검증** — `_verify_notify_signature`(`sample_service/shop/views.py:642`)가 `X-Signature`를 읽고, `service_name`으로 찾은(또는 전체) HMAC 시크릿으로 `HMAC-SHA256(message)`를 계산해 `hmac.compare_digest`로 비교한다.
2. **수신 처리** — 본문 JSON + `X-Signature`/`X-Timestamp`/`X-Nonce`를 검증한 뒤 `EVENT`/`STATUS` 등을 꺼내 저장한다(`views.py:672`~`683`).
3. **표시** — `notifications_view`(`views.py:690`)가 수신 내역을 화면에 보여준다.

> 참고: 검증 message는 발신 측 `sign_request`와 동일하게 `METHOD\nPATH\nTS\nNONCE\nSHA256(body)`를 만들어야 일치한다. 본 시스템은 `path`로 URL 경로 부분(`urlsplit(url).path`)을 쓴다.

## 15.7 수신 측 구현 규약 (중요)

알림을 받는 서비스 서버가 지켜야 할 계약이다. **발송은 단발(at-most-once)이라 전달이 보장되지 않는다**는 점을 전제로 설계해야 한다.

### 전달 보장 — 없음(재시도 없음)

- 알림은 **한 번만** POST하며 **재시도·재전송이 없다**(15.4). 수신 서버가 다운·타임아웃·5xx면 그 이벤트는 **영구 유실**된다.
- 따라서 **중요한 상태는 알림에만 의존하지 말 것.** 구독·결제의 최종 상태는 조회 API(`GET /api/v1/subscriptions/{external_user_id}` · `GET /api/v1/payments/{external_user_id}`, [서비스 API](11-service-api.md))로 **주기적으로 재확인**하거나, 사용자 접근 시점에 `access_allowed`를 다시 조회해 보정한다.

### 응답·타임아웃 계약

- 수신 서버는 **2xx**를 빠르게 반환해야 한다. **응답 본문은 무시**된다.
- 발송 측 타임아웃은 **5초**(`HttpServiceNotifier(timeout_seconds=5.0)`, `app/notifications/service_notify.py:81`). 5초 내 응답하지 못하면 실패로 간주되고 재시도는 없다 → 무거운 처리는 **수신 직후 비동기로** 넘기고 즉시 2xx를 반환하라.
- 4xx/5xx를 반환해도 발송 측은 **로그만 남기고 흡수**한다(별도 통지·재시도 없음). 단, 어드민 '테스트 알림'은 동기 발송이라 이 응답 코드가 운영자 토스트로 보인다(15.5).

### 멱등·중복 처리

- 재전송이 없으므로 정상 경로에선 중복 수신이 발생하지 않는다. 다만 중간 프록시 재시도나 운영자의 수동 테스트로 유사 이벤트가 또 올 수 있으니, 수신 측은 **비즈니스 키로 멱등 처리**하는 편이 안전하다.
- 전용 전달 ID는 없다. 멱등 키로는 결제 이벤트는 `order_id`, 구독 이벤트는 `subscribe_id` + `EVENT` + `STATUS` + `date` 조합을 쓴다. `X-Nonce`는 발송마다 랜덤이라 **중복 판별용으로 쓰지 말 것**(서명 검증용일 뿐).

### payload 필드 함정

- `subscribe_id`는 **구독 ID**다(키 이름이 `subscription_id`가 아님에 주의).
- `email`은 키 이름과 달리 **`external_user_id`(외부 서비스 측 사용자 식별자)**가 담긴다 — 실제 이메일이 아닐 수 있다.
- 요금제 이벤트(`plan.*`)는 사용자 비귀속이라 `subscribe_id`/`order_id`/`email`이 빈 문자열이고 `DESC`에 상세가 담긴다.

### 서명 검증은 운영에서 필수

수신 측은 받은 `X-Signature`를 **반드시 검증하고, 불일치 시 거부(401/403)**해야 한다. 참고로 샘플(`sample_service/shop/views.py`)은 데모 편의상 서명이 불일치해도 `verified=False`로 **기록만 남기고 200**을 돌려준다 — **운영 수신기는 이 동작을 복사하지 말고**, 검증 실패는 거부한다. (`X-Event`는 본문 `EVENT`와 동일한 값을 담은 힌트일 뿐이므로, 이벤트 종류는 검증된 본문의 `EVENT`로 판단한다.)

## 15.8 제약·에러 처리

| 상황 | 동작 |
| --- | --- |
| `notification_url` 미등록 | 발송 안 함(`send`는 조용히 반환, `send_test`는 실패 보고) |
| payload 구성/서명 실패 | `logger.warning`만 남기고 본 처리 계속(`service_notify.py:110`) |
| 백그라운드 POST 실패(4xx/5xx·네트워크) | 로그만 남기고 흡수 — 결제·구독은 영향 없음(`service_notify.py:113`) |
| URL 형식 오류(어드민 저장) | `?error=`로 안내, 저장 안 함(`services.py:428`) |
| 테스트 발송 | 동기 — 수신 응답/네트워크 오류를 운영자에게 즉시 표시 |

> 중요: 알림은 전 구간 best-effort다. "알림이 안 왔다"고 해서 결제/구독이 실패한 것이 아니다. 누락 추적은 수신 측 로그와 본 시스템 `service_notify` 로거(`logger.warning`)를 함께 봐야 한다.

## 15.9 유지보수 팁

1. **새 이벤트 추가** — `service_notify.py`에 `EVENT_*` 상수를 추가하고, 발생 지점(해당 도메인 서비스)에서 `notifier.send(event=..., ...)`를 호출한다. payload 구조는 `build_payload`가 공통이므로 키를 늘릴 때만 그 함수를 수정한다.
2. **서명 호환 유지** — `sign_request`의 canonical string 구조를 바꾸면 모든 수신기가 깨진다. 변경 시 샘플 수신기(`sample_service/shop/views.py`)의 `_verify_notify_signature`도 함께 맞춰야 한다.
3. **테스트는 RecordingServiceNotifier로** — 실제 네트워크 없이 발송 내역을 검사한다(`service_notify.py:153`). URL 미등록 서비스는 기록하지 않으므로 테스트에서 URL을 먼저 세팅한다.
4. **best-effort를 깨지 말 것** — 발송 경로에서 예외를 다시 던지면 결제/구독이 함께 실패한다. `send`의 광범위한 `except`(`# noqa: BLE001`)는 의도된 것이다.
5. **HMAC 시크릿 재사용** — 알림 서명은 별도 시크릿이 아니라 서비스 API와 같은 `hmac_secret_encrypted`를 쓴다. 시크릿 로테이션 시 알림 검증도 함께 영향받는 점을 기억한다.
