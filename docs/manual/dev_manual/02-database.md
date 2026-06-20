# 02. 데이터베이스 — 테이블·모델·관계·마이그레이션

> **쉽게 말하면**: DB는 시스템의 **장부**입니다. 어떤 서비스가 등록됐는지(`services`), 누가 무슨 요금제를 구독 중인지(`subscriptions`), 언제 얼마가 결제됐는지(`payments`)를 표로 보관합니다. 코드의 **모델 클래스 1개(`app/models/*.py`)가 DB 표 1개**에 그대로 대응하므로, 표를 알면 코드가 보이고 코드를 알면 표가 보입니다.

> **초보자 안내**: 이 문서는 `app/models/*.py` 파일과 `alembic/versions/` 마이그레이션 파일을 직접 읽어 작성했습니다. 실제 코드와 1:1 대응하므로 파일·라인 번호를 함께 기재합니다. 관련 API 기능(구독 생성, 결제 처리 등)은 04~15 문서를 참고하세요.

---

## 1. 테이블 목록

| # | 테이블명 | 모델 클래스 | 모델 파일 | 역할 한 줄 요약 |
|---|---------|-----------|---------|--------------|
| 1 | `services` | `Service` | `app/models/service.py` | 구독·결제 API를 이용하는 사내 서비스 등록 정보 |
| 2 | `users` | `User` | `app/models/user.py` | htmx 관리 화면 로그인 계정(관리자) |
| 3 | `password_setup_tokens` | `PasswordSetupToken` | `app/models/user.py` | 신규 계정 비밀번호 초기 설정용 일회용 토큰 |
| 4 | `user_services` | `UserService` | `app/models/user_service.py` | 관리자↔서비스 다대다 연결 (추가 담당 서비스) |
| 5 | `plans` | `Plan` | `app/models/plan.py` | 구독 요금제 정의(가격·주기·할인·체험) |
| 6 | `subscriptions` | `Subscription` | `app/models/subscription.py` | 외부 사용자의 요금제 구독 상태 |
| 7 | `payments` | `Payment` | `app/models/payment.py` | 개별 결제 시도 레코드(구독 정기결제·단건 모두) |
| 8 | `webhook_events` | `WebhookEvent` | `app/models/webhook_event.py` | 토스페이먼츠로부터 수신한 웹훅 이벤트 기록 |
| 9 | `audit_logs` | `AuditLog` | `app/models/audit_log.py` | 시스템 내 모든 중요 행위의 불변 이력 |
| 10 | `global_settings` | `GlobalSettings` | `app/models/global_settings.py` | 자동결제 재시도·어드민IP·킬스위치 전역 설정(단일 행) |
| 11 | `cards` | `Card` | `app/models/card.py` | 결제수단 보관함(vault) — 토스 빌링키 암호화 보관 |

**총 11개 테이블** (파이썬 클래스 12개 — `PasswordSetupToken`이 `user.py`에 함께 있음)

---

## 2. 공통 규칙

### 2.1 TimestampMixin (`app/models/base.py:27-37`)

모든 주요 모델은 `TimestampMixin`을 상속받아 두 컬럼을 자동으로 갖습니다.

```
created_at  DateTime(timezone=True)  레코드 삽입 시 DB 서버 시각으로 채워짐(UTC)
updated_at  DateTime(timezone=True)  매 UPDATE마다 DB 서버 시각으로 갱신됨(UTC)
```

> **포인트**: 모든 시각은 **UTC**로 저장합니다. 화면에 표시할 때 한국 시간(UTC+9)으로 변환하는 것은 프론트엔드 책임입니다.

`TimestampMixin`을 사용하는 테이블: `services`, `users`, `plans`, `subscriptions`, `payments`, `cards`, `global_settings`
사용하지 않는 테이블: `password_setup_tokens`, `user_services`, `webhook_events`, `audit_logs` (이들은 자체적으로 `created_at`만 갖거나 없음)

### 2.2 금액은 KRW 정수

모든 금액 컬럼(`price`, `amount`, `canceled_amount`, `cancel_fee` 등)은 `BigInteger`로 선언되며 **원 단위 정수**를 저장합니다. 소수점 없음.

### 2.3 민감정보 보안 처리

| 정보 | 저장 방식 | 컬럼 예시 |
|-----|---------|---------|
| API 키 | SHA-256 해시(인증 검증) + AES-GCM 암호문(화면 표시용) | `api_key_hash`, `api_key_encrypted` |
| HMAC 시크릿 | AES-GCM 암호문 | `hmac_secret_encrypted` |
| 빌링키 | AES-GCM 암호문 + SHA-256 해시 | `billing_key_encrypted`, `billing_key_hash` |
| 비밀번호 | Argon2id 해시 | `password_hash` |
| 토큰 | SHA-256 해시 | `token_hash` |

**평문은 절대 DB에 저장하지 않습니다.**

### 2.4 명명 규칙 (`app/models/base.py:12-18`)

`Base` 클래스는 Alembic이 자동 생성하는 인덱스·제약 이름이 일관되도록 고정된 `naming_convention`을 사용합니다.

---

## 3. 테이블 상세

### 3.1 `services` — 서비스 등록 정보

> 파일: `app/models/service.py:12-33`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | UUID | N | `uuid4()` | PK |
| `name` | String(100) | N | — | 서비스명, **전체 고유(UNIQUE)** |
| `allowed_ips` | JSONB | N | `[]` | API 호출 허용 IP 목록. **어드민 등록 시 1개 이상 필수**(빈 목록 불가). 비면 외부 인증이 모두 차단됨 — `admin_allowed_ips`(빈 배열=제한 없음)와 동작이 다름 |
| `manager_email` | String(255) | N | — | 서비스 담당자 이메일 |
| `api_key_hash` | String(64) | N | — | SHA-256 해시, **UNIQUE + INDEX**, 인증 검증에만 사용 |
| `hmac_secret_encrypted` | String(512) | N | — | 웹훅 서명 검증용 HMAC 시크릿 (AES 암호화) |
| `api_key_encrypted` | String(512) | **Y** | NULL | 관리 화면 키 표시용 AES 암호문 (요청 005에서 추가) |
| `status` | String(20) | N | `ACTIVE` | `ServiceStatus` 값 |
| `cancellation_enabled` | Boolean | N | `true` | 단건결제 취소 허용 여부 |
| `cancellation_fee_percent` | Integer | N | `0` | 취소 수수료율 (0~100, %) |
| `created_at` / `updated_at` | DateTime(tz) | N | `now()` | TimestampMixin |

- **FK 받는 곳**: `plans.service_id`, `subscriptions.service_id`, `payments.service_id`, `users.service_id`, `user_services.service_id`

---

### 3.2 `users` — 관리자 계정

> 파일: `app/models/user.py:19-37`

> **주의**: 이 테이블의 사용자는 **관리 화면 로그인 계정**입니다. 외부 서비스를 실제 이용하는 최종 사용자(end-user)가 아닙니다. 최종 사용자는 `subscriptions.external_user_id`로만 식별합니다.

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | UUID | N | `uuid4()` | PK |
| `email` | String(255) | N | — | 로그인 ID, **전체 고유(UNIQUE)** |
| `phone` | String(30) | **Y** | NULL | 연락처 (선택) |
| `password_hash` | String(512) | N | `""` | Argon2id 해시; PENDING 상태에서는 빈 문자열 |
| `role` | String(20) | N | — | `UserRole` 값: `SYSTEM_ADMIN` 또는 `SERVICE_MANAGER` |
| `service_id` | UUID | **Y** | NULL | FK → `services.id` (CASCADE); SYSTEM_ADMIN은 NULL |
| `status` | String(20) | N | `PENDING` | `UserStatus` 값 |
| `failed_login_count` | Integer | N | `0` | 연속 로그인 실패 횟수 (임계 초과 시 LOCKED) |
| `locked_until` | DateTime(tz) | **Y** | NULL | 자동 잠금 해제 시각(UTC); NULL = 잠금 없음 |
| `created_at` / `updated_at` | DateTime(tz) | N | `now()` | TimestampMixin |

- **`service_id` FK**: `ondelete="CASCADE"` — 서비스 삭제 시 해당 담당자 계정도 자동 삭제
- `SYSTEM_ADMIN`은 `service_id = NULL`, `SERVICE_MANAGER`는 담당 서비스의 UUID

---

### 3.3 `password_setup_tokens` — 비밀번호 초기 설정 토큰

> 파일: `app/models/user.py:40-54`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | UUID | N | `uuid4()` | PK |
| `user_id` | UUID | N | — | FK → `users.id` (CASCADE) |
| `token_hash` | String(64) | N | — | SHA-256 해시, **UNIQUE** (평문은 이메일 링크에만) |
| `expires_at` | DateTime(tz) | N | — | 링크 유효 기한 (UTC) |
| `used_at` | DateTime(tz) | **Y** | NULL | 최초 사용 시각 (UTC); NULL = 미사용 → 재사용 불가 |

- `used_at`이 채워진 토큰은 재사용 불가 (코드에서 검사)
- 계정 삭제 시 CASCADE로 토큰도 함께 삭제

---

### 3.4 `user_services` — 관리자↔서비스 다대다 연결

> 파일: `app/models/user_service.py:11-25`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `user_id` | UUID | N | — | **PK(복합)**, FK → `users.id` (CASCADE) |
| `service_id` | UUID | N | — | **PK(복합)**, FK → `services.id` (CASCADE) |
| `created_at` | DateTime(tz) | N | `now()` | 담당 서비스 부여 시각 (UTC) |

- `(user_id, service_id)` 복합 기본키 → 동일 조합은 하나만 존재
- `users.service_id`(주 서비스)와 **별개**. 유효 담당 서비스 = `users.service_id` ∪ 이 테이블의 `service_id`
- 관리자나 서비스가 삭제되면 연결 행도 CASCADE 삭제

---

### 3.5 `plans` — 구독 요금제

> 파일: `app/models/plan.py:16-48`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | UUID | N | `uuid4()` | PK |
| `service_id` | UUID | N | — | FK → `services.id` (**RESTRICT**); 구독이 있으면 삭제 불가 |
| `name` | String(100) | N | — | 요금제 표시명 |
| `price` | BigInteger | N | — | 정가 (원 단위 KRW 정수) |
| `currency` | String(3) | N | `"KRW"` | 통화 코드 (현재 KRW만 사용) |
| `billing_cycle` | String(10) | N | — | `BillingCycle` 값: `YEAR`/`MONTH`/`WEEK`/`DAY` |
| `cycle_days` | Integer | **Y** | NULL | `billing_cycle=DAY`일 때 실제 일수; 나머지는 NULL |
| `first_payment_type` | String(20) | N | `NONE` | `FirstPaymentType` 값 (첫 결제 혜택 유형) |
| `first_payment_value` | BigInteger | **Y** | NULL | 첫 결제 할인 값 (원 또는 %) |
| `recurring_discount_type` | String(20) | N | `NONE` | `DiscountType` 값 (상시 할인 유형) |
| `recurring_discount_value` | BigInteger | **Y** | NULL | 상시 할인 값 (원 또는 %) |
| `trial_enabled` | Boolean | N | `false` | 체험 기능 활성 여부 |
| `trial_days` | Integer | **Y** | NULL | 체험 기간(일수); `trial_enabled=True`일 때만 유효 |
| `auto_renew` | Boolean | N | `true` | `False`이면 첫 주기 후 자동연장 없음 |
| `extra_info` | JSONB | N | `{}` | 서비스 측 요금제 설명용 key/value (외부 API 노출) |
| `status` | String(20) | N | `ACTIVE` | `PlanStatus` 값 |
| `created_at` / `updated_at` | DateTime(tz) | N | `now()` | TimestampMixin |

- **삭제 규칙**: 연결된 구독(`subscriptions.plan_id`)이 1개라도 있으면 RESTRICT로 삭제 불가
- `billing_cycle=DAY` + `cycle_days=7` 이면 7일 주기 결제

---

### 3.6 `subscriptions` — 구독 상태

> 파일: `app/models/subscription.py:20-56`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | UUID | N | `uuid4()` | PK |
| `service_id` | UUID | N | — | FK → `services.id` (**RESTRICT**) |
| `plan_id` | UUID | N | — | FK → `plans.id` (**RESTRICT**) |
| `external_user_id` | String(255) | N | — | 외부 서비스 측 사용자 식별자 (내부 `users`와 무관) |
| `card_id` | UUID | N | — | FK → `cards.id` (**RESTRICT**), INDEX; 결제에 사용할 등록 카드 |
| `status` | String(20) | N | `ACTIVE` | `SubscriptionStatus` 값 |
| `current_period_start` | DateTime(tz) | N | — | 현재 결제 주기 시작 (UTC) |
| `current_period_end` | DateTime(tz) | N | — | 현재 결제 주기 종료 = 접근 만료 시각 (UTC) |
| `next_billing_at` | DateTime(tz) | **Y** | NULL | 다음 자동결제 예정 시각 (UTC); 스케줄러가 조회 |
| `retry_count` | Integer | N | `0` | PAST_DUE 상태에서 결제 재시도 누적 횟수 |
| `suspended_at` | DateTime(tz) | **Y** | NULL | SUSPENDED 진입 시각; 유예일 초과 시 EXPIRED 판정 기준 |
| `created_at` / `updated_at` | DateTime(tz) | N | `now()` | TimestampMixin |

#### 특수 인덱스 (`app/models/subscription.py:46-55`)

```python
# 서비스+사용자 당 1개 구독 규칙 — EXPIRED만 제외하여 재구독 허용
Index(
    "uq_subscriptions_one_per_user",
    "service_id", "external_user_id",
    unique=True,
    postgresql_where=text(
        "status IN ('TRIAL','ACTIVE','PAST_DUE','SUSPENDED','CANCELED','EXTENDED')"),
)

# 스케줄러의 결제 대상 조회 성능용 복합 인덱스
Index("ix_subscriptions_due", "status", "next_billing_at")

# 감사 Phase 3(성능 M1) — 어드민 스코프 필터·배치 만료 조회용
Index("ix_subscriptions_service_id", "service_id")
Index("ix_subscriptions_status_period_end", "status", "current_period_end")
```

payments/audit_logs에도 같은 마이그레이션(b8c9d0e1f2a3)으로 조회 인덱스가 추가되어
있다: `ix_payments_status_requested`(정산 스윕·결제목록), `ix_payments_service_approved`
(대시보드·정산 집계), `ix_audit_logs_created_at`(감사 목록·기간 집계),
`ix_audit_logs_target`(대상별 이벤트 조회).

> **핵심 규칙**: 동일한 `(service_id, external_user_id)` 조합으로 TRIAL/ACTIVE/PAST_DUE/SUSPENDED/CANCELED 상태 구독이 이미 있으면 신규 구독 시도 시 DB 에러 발생 → 서비스+사용자 당 1개 구독만 가능.
> EXPIRED가 되면 이 인덱스에서 제외되므로 재구독이 가능합니다.

---

### 3.7 `payments` — 결제 레코드

> 파일: `app/models/payment.py:19-46`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | UUID | N | `uuid4()` | PK |
| `subscription_id` | UUID | **Y** | NULL | FK → `subscriptions.id` (**RESTRICT**), INDEX; **단건은 NULL** |
| `service_id` | UUID | N | — | FK → `services.id` (**RESTRICT**), INDEX |
| `external_user_id` | String(255) | **Y** | NULL | 결제 대상 외부 사용자 ID (단건 추적용으로도 사용) |
| `kind` | String(20) | N | `SUBSCRIPTION` | `PaymentKind` 값, INDEX |
| `order_id` | String(64) | N | — | 주문 ID, **(service_id, order_id) 복합 유니크** — 서비스 내 고유(감사 Phase 2, 보안 M-1) |
| `toss_order_id` | String(64) | N | — | 토스 전달용 주문 ID, **전체 고유(UNIQUE)**. 구독 결제는 order_id와 동일, 단건은 서버 생성(`t`+uuid hex) |
| `toss_payment_key` | String(200) | **Y** | NULL | 토스 승인 후 발급 paymentKey (취소·조회에 사용) |
| `order_name` | String(255) | **Y** | NULL | 상품명(토스 orderName). 단건=클라이언트 전달값, 구독=요금제명(`plan.name`). 결제 상세에 표시. 과거 데이터는 NULL (마이그레이션 `f2a3b4c5d6e7`) |
| `amount` | BigInteger | N | — | 실제 청구 금액 (원 단위 KRW 정수) |
| `payment_type` | String(10) | N | — | `PaymentType` 값: `FIRST`/`RENEWAL`/`RETRY`/`ONE_OFF` |
| `status` | String(10) | N | `PENDING` | `PaymentStatus` 값 |
| `failure_code` | String(100) | **Y** | NULL | 토스 실패 코드 (FAILED 상태일 때 채워짐) |
| `failure_message` | String(500) | **Y** | NULL | 토스 실패 메시지 (사용자 표시용) |
| `idempotency_key` | String(300) | N | — | 토스 API 멱등성 키 (중복 요청 방지) |
| `requested_at` | DateTime(tz) | N | — | 결제 요청 생성 시각 (UTC) |
| `approved_at` | DateTime(tz) | **Y** | NULL | 토스 승인 완료 시각 (UTC); 실패 시 NULL |
| `raw_response` | JSONB | **Y** | NULL | 토스 API 응답 원문 (사후 분석·감사용) |
| `canceled_amount` | BigInteger | **Y** | NULL | 실제 환불액 (금액 - 수수료); 부분취소 시 `amount`와 다름 |
| `cancel_fee` | BigInteger | **Y** | NULL | 차감 수수료 (수수료율 × amount ÷ 100) |
| `canceled_at` | DateTime(tz) | **Y** | NULL | 취소 완료 시각 (UTC) |
| `created_at` / `updated_at` | DateTime(tz) | N | `now()` | TimestampMixin |

- **구독 결제**: `kind=SUBSCRIPTION`, `subscription_id` 있음, `payment_type`=`FIRST`/`RENEWAL`/`RETRY`
- **단건 결제**: `kind=ONE_OFF`, `subscription_id=NULL`, `payment_type=ONE_OFF`
- 레코드는 삭제하지 않습니다 — 실패한 시도도 영구 보존

---

### 3.8 `webhook_events` — 웹훅 이벤트

> 파일: `app/models/webhook_event.py:18-29`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | UUID | N | `uuid4()` | PK |
| `transmission_id` | String(100) | N | — | 토스가 부여한 전송 고유 ID, **UNIQUE** (중복 수신 방지) |
| `event_type` | String(100) | N | — | 이벤트 종류 (예: `PAYMENT_STATUS_CHANGED`) |
| `payload` | JSONB | N | — | 토스 웹훅 원문 페이로드 |
| `status` | String(20) | N | `RECEIVED` | `WebhookStatus` 값 |
| `received_at` | DateTime(tz) | N | `now()` | 웹훅 수신 시각 (UTC) |
| `processed_at` | DateTime(tz) | **Y** | NULL | 처리 완료 시각 (UTC); 미처리 시 NULL |

- `transmission_id` UNIQUE 제약으로 동일 웹훅 중복 처리를 DB 수준에서 방지 (멱등성 보장)

---

### 3.9 `audit_logs` — 감사 로그

> 파일: `app/models/audit_log.py:17-35`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | UUID | N | `uuid4()` | PK |
| `actor_user_id` | UUID | **Y** | NULL | USER 행위자일 때 `users.id` (FK 없음, 소프트 참조) |
| `actor_service_id` | UUID | **Y** | NULL | SERVICE 행위자일 때 `services.id` (소프트 참조) |
| `actor_type` | String(10) | N | — | `USER` / `SERVICE` / `SYSTEM` |
| `action` | String(100) | N | — | 행위 식별자 (예: `subscription.cancel`), INDEX |
| `target_type` | String(50) | **Y** | NULL | 대상 엔티티 종류 (예: `Subscription`) |
| `target_id` | String(64) | **Y** | NULL | 대상 엔티티 PK (문자열 직렬화) |
| `detail` | JSONB | **Y** | NULL | 변경 전·후 값 등 부가 정보 |
| `ip_address` | String(45) | **Y** | NULL | 요청 IP (IPv6 포함 최대 45자) |
| `created_at` | DateTime(tz) | N | `now()` | 이벤트 발생 시각 (UTC) |

- **삽입만 허용**, 수정·삭제하지 않습니다 (불변 이력)
- `actor_user_id`와 `actor_service_id`는 FK 제약 없음 — 계정이 삭제된 후에도 이력이 보존됩니다
- `actor_type`에 따라 `actor_user_id` 또는 `actor_service_id` 중 하나만 채워짐; SYSTEM은 둘 다 NULL

---

### 3.10 `global_settings` — 전역 운영 설정

> 파일: `app/models/global_settings.py:16-29`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | Integer | N | `1` | PK; **항상 1** (싱글톤 행) |
| `retry_limit` | Integer | N | `4` | 자동결제 실패 재시도 최대 횟수 |
| `retry_interval_hours` | Integer | N | `12` | 재시도 간격 (시간) |
| `suspended_grace_days` | Integer | N | `30` | SUSPENDED 상태 유예 기간 (일); 초과 시 EXPIRED |
| `admin_allowed_ips` | JSONB | N | `[]` | 어드민 접속 허용 IP 목록 (빈 배열 = 제한 없음) |
| `server_disabled` | Boolean | N | `false` | 결제서버 킬스위치 (`true`이면 외부 API 차단) |
| `disabled_reason` | String(500) | **Y** | NULL | 비활성화 사유 (서비스 API 응답에 반환) |
| `disabled_at` | DateTime(tz) | **Y** | NULL | 비활성화 시각 (UTC) |
| `disabled_by` | UUID | **Y** | NULL | 비활성화한 관리자 user id |
| `created_at` / `updated_at` | DateTime(tz) | N | `now()` | TimestampMixin |

- 코드는 항상 `id=1` 행에 접근하는 **get_or_create** 패턴 사용
- 런타임에 변경하면 스케줄러·어드민 IP 검사·외부 API 게이트에 즉시 반영

---

### 3.11 `cards` — 결제수단 보관함(vault)

> 파일: `app/models/card.py:15-32`

| 컬럼 | 타입 | Nullable | 기본값 | 설명 |
|-----|------|---------|--------|-----|
| `id` | UUID | N | `uuid4()` | PK |
| `service_id` | UUID | N | — | FK → `services.id` (**RESTRICT**), INDEX; 서비스 삭제 불가 |
| `external_user_id` | String(255) | N | — | 외부 서비스 측 사용자 식별자. **(service_id, external_user_id) 복합 유니크** |
| `customer_key` | String(300) | N | — | 토스페이먼츠 customerKey (빌링키 발급·관리에 사용) |
| `billing_key_encrypted` | String(1024) | N | — | 토스 빌링키 — AES-GCM 암호화 보관 (평문 저장 안 함) |
| `billing_key_hash` | String(64) | N | — | 빌링키 SHA-256 해시 (중복 탐지·조회용), INDEX |
| `card_info` | JSONB | **Y** | NULL | 카드 표시 정보 (마스킹된 번호·발급사 등, 토스 응답 부분 저장) |
| `created_at` / `updated_at` | DateTime(tz) | N | `now()` | TimestampMixin |

#### 제약

- `uq_cards_service_user` — `(service_id, external_user_id)` 복합 유니크: 서비스+사용자 당 카드 1건만 허용
- `service_id` FK는 `ondelete=RESTRICT` — 카드가 있는 서비스는 삭제 불가
- 카드 교체(재등록) 시 기존 레코드를 UPDATE 또는 DELETE 후 INSERT — 동일 (service_id, external_user_id)로 두 행을 동시에 가질 수 없음
- **카드 삭제 정책**: soft-delete 없음. 활성 구독(`ACTIVE` 상태)이 참조 중인 카드는 서비스 레이어에서 삭제를 차단한다. 활성 구독이 없을 때만 하드 삭제(hard-delete) 허용.

> **vault 개념**: 빌링키는 카드 번호와 동급의 민감 정보입니다. `billing_key_encrypted`에 AES-GCM으로만 저장하고, 인증 검증 없이 평문이 노출되는 경로를 만들지 않습니다.

---

## 4. 관계도 (텍스트)

```
services (1)
  ├─── plans (N)          service_id FK, ondelete=RESTRICT
  ├─── subscriptions (N)  service_id FK, ondelete=RESTRICT
  ├─── payments (N)       service_id FK, ondelete=RESTRICT
  ├─── cards (N)          service_id FK, ondelete=RESTRICT
  ├─── users (N)          service_id FK, ondelete=CASCADE   ← 주 담당 서비스
  └─── user_services (N)  service_id FK, ondelete=CASCADE   ← 추가 담당 서비스

users (N) ──── user_services (M) ──── services (N)
  (다대다: 한 관리자가 여러 서비스 담당 가능)

users (1)
  └─── password_setup_tokens (N)  user_id FK, ondelete=CASCADE

plans (1)
  └─── subscriptions (N)  plan_id FK, ondelete=RESTRICT

subscriptions (1)
  └─── payments (N)       subscription_id FK, ondelete=RESTRICT  ← 단건 결제는 NULL
```

### FK + ondelete 요약

| FK 컬럼 (테이블) | 참조 | ondelete |
|---------------|------|---------|
| `plans.service_id` | `services.id` | RESTRICT (구독 없을 때만 삭제 가능) |
| `subscriptions.service_id` | `services.id` | RESTRICT |
| `subscriptions.plan_id` | `plans.id` | RESTRICT |
| `payments.subscription_id` | `subscriptions.id` | RESTRICT |
| `payments.service_id` | `services.id` | RESTRICT |
| `users.service_id` | `services.id` | **CASCADE** |
| `user_services.user_id` | `users.id` | **CASCADE** |
| `user_services.service_id` | `services.id` | **CASCADE** |
| `password_setup_tokens.user_id` | `users.id` | **CASCADE** |
| `cards.service_id` | `services.id` | RESTRICT |

> **RESTRICT**: 참조 중인 행이 있으면 삭제 불가 → 안전망  
> **CASCADE**: 부모가 삭제되면 자식도 자동 삭제 → 계정·서비스 정리에 사용

---

## 5. Enum 값 상세

> 파일: `app/models/enums.py`

### `ServiceStatus` (line 13)
| 값 | 의미 |
|----|-----|
| `ACTIVE` | 정상 운영 중인 서비스 (API 키 인증 가능) |
| `INACTIVE` | 비활성화된 서비스 (API 키 인증 불가) |

### `UserRole` (line 18)
| 값 | 의미 |
|----|-----|
| `SYSTEM_ADMIN` | 전체 관리자 — 서비스·관리자 계정 전체 접근 |
| `SERVICE_MANAGER` | 서비스 담당자 — 자신이 담당하는 서비스만 접근 |

### `UserStatus` (line 23)
| 값 | 의미 |
|----|-----|
| `PENDING` | 생성됨, 비밀번호 설정 대기 |
| `ACTIVE` | 정상 로그인 가능 |
| `LOCKED` | 로그인 연속 실패로 잠김 (자동 해제 — `locked_until` 이후) |
| `DISABLED` | 관리자가 비활성화 (복구 가능) |
| `DELETED` | 관리자가 삭제 (소프트 삭제, 화면에서 숨김) |

### `BillingCycle` (line 31)
| 값 | 의미 |
|----|-----|
| `YEAR` | 연 단위 결제 |
| `MONTH` | 월 단위 결제 |
| `WEEK` | 주 단위 결제 |
| `DAY` | 일 단위 결제 — `Plan.cycle_days`에 원하는 일수를 함께 지정 |

### `FirstPaymentType` (line 40)
| 값 | 의미 |
|----|-----|
| `NONE` | 첫 결제 혜택 없음 (정상 금액 청구) |
| `FREE` | 첫 결제 무료 (0원) |
| `DISCOUNT_AMOUNT` | 첫 결제 정액 할인 — `first_payment_value`(원) 차감 |
| `DISCOUNT_PERCENT` | 첫 결제 정률 할인 — `first_payment_value`(%) 비율 할인 |

### `DiscountType` (line 55)
| 값 | 의미 |
|----|-----|
| `NONE` | 상시 할인 없음 |
| `DISCOUNT_AMOUNT` | 상시 정액 할인 (원) |
| `DISCOUNT_PERCENT` | 상시 정률 할인 (%) |

> `DiscountType`은 `FirstPaymentType`에서 `FREE`가 빠진 버전입니다. 상시 할인에 "무료" 개념은 없습니다.

### `PlanStatus` (line 62)
| 값 | 의미 |
|----|-----|
| `ACTIVE` | 신규 구독 가능한 정상 요금제 |
| `ARCHIVED` | 신규 구독 불가 (기존 구독은 유지됨); 구독이 남아있으면 삭제 불가 |

### `SubscriptionStatus` (line 67)
| 값 | 접근 허용? | 의미 |
|----|----------|-----|
| `TRIAL` | O | 체험 중 — 만료 시 첫 정기 결제 |
| `ACTIVE` | O | 정상 이용 |
| `PAST_DUE` | O | 결제 실패/유예 (접근은 유지, 재시도 진행 중) |
| `SUSPENDED` | X | 강제 정지 — 수동 결제 대기 (접근 차단) |
| `CANCELED` | O | 해지 예약 — 만료일까지 이용 가능, 이후 EXPIRED |
| `EXTENDED` | O | 연장처리 — 운영자가 만료일 수동 연장, 새 만료일에 자동결제 갱신 |
| `EXPIRED` | X | 완전 종료 (재구독 시 새 레코드 생성 가능) |

`ACCESS_ALLOWED_STATUSES = {TRIAL, ACTIVE, PAST_DUE, CANCELED, EXTENDED}`  
`OPEN_SUBSCRIPTION_STATUSES = (TRIAL, ACTIVE, PAST_DUE, SUSPENDED, CANCELED, EXTENDED)` — 이 상태에서는 1구독 제한 적용

### `PaymentStatus` (line 93)
| 값 | 의미 |
|----|-----|
| `PENDING` | 결제 요청 생성됨, 토스 승인 응답 대기 |
| `DONE` | 토스 승인 완료 |
| `FAILED` | 토스 거절 또는 네트워크 오류 |
| `CANCELED` | 승인 후 취소 처리됨 |

### `PaymentType` (line 102)
| 값 | 의미 |
|----|-----|
| `FIRST` | 최초 결제 (첫 구독 시 할인 적용 대상) |
| `RENEWAL` | 정기 자동 갱신 결제 |
| `RETRY` | PAST_DUE 상태에서 재시도한 결제 |
| `ONE_OFF` | 단건(구독 무관) 결제 |

### `PaymentKind` (line 111)
| 값 | 의미 |
|----|-----|
| `SUBSCRIPTION` | 구독에 묶인 정기(자동) 결제 |
| `ONE_OFF` | 구독과 무관한 단건 즉시 결제 |

### `WebhookStatus` (line 118)
| 값 | 의미 |
|----|-----|
| `RECEIVED` | 수신됨, 아직 처리 전 |
| `PROCESSED` | 정상 처리 완료 |
| `IGNORED` | 중복·무관 이벤트로 무시 |
| `FAILED` | 처리 중 오류 발생 |

---

## 6. 마이그레이션 (Alembic)

### 6.1 기본 개념

Alembic은 SQLAlchemy ORM 모델과 실제 DB 스키마 사이의 변경 이력을 버전으로 관리합니다.  
각 마이그레이션 파일은 `revision`(현재 버전 ID)과 `down_revision`(이전 버전 ID)을 체인 형태로 연결합니다.

```
[최초 스키마] → [user_services 추가] → [trial/suspended 추가] → ... → [최신]
```

### 6.2 마이그레이션 리비전 체인

> 파일 위치: `alembic/versions/`

| 순서 | Revision ID | 파일명 | 변경 내용 |
|-----|------------|--------|---------|
| 1 | `8cf5f449fda1` | `8cf5f449fda1_initial_schema.py` | 초기 스키마: audit_logs, services, webhook_events, plans, users, password_setup_tokens, subscriptions, payments |
| 2 | `3501c20729e0` | `3501c20729e0_user_services_junction.py` | `user_services` 다대다 테이블 추가 |
| 3 | `2234818cce0e` | `2234818cce0e_trial_suspended_states.py` | plans에 `trial_enabled`·`trial_days`, subscriptions에 `suspended_at`, 1구독 인덱스 확장(`TRIAL`·`SUSPENDED` 포함) |
| 4 | `2caae01d3691` | `2caae01d3691_user_phone_and_statuses.py` | users에 `phone` 컬럼 추가 |
| 5 | `794d3b3fcf7c` | `794d3b3fcf7c_plan_recurring_discount.py` | plans에 `recurring_discount_type`·`recurring_discount_value` 추가 |
| 6 | `a1b2c3d4e5f6` | `a1b2c3d4e5f6_service_api_key_encrypted.py` | services에 `api_key_encrypted` 추가 (화면 표시용 AES 암호문) |
| 7 | `b2c3d4e5f6a7` | `b2c3d4e5f6a7_audit_actor_service_id.py` | audit_logs에 `actor_service_id` 추가 |
| 8 | `c3d4e5f6a7b8` | `c3d4e5f6a7b8_payment_one_off.py` | payments에 `kind`·`service_id`·`external_user_id` 추가, `subscription_id` nullable로 변경 (단건 결제 지원) |
| 9 | `d4e5f6a7b8c9` | `d4e5f6a7b8c9_cancel_policy_and_fields.py` | services에 `cancellation_enabled`·`cancellation_fee_percent`, payments에 `canceled_amount`·`cancel_fee`·`canceled_at` 추가 |
| 10 | `e5f6a7b8c9d0` | `e5f6a7b8c9d0_global_settings.py` | `global_settings` 테이블 신규 생성 |
| 11 | `f6a7b8c9d0e1` | `f6a7b8c9d0e1_plan_autorenew_extra.py` | plans에 `auto_renew`·`extra_info` 추가 |
| 12 | `a7b8c9d0e1f2` | `a7b8c9d0e1f2_payment_order_scope.py` | payments `order_id` 전역 유니크 → `(service_id, order_id)` 복합 유니크, `toss_order_id`(전역 유니크) 추가 — 감사 Phase 2(보안 M-1) |
| 13 | `b8c9d0e1f2a3` | `b8c9d0e1f2a3_perf_indexes.py` | 조회 인덱스 6종: payments(status,requested_at)/(service_id,approved_at), audit_logs(created_at)/(target_type,target_id), subscriptions(service_id)/(status,current_period_end) — 감사 Phase 3(성능 M1) |

**현재 HEAD**: `b8c9d0e1f2a3`

### 6.3 주요 명령어

```bash
# DB를 최신 버전으로 업그레이드 (배포 시 실행)
alembic upgrade head

# 현재 리비전 확인
alembic current

# 리비전 이력 조회
alembic history --verbose

# 모델 변경 후 마이그레이션 파일 자동 생성
alembic revision --autogenerate -m "설명을 여기에 작성"

# 한 단계 롤백 (주의: 운영 환경에서 신중하게 사용)
alembic downgrade -1

# 특정 리비전으로 다운그레이드
alembic downgrade 8cf5f449fda1
```

> **환경변수**: `DATABASE_URL` 환경변수가 설정되어 있으면 `alembic.ini`의 기본값을 덮어씁니다 (`alembic/env.py`에서 처리).

### 6.4 테스트에서의 스키마 처리

테스트 환경에서는 Alembic 마이그레이션을 사용하지 않고 `Base.metadata.create_all()`로 스키마를 생성합니다.

```python
# tests/conftest.py:45-48
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.drop_all)   # 기존 테이블 전체 삭제
    await conn.run_sync(Base.metadata.create_all)  # 모델로부터 테이블 새로 생성
```

- **이유**: 테스트는 매번 깨끗한 상태에서 시작해야 하므로 drop_all → create_all 패턴 사용
- **격리**: `tests/integration/conftest.py`의 `_auto_clean` fixture가 매 테스트 후 DB/Redis를 초기화
- **주의**: 테스트 DB는 운영 DB와 별개(`payment_test` 데이터베이스)

---

## 7. 상호 참조

- **구독 생성 API**: 04번 문서 (subscription 레코드 생성, 1구독 인덱스 충돌 처리)
- **결제 처리 흐름**: 05번 문서 (payment 레코드 상태 전환, 토스 API 연동)
- **스케줄러**: [05번 문서](05-subscription-renewal.md) (`ix_subscriptions_due` 인덱스를 이용한 `next_billing_at` 조회)
- **웹훅 처리**: [12번 문서](12-webhooks.md) (`webhook_events` 테이블, `transmission_id` 중복 방지)
- **관리자 인증·계정**: [13번 문서](13-admin-accounts.md) (`users`, `password_setup_tokens`)
- **암호화·인증 공통**: [03번 문서](03-auth-and-security.md) (AES-GCM, SHA-256 해시, HMAC)
- **감사 로그 기록**: 15번 문서 (`audit_logs` 삽입 패턴, `actor_type` 구분)
