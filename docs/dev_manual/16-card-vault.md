# 16. 카드 보관함(Card Vault) — 카드 등록·교체·조회·삭제

> **상호참조**: 데이터베이스 → [02. 데이터베이스](02-database.md) |
> 인증 공통 → [03. 인증과 보안 공통](03-auth-and-security.md) |
> 단건결제 → [07. 단건 결제](07-one-off-payment.md) |
> 구독 생성 → [04. 구독 생성](04-subscription-create.md)

---

## 1. 한 줄 요약

사용자의 **결제수단(빌링키)을 서버에 안전하게 보관**하고, 구독·단건 결제 시 재사용할 수 있도록 합니다.
토스에서 발급한 빌링키를 **AES-256-GCM으로 암호화**해 `cards` 테이블에 저장합니다.
`(service_id, external_user_id)` 쌍당 **1건**만 허용하며, 재등록 시 기존 행을 교체하고 옛 빌링키를 best-effort 삭제합니다.
삭제는 billing-active 구독(TRIAL/ACTIVE/PAST_DUE/SUSPENDED/EXTENDED)이 카드를 참조하는 경우 차단됩니다.

---

## 2. 외부 API 엔드포인트 (`app/api/v1/cards.py`)

Task 6에서 `/api/v1/cards` 라우터가 완성됐습니다. 인증 방식과 응답 형식은 구독/결제 라우터와 동일합니다.

### 2-1. 카드 등록/교체 — `POST /api/v1/cards`

| 항목 | 내용 |
|------|------|
| **인증** | `payment_rate_limit` (빌링키 발급 → 결제 전용 처리율 제한) |
| **요청 스키마** | `CardRegisterRequest` (`app/schemas/api.py`) |
| **응답 상태** | 201 Created |
| **응답 스키마** | `CardResponse` — `external_user_id` + `card`(마스킹 정보만, billingKey 미포함) |

```json
// 요청 본문
{
  "external_user_id": "user-123",
  "customer_key": "cust-123",
  "auth_key": "toss_auth_key_xxx"
}

// 응답 201
{
  "external_user_id": "user-123",
  "card": {"issuerCode": "61", "number": "123456******1234"}
}
```

### 2-2. 카드 조회 — `GET /api/v1/cards/{external_user_id}`

| 항목 | 내용 |
|------|------|
| **인증** | `authenticate_service` (일반 HMAC — 결제 API 호출 없음) |
| **응답 상태** | 200 OK / 404 NOT_FOUND |
| **응답 스키마** | `CardResponse` |

### 2-3. 카드 삭제 — `DELETE /api/v1/cards/{external_user_id}`

| 항목 | 내용 |
|------|------|
| **인증** | `authenticate_service` (일반 HMAC) |
| **응답 상태** | 204 No Content / 404 NOT_FOUND / 409 CONFLICT |
| **409 조건** | billing-active 구독(TRIAL/ACTIVE/PAST_DUE/SUSPENDED/EXTENDED)이 카드를 참조 중일 때 |

---

## 3. 언제 실행되나

| 트리거 | 설명 |
|--------|------|
| **외부 서비스 API** `POST /api/v1/cards` | 사내 서비스가 사용자 카드를 등록/교체할 때 |
| **외부 서비스 API** `GET /api/v1/cards/{id}` | 사내 서비스가 등록 카드 확인 시 |
| **외부 서비스 API** `DELETE /api/v1/cards/{id}` | 사내 서비스가 카드를 삭제할 때 |
| 서비스 레이어 직접 호출 | 구독·단건 결제 시 `cards` 테이블에서 빌링키를 조회(T7~T9, 예정) |

---

## 4. 서비스 레이어 (`app/services/cards.py`)

### 3-1. `register_or_replace_card`

```python
async def register_or_replace_card(
    db, toss, cipher, *,
    service, external_user_id, customer_key, auth_key
) -> Card
```

**처리 흐름:**

1. `customer_key` 형식 검증 — `CUSTOMER_KEY_RE` (payment_utils와 공유)
2. `external_user_id` 빈값/255자 초과 검증
3. `toss.issue_billing_key(auth_key, customer_key)` → `BillingKeyResult` 발급
4. `get_card(db, service_id, external_user_id)` 조회
   - **기존 카드 있음** → 교체: `billing_key_encrypted`, `billing_key_hash`, `card_info`, `customer_key` 갱신 / action = `"card.replace"`
   - **기존 카드 없음** → 신규 삽입 / action = `"card.register"`
     - `db.add(card)` 후 즉시 `db.flush()` 실행
     - **`IntegrityError` 발생 시** (동시 요청 경쟁): `db.rollback()` → 고아 빌링키 best-effort 삭제 → `ConflictError("이미 등록된 카드가 있습니다")` raise
5. `db.flush()` → `record_audit(action=...)` → `db.commit()`
6. 교체 시 기존 빌링키를 `safe_delete_billing_key(toss, old_billing_key)` best-effort 삭제 (실패해도 교체 결과에 영향 없음)
7. Card 반환

**입력 검증 및 오류:**

| 조건 | 예외 |
|------|------|
| `customer_key`가 `CUSTOMER_KEY_RE` 불일치 | `InputValidationError` |
| `external_user_id`가 빈값 또는 255자 초과 | `InputValidationError` |
| 동시 첫 등록 경쟁 — `uq_cards_service_user` 위반 | `ConflictError` |
| 토스 빌링키 발급 실패 | `TossError` (그대로 전파) |

> **동시성 경쟁 가드** (`registry.py`와 동일한 패턴):  
> `SELECT` 후 `INSERT` 사이에 동시 요청이 같은 `(service_id, external_user_id)`로 들어오면  
> DB `uq_cards_service_user` 유니크 제약이 위반된다. `flush()` 시 `IntegrityError`를 잡아  
> 패자 요청의 고아 빌링키를 best-effort 삭제한 뒤 `ConflictError`를 반환한다.

### 3-2. `get_card`

```python
async def get_card(db: AsyncSession, *, service_id: uuid.UUID, external_user_id: str) -> Card | None
```

`cards` 테이블에서 `(service_id, external_user_id)` 조건으로 단일 카드를 조회합니다. 없으면 `None` 반환(예외 없음).

### 3-3. `delete_card` (Task 5, spec §6.1)

```python
async def delete_card(
    db, toss, cipher, *,
    service_id: uuid.UUID,
    external_user_id: str,
) -> None
```

**처리 흐름:**

1. `get_card(db, service_id, external_user_id)` 조회 — 없으면 `NotFoundError("등록된 카드가 없습니다")` raise
2. `CARD_DELETE_BLOCKING_STATUSES`(아래 참조) 중 하나인 구독이 이 카드를 참조하면 `ConflictError("활성 구독이 사용 중인 카드는 삭제할 수 없습니다")` raise
3. 남아 있는 CANCELED/EXPIRED 구독의 `card_id`를 `NULL`로 초기화 (FK RESTRICT 해소)
4. 빌링키 복호화 (`cipher.decrypt`) — 삭제 전에 평문 확보
5. `db.delete(card)` → `record_audit(action="card.delete")` → `db.commit()`
6. 커밋 후 `safe_delete_billing_key(toss, billing_key)` best-effort 삭제

**차단 상태 상수** (`app/services/cards.py` 모듈 레벨):

```python
CARD_DELETE_BLOCKING_STATUSES = frozenset({
    SubscriptionStatus.TRIAL,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
    SubscriptionStatus.SUSPENDED,
    SubscriptionStatus.EXTENDED,
})
```

| 구독 상태 | 카드 삭제 |
|-----------|---------|
| TRIAL / ACTIVE / PAST_DUE / SUSPENDED / EXTENDED | **차단** — `ConflictError` |
| CANCELED / EXPIRED | **허용** — 삭제 전 해당 구독의 `card_id` → NULL |
| 구독 없음 | **허용** |

**오류 목록:**

| 조건 | 예외 |
|------|------|
| 카드 미등록 | `NotFoundError` |
| billing-active 구독이 카드 참조 중 | `ConflictError` |

---

## 5. 데이터 모델

### 4-1. `cards` 테이블 (`app/models/card.py`)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID PK | 카드 고유 ID (자동 생성) |
| `service_id` | UUID FK → services | 카드가 속한 서비스 (RESTRICT 삭제 불가) |
| `external_user_id` | VARCHAR(255) | 외부 서비스의 사용자 ID |
| `customer_key` | VARCHAR(300) | 토스 customerKey |
| `billing_key_encrypted` | VARCHAR(1024) | 빌링키 AES-256-GCM 암호문 (평문 저장 안 함) |
| `billing_key_hash` | VARCHAR(64) | 빌링키 SHA-256 해시 (중복탐지·조회용) |
| `card_info` | JSONB | 마스킹된 카드번호·발급사 등 토스 응답 부분 보관 |
| `is_active` | BOOLEAN NOT NULL (기본 true) | 활성/비활성 — **false면 이 카드로의 모든 결제 차단** (migration `c2d3e4f5a6b7`) |
| `created_at` | timestamptz | 최초 등록 시각 (TimestampMixin) |
| `updated_at` | timestamptz | 최근 교체 시각 (TimestampMixin) |

**유니크 제약:** `uq_cards_service_user` — `(service_id, external_user_id)` 쌍당 1건

### 4-2. `subscriptions.card_id` 변경 이력 (migration `b1c2d3e4f5a6`)

Task 5에서 `subscriptions.card_id` 컬럼이 **NOT NULL → nullable**로 변경됐습니다.

- **이유**: 카드 삭제 시 FK `RESTRICT` 제약을 우회하기 위해, CANCELED/EXPIRED 구독의 `card_id`를 NULL로 초기화한 뒤 카드를 삭제합니다.
- **billing-active 구독**(TRIAL/ACTIVE/PAST_DUE/SUSPENDED/EXTENDED)이 있으면 삭제 자체가 앱 레이어에서 차단되므로 실제로 NULL이 되는 경우는 종료 상태 구독뿐입니다.
- Subscription 모델: `card_id: Mapped[uuid.UUID | None]` (nullable=True)

---

## 6. 암호화·해시 전략

| 값 | 저장 방식 | 이유 |
|----|----------|------|
| 빌링키 원문 | 저장 안 함 | 유출 방지 |
| `billing_key_encrypted` | AES-256-GCM (nonce 포함) | 운영자·갱신 결제 시 복호화 필요 |
| `billing_key_hash` | SHA-256 16진수 | 중복 탐지·감사 로그 참조용 |

암호화: `app/core/crypto.py` `AesGcmCipher.encrypt/decrypt`  
해시: `app/core/security.py` `sha256_hex`

---

## 7. 감사 로그

| 이벤트 | `action` | `target_type` | `detail` |
|--------|----------|--------------|----------|
| 신규 카드 등록 | `card.register` | `card` | 공통 detail |
| 카드 교체 | `card.replace` | `card` | 공통 detail |
| 카드 삭제 | `card.delete` | `card` | 공통 detail |
| 카드 활성화 | `card.activate` | `card` | 공통 detail + `is_active: true` |
| 카드 비활성화 | `card.deactivate` | `card` | 공통 detail + `is_active: false` |

**공통 detail** — 모든 카드 이벤트는 `_card_audit_detail(card, **extra)`로 동일한 상세를 남깁니다:
`{"external_user_id", "service_id"(스코프 필터용), "card_number"(마스킹), "issuer"}`.
화면(감사로그·서비스 이벤트 섹션)에는 사용자·카드번호·발급사가 한글로 표시되며,
`service_id`는 원시 UUID라 표시하지 않고 서비스 상세 이벤트 스코프 필터에만 쓰입니다.
빌링키 암호문·해시는 감사로그에 넣지 않습니다.

등록/교체/삭제는 `actor_type="SERVICE"`(외부 API 컨텍스트), 활성/비활성 토글은
`actor_type="USER"`(어드민 관리자)로 기록됩니다. 감사로그 한글 라벨은
`audit_labels.py`의 `ACTION_LABELS`(`card.*`)·`TARGET_TYPE_LABELS["card"]="카드"`에 정의됩니다.

---

## 8. best-effort 빌링키 삭제

카드 **교체** 또는 **삭제** 시 기존 빌링키를 `safe_delete_billing_key(toss, billing_key)` 로 삭제합니다 (`app/services/payment_utils.py`).

- **성공**: 토스에서 키 삭제 완료
- **404**: 이미 삭제됨 → 성공으로 간주
- **그 외 오류**: WARNING 로그 기록 + `False` 반환, **교체 커밋에는 영향 없음**

---

## 9. 관련 테스트

### 서비스 레이어 — `tests/integration/test_cards.py` (13 passed)

| 테스트 | 검증 내용 |
|--------|----------|
| `test_register_card_stores_encrypted_billing_key` | 빌링키 암호화 저장 + 평문과 다름 확인 |
| `test_replace_card_reuses_same_row` | 재등록 시 같은 `id` 유지 (행 교체) |
| `test_replace_card_updates_billing_key` | 교체 후 `billing_key_hash` 변경 확인 |
| `test_get_card_returns_none_when_not_found` | 미등록 카드 → None |
| `test_get_card_returns_registered_card` | 등록 후 조회 성공 |
| `test_register_card_invalid_customer_key_raises` | 잘못된 customer_key → InputValidationError |
| `test_register_card_empty_external_user_id_raises` | 빈 external_user_id → InputValidationError |
| `test_different_users_get_separate_cards` | 다른 사용자는 각자 카드 보유 |
| `test_replace_deletes_old_billing_key_best_effort` | 교체 시 기존 빌링키 삭제 호출 확인 |
| `test_billing_key_issue_failure_no_card_created` | 빌링키 발급 실패(TossError) → 카드 행 미생성 확인 |
| `test_delete_card_blocked_when_active_subscription` | ACTIVE 구독이 카드 참조 시 ConflictError |
| `test_delete_card_allowed_when_canceled` | CANCELED 구독만 있을 때 삭제 허용 + get_card → None |
| `test_delete_card_not_found` | 카드 미등록 시 NotFoundError |

### 외부 API — `tests/integration/test_cards_api.py` (6 passed)

| 테스트 | 검증 내용 |
|--------|----------|
| `test_register_then_get_card` | POST 201 + billingKey 미노출 확인 + GET 200 |
| `test_delete_card` | POST → DELETE 204 → GET 404 |
| `test_get_card_not_found` | 미등록 카드 GET → 404 NOT_FOUND |
| `test_register_replaces_existing_card` | 재등록 POST 201 + 이후 GET 200 |
| `test_register_card_requires_auth` | 인증 헤더 없이 POST → 401 |
| `test_delete_card_not_found` | 미등록 카드 DELETE → 404 NOT_FOUND |

---

## 10-1. 카드 활성/비활성 — 결제 차단

카드는 `is_active` 플래그로 활성/비활성을 토글할 수 있습니다(어드민 전용). **비활성(false) 카드는 모든 결제가 차단**됩니다.

**토글:** `set_card_active(db, *, card_id, is_active, actor_user_id)` (`app/services/cards.py`) — 상태 변경 + 감사로그(`card.activate`/`card.deactivate`). 이미 같은 상태면 멱등(로그 없음). 어드민 라우트 `POST /admin/cards/{id}/toggle`.

**결제 차단 지점** — 각 충전 경로에서 `get_card` 직후 `is_active`를 검사합니다.

| 경로 | 위치 | 비활성 시 동작 |
|------|------|----------------|
| 구독 자동연장·재시도 | `renewals.py:_renew_one` | 토스 호출 없이 합성 `TossError("CARD_INACTIVE")` → 기존 실패 처리(PAST_DUE/정지) |
| 구독 생성 | `subscriptions.py:create_subscription` | `ConflictError` (생성 차단) |
| 수동 재결제 | `subscriptions.py:_perform_manual_charge` | `PaymentFailedError(code="CARD_INACTIVE")` |
| 일반결제(one-off) | `payments.py:create_one_off_payment` | `ConflictError` |

> Q3 정책: 활성 구독이 있는 카드를 비활성화해도 구독 상태는 **즉시 바꾸지 않고**, 다음 자동결제 시도에서 실패 처리(PAST_DUE → 재시도 소진 시 SUSPENDED)됩니다.

## 10-2. 관리자 화면 — 카드 표시

| 화면 | 표시 내용 | 구현 |
|------|-----------|------|
| **구독 상세** (`/admin/subscriptions/{id}`) | 결제 카드(마스킹 번호+발급사+활성/비활성 뱃지, 카드 상세 링크). 미등록·비활성이면 재결제 버튼 비활성화 | `card_service.get_card(...)` |
| **결제 상세** (`/admin/payments/{id}`) | “결제 카드” 행 — 실제 충전 카드(`raw_response.card.number`) 우선, 없으면 보관함 카드 | `payment_detail`이 `get_card` 로드 |
| **서비스 상세** (`/admin/services/{id}`) — “등록 카드” 섹션 | 사용자별 1행 리스트 + **상태 뱃지 + 활성/비활성 토글 버튼**. 행 클릭 → 카드 상세 | `services.py` `_cards_tab()` → `services/_cards_table.html` |
| **카드 상세** (`/admin/cards/{card_id}`) | 카드 정보 전체 + 토글 버튼 + **이 카드로 결제한 내역**(구독+일반) | `cards.py:cards_detail` → `cards/detail.html` |

**카드별 결제내역 연결:** Payment에는 `card_id`가 없지만 `(service_id, external_user_id)`가 Card 고유키와 같으므로, 동일 `(service_id, external_user_id)`의 Payment를 그 카드의 결제내역으로 조회합니다(구독·일반결제 모두 포함, 스키마 변경 없음).

서비스 상세 “등록 카드” 섹션(`_cards_table.html`) 컬럼: 사용자(정렬) / 카드번호(마스킹) / 발급사코드 / **상태** / customerKey / 빌링키 해시(앞 12자) / 등록일(정렬) / 변경일(정렬) / 토글 버튼. 페이징 `kpage`(10건), 사용자 ID 검색 `q`, htmx 컨테이너 id `list-svc-cards`. **빌링키 암호문은 절대 노출하지 않습니다.**

---

## 10. 유지보수 팁

- **billingKey 노출 방지**: `CardResponse.from_model()`은 `card_info`(마스킹 정보)만 반환하며 `billing_key_encrypted`는 절대 포함하지 않습니다.
- **빌링키 복호화 실패 시**: `cipher.decrypt()` 가 `InvalidTag` 예외를 발생시킵니다. `ENCRYPTION_KEY` 환경변수가 바뀌지 않았는지 확인하세요.
- **카드 없이 구독 시**: `POST /api/v1/subscriptions` 호출 전에 반드시 `POST /api/v1/cards`로 카드를 등록해야 합니다. 미등록 시 404 `NOT_FOUND` 오류가 반환됩니다(Task 7 이후).
- **`cards` 테이블 서비스 삭제 제약**: `service_id` FK가 `RESTRICT`로 설정돼 카드가 있는 서비스는 DB 레벨에서 삭제가 차단됩니다.
- **`subscriptions.card_id` NULL 허용**: CANCELED/EXPIRED 구독은 카드 삭제 시 `card_id`가 NULL로 초기화됩니다. billing-active 구독이 카드를 참조하는 경우에는 앱 레이어에서 미리 차단합니다.
- **Task 7 완료**: 구독 생성(`POST /api/v1/subscriptions`)이 카드 보관함 빌링키를 참조하도록 변경됐습니다. 단건 결제(T8·T9)는 후속 태스크에서 처리 예정.
