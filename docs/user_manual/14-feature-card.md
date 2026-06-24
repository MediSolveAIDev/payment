# 14. 카드 보관함(Card Vault) 기능

> 함께 보기: [구독 기능](15-feature-subscription.md)

이 문서는 카드 보관함 기능을 **호출 진입(라우트)부터 반환까지** 코드 흐름으로 따라갑니다. 카드 등록·교체·삭제·활성/비활성 토글, 빌링키 암호화 보관, 비활성 카드 결제 차단, 카드별 결제내역 표시를 다룹니다.

> 쉽게 말하면 카드 보관함은 "토스가 발급한 빌링키(자동결제 열쇠)를 서버 금고에 암호화해 넣어 두고, 구독·결제가 필요할 때 꺼내 쓰는 곳"입니다. `(서비스, 외부 사용자)`당 카드 1장만 보관합니다.

---

## 14.1. 기능 개요·관련 파일·DB 테이블

### 14.1.1. 한 줄 정의

토스에서 발급한 **빌링키를 AES-GCM으로 암호화**해 `cards` 테이블에 보관하고, 구독·단건 결제가 이 카드를 참조해 자동결제합니다. `(service_id, external_user_id)` 쌍당 **1건**만 허용하며, 재등록 시 같은 행을 교체합니다.

### 14.1.2. 관련 파일

| 파일 | 역할 |
|------|------|
| `app/api/v1/cards.py` | 외부 API 라우터 — `POST/GET/DELETE /api/v1/cards` |
| `app/services/cards.py` | 서비스 레이어 — 등록/교체·조회·삭제·활성 토글 |
| `app/models/card.py` | `Card` 모델(cards 테이블) |
| `app/core/crypto.py` | `AesGcmCipher` — 빌링키 암호화/복호화 |
| `app/core/security.py` | `sha256_hex` — 빌링키 해시 |
| `app/services/payment_utils.py` | `safe_delete_billing_key`, `CUSTOMER_KEY_RE` |
| `app/toss/client.py` | 토스 빌링키 발급(`issue_billing_key`)·삭제(`delete_billing_key`) |
| `app/toss/provider.py` | `TossClientProvider` — 서비스별 토스 클라이언트 해석(T7 컷오버) |
| `app/notifications/service_notify.py` | 카드 이벤트 서비스 알림 상수 |

### 14.1.3. DB 테이블 — `cards` (`app/models/card.py:15`)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID PK | 카드 고유 ID |
| `service_id` | UUID FK → services (RESTRICT) | 소속 서비스 |
| `external_user_id`<span style="color:#e5484d">(이메일)</span> | VARCHAR(255) | 외부 서비스 사용자 ID |
| `customer_key` | VARCHAR(300) | 토스 customerKey |
| `billing_key_encrypted` | VARCHAR(1024) | 빌링키 AES-GCM 암호문(평문 저장 안 함) |
| `billing_key_hash` | VARCHAR(64) | 빌링키 SHA-256 해시(중복탐지·조회용) |
| `card_info` | JSONB | 마스킹 카드번호·발급사 등 토스 응답 일부 |
| `is_active` | BOOLEAN (기본 true) | **false면 이 카드로의 모든 결제 차단** |
| `created_at`/`updated_at` | timestamptz | 등록/교체 시각 |

유니크 제약 `uq_cards_service_user`가 `(service_id, external_user_id)` 쌍당 1건을 DB 수준에서 강제합니다(`app/models/card.py:25`).

> 참고: 빌링키 원문은 **어디에도 저장하지 않습니다.** 암호문(`billing_key_encrypted`)만 보관하고, 자동결제 시점에 복호화해서 토스에 전달합니다.

---

## 14.2. 주요 흐름별 단계 추적

### 14.2.1. 카드 등록/교체 — `POST /api/v1/cards`

요청 → 라우터 → 서비스 함수 → 토스 → DB → 감사로그 → 알림 → 반환의 전체 경로입니다.

**1) 라우터 진입** (`app/api/v1/cards.py:52` `register_card`)

빌링키 발급(토스 호출)을 수반하므로 일반 인증이 아닌 **결제 전용 처리율 제한** `payment_rate_limit`을 통과합니다. 또한 **T7 컷오버**로 전역 토스 클라이언트는 제거되었고, 서비스별 시크릿키로 클라이언트를 해석하는 `TossClientProvider`를 주입받아 `toss_provider.for_service(service)`로 그 서비스 전용 `TossClient`를 얻습니다.

```python
@router.post("/cards", status_code=201, response_model=CardResponse, ...)
async def register_card(
    payload: CardRegisterRequest,
    service: Service = Depends(payment_rate_limit),  # 결제 전용 처리율 제한 + 인증
    db: AsyncSession = Depends(get_db),
    toss_provider: TossClientProvider = Depends(get_toss_provider),  # T7: 서비스별 해석기
    cipher: AesGcmCipher = Depends(get_cipher),
    notifier=Depends(get_notifier),
):
    toss = toss_provider.for_service(service)  # T7: 서비스의 toss_secret_key로 클라이언트 해석
    card = await card_service.register_or_replace_card(
        db, toss, cipher,
        service=service,
        external_user_id=payload.external_user_id,
        customer_key=payload.customer_key,
        auth_key=payload.auth_key,
        notifier=notifier,
    )
    return CardResponse.from_model(card)  # 마스킹 정보만 — billingKey 비포함
```

> 참고: `toss_provider.for_service(service)`는 서비스의 `toss_secret_key_encrypted`를 복호화해 클라이언트를 만들고 시크릿별로 캐시합니다. 키가 미등록이면 `TossKeyNotConfiguredError`를 던집니다(`app/toss/provider.py:35`).

**2) 서비스 함수** (`app/services/cards.py:163` `register_or_replace_card`)

시그니처와 핵심 흐름:

```python
async def register_or_replace_card(
    db, toss, cipher, *,
    service, external_user_id, customer_key, auth_key, notifier=None,
) -> Card:
```

단계 추적:

| # | 단계 | 코드 위치 | 외부호출/DB |
|---|------|-----------|-------------|
| 1 | `customer_key` 형식 검증(`CUSTOMER_KEY_RE`) | `cards.py:195` | — |
| 2 | `external_user_id`<span style="color:#e5484d">(이메일)</span> 빈값/255자 초과 검증 | `cards.py:198` | — |
| 3 | 토스 빌링키 발급 | `cards.py:202` | `toss.issue_billing_key(auth_key, customer_key)` → `BillingKeyResult` |
| 4 | 기존 카드 조회로 교체/신규 분기 | `cards.py:205` | `SELECT cards` |
| 5a | 교체: 기존 행 갱신 | `cards.py:208-215` | UPDATE(메모리) |
| 5b | 신규: `Card` 삽입 + `flush` | `cards.py:218-239` | INSERT, 충돌 시 `IntegrityError` |
| 6 | 감사 로그 + commit | `cards.py:244-254` | `record_audit` + `COMMIT` |
| 7 | 교체 시 옛 빌링키 best-effort 삭제 | `cards.py:257-264` | `safe_delete_billing_key` |
| 8 | 서비스 알림(best-effort) | `cards.py:267` | `notifier.send` |
| 9 | `Card` 반환 | `cards.py:271` | — |

빌링키 발급 후 암호화 저장 부분(신규):

```python
bk = await toss.issue_billing_key(auth_key, customer_key)  # 토스 발급
...
card = Card(
    service_id=service.id,
    external_user_id=external_user_id,
    customer_key=customer_key,
    billing_key_encrypted=cipher.encrypt(bk.billing_key),  # AES-GCM 암호화 저장
    billing_key_hash=sha256_hex(bk.billing_key),           # 중복탐지용 해시
    card_info=bk.card,                                     # 마스킹 번호·발급사
)
db.add(card)
```

> 중요: 신규 등록은 `db.add(card)` 직후 `db.flush()`를 실행합니다(`cards.py:234`). `SELECT` 후 `INSERT` 사이에 동시 요청이 같은 키로 들어오면 `uq_cards_service_user` 유니크 제약이 위반되어 `IntegrityError`가 납니다. 이때 `rollback` → 패자 요청이 발급한 **고아 빌링키를 best-effort 삭제** → `ConflictError`를 던집니다(`cards.py:235-239`).

**3) 반환** — `CardResponse.from_model(card)`는 `card_info`(마스킹 정보)만 담고 `billing_key_encrypted`는 절대 응답에 넣지 않습니다.

### 14.2.2. 카드 조회 — `GET /api/v1/cards/{external_user_id}`

`app/api/v1/cards.py:93` `get_card`. 읽기 전용이므로 일반 HMAC 인증 `authenticate_service`를 사용합니다(토스 호출이 없어 `toss_provider`를 주입받지 않습니다).

```python
card = await card_service.get_card(
    db, service_id=service.id, external_user_id=external_user_id)
if card is None:
    raise NotFoundError("등록된 카드가 없습니다")  # 404
return CardResponse.from_model(card)
```

서비스 함수 `get_card`(`app/services/cards.py:95`)는 `(service_id, external_user_id)`로 단일 카드를 조회하고, 없으면 **예외 없이 `None`**을 반환합니다(라우터에서 404로 변환).

### 14.2.3. 카드 삭제 — `DELETE /api/v1/cards/{external_user_id}`

**1) 라우터** (`app/api/v1/cards.py:125` `delete_card`) — 실제 과금은 없으므로 일반 인증 `authenticate_service`. 단, 빌링키를 토스에서 best-effort 삭제하므로 `toss_provider.for_service(service)`로 클라이언트를 해석합니다(`app/api/v1/cards.py:143`). 응답은 204 No Content.

**2) 서비스 함수** (`app/services/cards.py:274` `delete_card`) 단계 추적:

| # | 단계 | 코드 위치 | DB/외부 |
|---|------|-----------|---------|
| 1 | 카드 조회 — 없으면 `NotFoundError` | `cards.py:308` | `SELECT cards` |
| 2 | billing-active 구독이 카드 참조 시 `ConflictError` | `cards.py:314-321` | `SELECT subscriptions` |
| 3 | CANCELED/EXPIRED 구독의 `card_id` → NULL | `cards.py:328-334` | UPDATE |
| 4 | 빌링키 평문 확보(삭제 전) | `cards.py:337` | `cipher.decrypt` |
| 5 | 카드 삭제 + 감사 로그 + commit | `cards.py:340-350` | `DELETE` + `record_audit` + `COMMIT` |
| 6 | 서비스 알림(best-effort) | `cards.py:353` | `notifier.send` |
| 7 | 커밋 후 토스 빌링키 best-effort 삭제 | `cards.py:356` | `safe_delete_billing_key` |

활성 구독 차단 검사 핵심:

```python
blocking_sub = await db.scalar(
    select(Subscription).where(
        Subscription.card_id == card.id,
        Subscription.status.in_(CARD_DELETE_BLOCKING_STATUSES),
    )
)
if blocking_sub is not None:
    raise ConflictError("활성 구독이 사용 중인 카드는 삭제할 수 없습니다")
```

> 주의: 빌링키 복호화는 **반드시 `db.delete(card)` 전에** 합니다(`cards.py:337`). 삭제 후에는 암호문에 접근할 수 없어 토스 측 빌링키를 지울 수 없게 됩니다.

### 14.2.4. 카드 활성/비활성 토글 — `set_card_active` (어드민)

`app/services/cards.py:113` `set_card_active`. 어드민 라우트 `POST /admin/cards/{card_id}/toggle`(`app/admin/routes/cards.py:31` `cards_toggle`)에서 현재 상태를 반전(`is_active=not card.is_active`)시켜 호출하며, 감사 로그는 `actor_type="USER"`로 남깁니다.

```python
async def set_card_active(db, *, card_id, is_active, actor_user_id=None, notifier=None) -> Card:
    card = await db.get(Card, card_id)
    if card is None:
        raise NotFoundError("카드를 찾을 수 없습니다")
    if card.is_active == is_active:
        return card                     # 멱등 — 같은 상태면 감사로그도 안 남김
    card.is_active = is_active
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="card.activate" if is_active else "card.deactivate",
                       target_type="card", target_id=str(card.id),
                       detail=_card_audit_detail(card, is_active=is_active))
    await db.commit()
    await _notify_card(db, notifier, card,
                       event=(EVENT_CARD_ACTIVATED if is_active else EVENT_CARD_DEACTIVATED),
                       desc=("카드 활성화" if is_active else "카드 비활성화"))
    return card
```

> 참고: 이미 원하는 상태면 아무 것도 하지 않습니다(멱등). 중복 감사로그를 막기 위함입니다(`cards.py:143`).

### 14.2.5. 카드별 결제내역 (관리자 화면)

`Payment` 테이블에는 `card_id` 컬럼이 없습니다. 대신 카드의 고유키 `(service_id, external_user_id)`가 Payment에도 동일하게 존재하므로, **같은 `(service_id, external_user_id)`의 Payment를 그 카드의 결제내역**으로 조회합니다(구독·일반결제 모두 포함). 카드 상세 화면(`/admin/cards/{card_id}`)이 이 방식을 씁니다(스키마 변경 없음).

---

## 14.3. 상태·제약·에러 처리

### 14.3.1. 비활성 카드 결제 차단

`is_active=False`이면 모든 결제 경로에서 차단됩니다. 각 경로는 `get_card` 직후 `is_active`를 검사합니다.

| 결제 경로 | 위치 | 비활성 시 동작 |
|-----------|------|----------------|
| 구독 자동연장·재시도 | `app/services/renewals.py:443` `_renew_one`(정의 `renewals.py:340`) | 토스 호출 없이 합성 `TossError("CARD_INACTIVE")` → 기존 실패 처리(PAST_DUE/정지) |
| 구독 생성 | `app/services/subscriptions.py:210` | `ConflictError`(생성 차단) |
| 수동 재결제 | `app/services/subscriptions.py:389` `_perform_manual_charge`(정의 `subscriptions.py:367`) | `PaymentFailedError(code="CARD_INACTIVE")` |
| 일반결제(one-off) | `app/services/payments.py:94` `create_one_off_payment`(정의 `payments.py:44`) | `ConflictError` |

자동연장 경로의 합성 에러(`renewals.py:443-453`):

```python
if card is None or sub.card_id is None or not card.is_active:
    # 비활성 카드와 미등록 카드를 합성 TossError 코드로 구분(감사·메시지용)
    exc = (TossError("CARD_INACTIVE", "비활성화된 카드입니다")
           if card is not None and not card.is_active
           else TossError("NO_BILLING_KEY", "등록된 카드가 없습니다"))
    await db.commit()
    await db.refresh(payment, with_for_update=True)
    await db.refresh(sub, with_for_update=True)
    await _handle_charge_failure(
        db, None, email_sender, sub, service, payment, billing_key="",
        exc=exc, now=now, cfg=cfg, stats=stats, notifier=notifier)
    return
```

> 중요: 활성 구독이 있는 카드를 비활성화해도 구독 상태는 **즉시 바뀌지 않습니다.** 다음 자동결제 시도에서 실패 처리되어 PAST_DUE → (재시도 소진 시) SUSPENDED로 이행합니다.

### 14.3.2. 활성 구독이 있는 카드 삭제 차단

삭제 차단 상태 집합(`app/services/cards.py:47`):

```python
CARD_DELETE_BLOCKING_STATUSES = frozenset({
    SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE, SubscriptionStatus.SUSPENDED,
    SubscriptionStatus.EXTENDED,
})
```

| 구독 상태 | 카드 삭제 |
|-----------|-----------|
| TRIAL / ACTIVE / PAST_DUE / SUSPENDED / EXTENDED | **차단** — `ConflictError` |
| CANCELED / EXPIRED | **허용** — 삭제 전 해당 구독의 `card_id`를 NULL로 |
| 구독 없음 | **허용** |

> 참고: `subscriptions.card_id` FK는 `RESTRICT`입니다. 그래서 CANCELED/EXPIRED 구독은 카드 삭제 전에 `card_id`를 NULL로 풀어 FK 위반을 피합니다(`cards.py:328-334`).

### 14.3.3. 에러 요약

| 조건 | 예외 | HTTP |
|------|------|------|
| `customer_key` 형식 오류 | `InputValidationError` | 422 |
| `external_user_id`<span style="color:#e5484d">(이메일)</span> 빈값/255자 초과 | `InputValidationError` | 422 |
| 동시 첫 등록 경쟁(유니크 위반) | `ConflictError` | 409 |
| 토스 빌링키 발급 실패 | `TossError`(전파) | 4xx/5xx |
| 카드 미등록(조회/삭제) | `NotFoundError` | 404 |
| billing-active 구독이 카드 참조 중(삭제) | `ConflictError` | 409 |

### 14.3.4. best-effort 빌링키 삭제

교체·삭제 시 옛 빌링키를 `safe_delete_billing_key(toss, billing_key)`로 지웁니다(`app/services/payment_utils.py:24`).

```python
async def safe_delete_billing_key(toss: TossClient, billing_key: str) -> bool:
    try:
        await toss.delete_billing_key(billing_key)
        return True
    except TossError as exc:
        if exc.http_status == 404:
            return True   # 이미 토스에서 삭제됨 → 성공 간주
        logger.warning("빌링키 삭제 실패(토스에 키 잔존 가능): hash=%s code=%s",
                       sha256_hex(billing_key)[:12], exc.code)  # 평문 아닌 해시 일부만 기록
        return False
```

실패해도 **카드 교체·삭제 커밋은 이미 완료**되어 유효합니다. 실패 시 WARNING 로그만 남고 토스에 고아 키가 남을 수 있습니다.

---

## 14.4. 감사 로그 & 알림

| 이벤트 | `action` | actor | 알림 상수 |
|--------|----------|-------|-----------|
| 신규 등록 | `card.register` | SERVICE | `EVENT_CARD_REGISTERED` |
| 교체 | `card.replace` | SERVICE | `EVENT_CARD_REPLACED` |
| 삭제 | `card.delete` | SERVICE | `EVENT_CARD_DELETED` |
| 활성화 | `card.activate` | USER | `EVENT_CARD_ACTIVATED` |
| 비활성화 | `card.deactivate` | USER | `EVENT_CARD_DEACTIVATED` |

모든 카드 이벤트는 `_card_audit_detail`(`cards.py:77`)로 동일한 상세(`external_user_id`<span style="color:#e5484d">(이메일)</span>, `service_id`, 마스킹 `card_number`, `issuer`)를 남깁니다. **빌링키 암호문·해시는 감사로그에 넣지 않습니다.**

알림은 best-effort이며(`_notify_card`, `cards.py:58`), notifier가 없거나 서비스 알림 URL 미등록이면 조용히 건너뜁니다.

---

## 14.5. 유지보수 팁

- **billingKey 노출을 막으려면**: `CardResponse.from_model()`만 사용하세요. `card_info`(마스킹)만 반환하고 암호문은 절대 포함하지 않습니다.
- **삭제 차단 상태를 바꾸려면**: `app/services/cards.py:47` `CARD_DELETE_BLOCKING_STATUSES`를 수정하세요. 여기에 든 상태의 구독이 카드를 참조하면 삭제가 차단됩니다.
- **비활성 카드 차단 동작을 바꾸려면**: 각 결제 경로의 `is_active` 검사(14.3.1 표)를 함께 보세요. 자동연장은 `renewals.py:443`, 생성은 `subscriptions.py:210`, 수동결제는 `subscriptions.py:389`, 일반결제는 `payments.py:94`입니다.
- **빌링키 복호화 실패(`InvalidTag`)**: `ENCRYPTION_KEY` 환경변수가 바뀌지 않았는지 확인하세요. 키가 달라지면 기존 암호문을 복호화할 수 없습니다.
- **카드 없이 구독 시도**: `POST /api/v1/subscriptions` 전에 반드시 `POST /api/v1/cards`로 카드를 먼저 등록해야 합니다. 미등록이면 구독 생성이 404 `NOT_FOUND`로 거부됩니다(`subscriptions.py:208`).
- **서비스 삭제 제약**: `cards.service_id` FK가 `RESTRICT`라 카드가 있는 서비스는 DB 레벨에서 삭제가 차단됩니다.

> 함께 보기: 카드가 실제로 어떻게 자동결제에 사용되는지는 [구독 기능](15-feature-subscription.md)의 자동연장(스케줄러) 절을 보세요.
