# 14. 일반결제·취소·정산 기능

이 문서는 **구독 없이 발생하는 1회성(단건) 결제**와 그 **취소(환불)**, 그리고 매출·환불·순매출을 합산하는 **정산/대시보드 집계**가 코드에서 어떻게 흐르는지 추적한다. 호출 진입점 → 서비스 함수(`file:line`) → 토스 호출 → DB 갱신 → 감사 로그 → 서비스 알림 → 반환의 순서로 본다.

> 쉽게 말하면 단건결제는 "사전 등록한 카드(카드 보관함)에서 한 번 긁는" 결제이고, 취소는 그 청구를 토스로 되돌리는 것이며, 정산은 "얼마 벌고 얼마 돌려줬는지"를 합산하는 것이다.

> 함께 보기: [서비스 API](11-service-api.md), [카드 보관함](12-feature-card.md), [서비스 알림](15-feature-notifications.md)

## 14.1 기능 개요·관련 파일

### 무엇을 하는가
1. **단건 결제 생성** — 외부 서비스가 `POST /v1/payments`로 요청하면, 사전에 등록된 카드(카드 보관함)의 빌링키로 토스에 **즉시 청구**한다. `auth_key`/`customer_key`를 받지 않는다(카드 보관함 기반).
2. **외부 사용자 취소** — `POST /v1/payments/{order_id}/cancel`. 서비스의 취소 수수료율을 적용해 부분환불(또는 전액환불)한다.
3. **어드민 취소** — 관리자 화면에서 `POST /admin/payments/{payment_id}/cancel`. **수수료 없이** 전액/부분 취소가 가능하며, 부분취소는 **누적**된다.
4. **정산·대시보드 집계** — 매출(DONE+CANCELED 원금), 환불(`canceled_amount`), 순매출(매출−환불)을 서비스별/기간별로 합산한다.

### 관련 파일

| 역할 | 파일 |
| --- | --- |
| 단건 결제 생성·취소·어드민 취소 도메인 로직 | `app/services/payments.py` |
| 외부 API 진입점(생성/취소) | `app/api/v1/payments.py` |
| 어드민 취소 진입점 | `app/admin/routes/payments.py` |
| 취소 수수료 계산(공유 공식) | `app/services/billing_math.py` |
| 정산 집계 | `app/services/settlement.py` |
| 대시보드 매출·환불 집계 | `app/services/dashboard.py` |
| 응답 스키마 | `app/schemas/api.py`(`PaymentResponse`) |
| 이벤트 상수·알림 발송 | `app/notifications/service_notify.py` |

## 14.2 흐름 1 — 단건 결제 생성

### 진입점

외부 서비스의 `POST /v1/payments` 요청은 `app/api/v1/payments.py:73`의 `create_payment`로 들어온다. 카드 정보가 아니라 `external_user_id`/`order_id`/`order_name`/`amount`만 받고, 서버가 카드 보관함에서 빌링키를 자동 조회한다.

```python
# app/api/v1/payments.py:90
payment = await payment_service.create_one_off_payment(
    db, toss, cipher,
    service=service,
    external_user_id=payload.external_user_id,
    order_id=payload.order_id,
    order_name=payload.order_name,
    amount=payload.amount,
    notifier=notifier,
)
return PaymentResponse.from_model(payment, service)
```

### 도메인 처리 단계

`app/services/payments.py:44`의 `create_one_off_payment`가 **결제 3원칙**(PENDING 선커밋 / 타임아웃은 PENDING 유지 / 멱등 `order_id`)에 따라 처리한다.

1. **입력 검증** — `order_id` 형식, `external_user_id` 길이, `amount > 0`. 상한은 `GlobalSettings.one_off_max_amount`(런타임 조정 가능)로 검사하고 초과 시 `InputValidationError` (`app/services/payments.py:75`~`86`).
2. **카드 보관함 조회** — `get_card(...)`로 등록 카드를 찾는다. 없으면 `NotFoundError`, **비활성 카드면 `ConflictError`로 차단** (`app/services/payments.py:90`~`95`).
3. **멱등성 검사** — 같은 `(service_id, order_id)`가 이미 있으면 재결제 없이 기존 Payment 반환 (`app/services/payments.py:99`~`102`).
4. **PENDING 선커밋** — 토스 전달용 전역 고유 `toss_order_id`(`t` + uuid4 hex)를 만들어 Payment를 `PENDING`으로 저장하고 감사 로그(`payment.one_off`) 후 **commit** (`app/services/payments.py:104`~`142`).

```python
# app/services/payments.py:109
toss_order_id = f"t{uuid.uuid4().hex}"
payment = Payment(
    ...
    order_id=order_id,
    toss_order_id=toss_order_id,
    amount=amount,
    payment_type=PaymentType.ONE_OFF,
    kind=PaymentKind.ONE_OFF,
    status=PaymentStatus.PENDING,
    idempotency_key=toss_order_id,
    requested_at=now,
)
```

5. **빌링키 복호화 + 토스 청구** — 카드의 `billing_key_encrypted`를 `cipher.decrypt`로 풀어 `resolve_charge(...)`로 청구한다. 토스에는 클라이언트 `order_id`가 아닌 전역 고유 `toss_order_id`를 전달한다(서비스 간 멱등키 충돌 방지) (`app/services/payments.py:146`~`160`).
6. **결과 분기**
   - `TossTimeoutError`(결과 불명): **절대 FAILED로 만들지 않고** PENDING 유지, 감사(`payment.one_off_unresolved`), HTTP 503으로 `PaymentFailedError` (`app/services/payments.py:161`~`172`).
   - `TossError`(실패 확정): `status=FAILED` + `failure_code`/`failure_message`, 감사(`payment.one_off_failed`) (`app/services/payments.py:173`~`184`).
   - 성공: `status=DONE` + `toss_payment_key`·`approved_at`·`raw_response` 기록 후 commit (`app/services/payments.py:186`~`191`).
7. **서비스 알림(best-effort)** — 성공 시 `EVENT_PAYMENT_ONE_OFF` 발송. 알림 실패는 본 처리에 영향 없음 (`app/services/payments.py:192`~`196`).

```python
# app/services/payments.py:187
payment.status = PaymentStatus.DONE
payment.toss_payment_key = result.payment_key
payment.approved_at = utcnow()
payment.raw_response = result.raw
await db.commit()
if notifier is not None:
    await notifier.send(service, event=EVENT_PAYMENT_ONE_OFF, order_id=payment.order_id,
                        status=payment.status, email=external_user_id,
                        desc=f"일반결제 {amount:,}원({order_name})")
```

> 중요: 빌링키 삭제 로직이 없다. 카드는 영속(persistent)이므로 단건결제 성공·실패·타임아웃 어느 경우에도 카드를 지우지 않는다.

## 14.3 흐름 2 — 외부 사용자 취소(수수료율 적용)

### 진입점

`POST /v1/payments/{order_id}/cancel` → `app/api/v1/payments.py:109`의 `cancel_payment` → `cancel_one_off_payment` 호출(`app/api/v1/payments.py:123`). 외부 호출이므로 `actor_user_id`를 넘기지 않는다.

### 도메인 처리 단계 — `app/services/payments.py:201`

1. **결제 조회** — `(service_id, order_id)` 스코프로 조회. 없으면 `NotFoundError` (`app/services/payments.py:219`~`222`).
2. **상태 가드** — `kind==ONE_OFF` & `status==DONE`만 취소 가능. **이미 부분취소(`canceled_amount>0`)된 결제는 이중환불 위험으로 차단** (`app/services/payments.py:225`~`231`).
3. **정책 가드** — `service.cancellation_enabled`가 꺼져 있으면 `PaymentFailedError("CANCEL_DISABLED")` (`app/services/payments.py:234`).
4. **수수료 계산** — `compute_cancel_fee(...)`로 `(fee, refund)`를 구한다. 조회 응답·화면 표시와 **동일한 공식**을 공유한다 (`app/services/payments.py:238`).

```python
# app/services/billing_math.py:107
fee = amount * fee_percent // 100   # 정수 내림
return fee, amount - fee            # (수수료, 환불액)
```

5. **토스 취소** — `refund == amount`(수수료 0)면 `cancel_amount=None`(전액취소), 아니면 `cancel_amount=refund`(부분취소) (`app/services/payments.py:241`~`246`). 실패 시 상태 `DONE` 유지 + 감사(`payment.cancel_failed`) 후 재발생(멱등 재시도 가능).
6. **확정** — `status=CANCELED`, `canceled_amount=refund`, `cancel_fee=fee`, `canceled_at` 기록, 감사(`payment.canceled`, `actor_type="SERVICE"`) (`app/services/payments.py:268`~`288`).
7. **알림** — `EVENT_PAYMENT_ONE_OFF_CANCELED` (best-effort) (`app/services/payments.py:291`~`295`).

> 주의: 외부 사용자 취소는 항상 **전액 1회 취소**다(`refund`는 수수료를 뺀 환불액). 부분 금액 지정은 어드민 취소에서만 가능하다.

## 14.4 흐름 3 — 어드민 전액/부분 취소(수수료 없음·누적)

### 진입점

관리자 화면 `POST /admin/payments/{payment_id}/cancel` → `app/admin/routes/payments.py:115`의 `payment_cancel`. CSRF·스코프 검증 후, 폼 `cancel_amount`(빈값=전액, 숫자=부분)를 파싱해 호출한다.

```python
# app/admin/routes/payments.py:144
await payment_service.admin_cancel_one_off_payment(
    db, toss, payment=payment, cancel_amount=cancel_amount,
    reason="관리자 취소", actor_user_id=ctx.user.id, notifier=notifier)
```

### 외부 사용자 취소와의 차이

| 항목 | 외부 사용자 취소 | 어드민 취소 |
| --- | --- | --- |
| 취소 수수료 | 적용(`cancellation_fee_percent`) | **없음**(지정 금액 그대로 환불) |
| 취소 허용 게이트 | `cancellation_enabled` 검사 | **무시**(항상 가능) |
| 부분 금액 지정 | 불가 | 가능(`cancel_amount`) |
| 부분취소 누적 | 불가(1회) | **누적**(여러 번 가능) |
| 행위자 감사 | `SERVICE` | `USER`(관리자 UUID) |

### 도메인 처리 단계 — `app/services/payments.py:299`

1. **상태 가드** — `kind==ONE_OFF` & `status==DONE`. 부분취소 후에도 `DONE`을 유지하므로 `DONE`이면 잔여가 있다고 본다 (`app/services/payments.py:328`).
2. **잔여 계산** — `remaining = amount − 기존 누적 환불액(canceled_amount)`. 잔여 0이면 `ConflictError("이미 전액 취소된 결제입니다")` (`app/services/payments.py:331`~`334`).
3. **금액 검증** — `cancel_amount=None`이면 잔여 전액, 지정 시 `1 ~ 잔여` 범위. 벗어나면 `InputValidationError` (`app/services/payments.py:336`~`340`).
4. **토스 취소** — 최초 전액취소(`already==0` & `refund==amount`)만 `cancel_amount` 생략, 그 외는 환불액 명시 (`app/services/payments.py:344`~`347`). 실패 시 상태·누적액 보존 + 감사(`payment.cancel_failed`).
5. **누적 확정** — `canceled_amount += refund`. **잔여 0이 되면 `CANCELED`로 전환, 남으면 `DONE` 유지(추가 취소 가능)**. `cancel_fee`는 무수수료라 건드리지 않는다 (`app/services/payments.py:359`~`365`).

```python
# app/services/payments.py:360
new_total = already + refund
payment.canceled_amount = new_total
payment.canceled_at = utcnow()
if new_total >= payment.amount:
    payment.status = PaymentStatus.CANCELED       # 전액 도달 → 취소 종료
```

6. **알림** — `EVENT_PAYMENT_ONE_OFF_ADMIN_CANCELED`. `desc`에 "전액취소/부분취소"와 이번 환불액·누적액을 담는다 (`app/services/payments.py:375`~`382`).

## 14.5 응답 스키마(PaymentResponse)

`app/schemas/api.py:183`의 `PaymentResponse`는 결제 결과와 함께 **취소 안내 필드**를 반환한다(서비스가 "지금 취소하면 얼마 빠지고 얼마 환불"을 미리 보여줄 수 있게).

| 필드 | 의미 |
| --- | --- |
| `status` | `PENDING / DONE / FAILED / CANCELED` |
| `kind` / `payment_type` | `SUBSCRIPTION/ONE_OFF` / `FIRST/RENEWAL/RETRY/ONE_OFF` |
| `cancelable` | 단건·DONE·서비스 취소허용일 때만 `true` |
| `cancel_fee_percent` / `cancel_fee` / `cancel_refund_amount` | 취소 가능 결제는 예상액, 이미 취소된 결제는 실제값 |
| `canceled_amount` | 실제 누적 환불액(어드민 부분취소 시 `DONE`이어도 `>0`) |
| `net_amount` | 순매출(`amount − canceled_amount`) |

> 참고: `toss_payment_key`·`raw_response` 같은 내부 필드는 노출하지 않는다.

## 14.6 흐름 4 — 매출·환불·순매출 집계

매출/환불의 핵심은 **취소 수수료는 매출로 보유**하고 **환불액만 빼는** 것이다. 정산(`settlement.py`)과 대시보드(`dashboard.py`)가 동일 결과가 되도록 맞춰져 있다.

### 정산(settlement.py)

`app/services/settlement.py:41`의 `settlement_summary`는 기간 내 `DONE`+`CANCELED`(승인일 `approved_at` 기준)를 서비스별로 합산한다.

- **총매출** = `sum(amount)` (DONE+CANCELED 원금)
- **환불** = `sum(coalesce(canceled_amount, 0))`
- **순매출** = `net_amount` 프로퍼티 = `amount − refund_amount` (`app/services/settlement.py:36`~`38`)

### 대시보드(dashboard.py)

대시보드는 결제 1건의 **순매출 기여액**을 `_revenue_expr()`로 계산한다 — 어드민 부분취소(`DONE` 유지)에서도 환불액을 빼야 정확하다.

```python
# app/services/dashboard.py:133
return case(
    (Payment.status == PaymentStatus.DONE,
     Payment.amount - func.coalesce(Payment.canceled_amount, 0)),
    (Payment.status == PaymentStatus.CANCELED,
     Payment.amount - func.coalesce(Payment.canceled_amount, Payment.amount)),
    else_=0)
```

환불 합계는 `_refund_between()`(`app/services/dashboard.py:155`)이 `DONE`(부분환불)과 `CANCELED`(환불액) **둘 다**에서 집계한다. 매출 인식 시점은 원결제 승인일(`approved_at`), 환불 인식 시점은 `requested_at`이다.

이번 달 요약 카드 4종(총매출·구독매출·일반매출·환불금액)은 `_revenue_cards()`(`app/services/dashboard.py:184`)가 만든다. 환불이 0원이면 긍정 색(`up=True`)으로 표시한다.

> 참고: 구독 결제는 취소가 발생하지 않으므로 환불은 단건(ONE_OFF) 쪽에서만 잡힌다(`app/services/settlement.py:7`).

## 14.7 제약·에러 처리

| 상황 | 동작 | 위치 |
| --- | --- | --- |
| 등록 카드 없음 | `NotFoundError` | `payments.py:91` |
| 비활성 카드로 결제 | `ConflictError`(활성화 후 재시도) | `payments.py:94` |
| 같은 `(service, order_id)` 재시도 | 재결제 없이 기존 Payment 반환(멱등) | `payments.py:101`, `132` |
| 토스 타임아웃(결과 불명) | PENDING 유지, 503 — 절대 FAILED 금지 | `payments.py:161` |
| 토스 실패 확정 | FAILED + 실패코드 | `payments.py:173` |
| 외부 취소인데 정책 꺼짐 | `PaymentFailedError(CANCEL_DISABLED)` | `payments.py:234` |
| 외부 취소인데 이미 부분취소됨 | `ConflictError`(이중환불 차단) | `payments.py:230` |
| 어드민 취소 잔여 0 | `ConflictError`(전액 취소 완료) | `payments.py:333` |
| 어드민 취소 금액 범위 초과 | `InputValidationError`(1~잔여) | `payments.py:338` |
| 취소 토스 실패 | 상태·누적액 보존, 감사 후 재발생(멱등 재시도) | `payments.py:247`, `348` |

> 중요: 어드민 부분취소가 끝나도 잔여가 남아 있으면 `status`는 `DONE`이다. "CANCELED가 아니니 취소 안 됨"으로 오해하지 말고 `canceled_amount`/잔여로 판단해야 한다.

## 14.8 유지보수 팁

1. **수수료 공식은 한 곳뿐** — 화면 표시, API 응답, 실제 취소가 모두 `compute_cancel_fee`(`app/services/billing_math.py:100`)를 쓴다. 공식을 바꾸면 세 곳이 동시에 바뀐다. 직접 `amount × percent`를 다시 쓰지 말 것.
2. **매출/환불 식은 정산·대시보드가 일치해야 함** — `settlement.py`의 `net_amount`와 `dashboard.py`의 `_revenue_expr()`/`_refund_between()`은 같은 의미여야 한다. 한쪽만 고치면 어드민 화면 수치가 어긋난다.
3. **상한 조정** — 단건 상한은 런타임 `GlobalSettings.one_off_max_amount`로 즉시 조일 수 있다(`payments.py:83`). 기본값보다 **높이려면** `schemas/api.py`의 `le=` 제약도 함께 올려야 한다(Pydantic 경계 검증이 먼저 걸린다).
4. **타임아웃을 FAILED로 바꾸지 말 것** — 이중 결제 위험. 결과 불명은 항상 PENDING 유지가 원칙이다.
5. **알림은 best-effort** — `notifier.send(...)` 실패가 결제/취소를 깨면 안 된다. 알림 누락이 의심되면 [15. 서비스 알림](15-feature-notifications.md)을 본다.
