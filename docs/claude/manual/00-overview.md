# 구독/결제 서버 — 전체 구성 개요

> 코드 분석 문서 시리즈의 0번. 이후 기능별 프로세스 문서(01~)가 이 구조 위에서
> "어떤 코드를 통해 어떻게 처리되는지"를 설명한다.

---

## 1. 시스템이 하는 일

사내 여러 서비스가 **공용으로 쓰는 구독·결제 백엔드**다. 결제는 토스페이먼츠
**빌링키(자동결제)** 기반이며, **단건(일반) 결제**도 지원한다. 두 종류의 사용자가 있다:

- **외부 서비스(서버)** — HMAC 서명 REST API(`/api/v1`)로 구독 생성·취소·결제 조회 등을 호출.
- **운영자/담당자(사람)** — htmx 기반 Admin 화면(`/admin`)으로 서비스·요금제·구독·정산을 관리.

핵심 규칙: **(서비스, 외부 사용자) 당 열린 구독 1개**, 만료일 자동연장, 첫 구독 할인/무료,
체험(TRIAL) 후 자동 전환, 결제 실패 시 재시도→정지→만료의 생명주기.
단건(일반) 결제 취소: 서비스별 취소 허용 여부(`cancellation_enabled`)와 수수료율(`cancellation_fee_percent`)로 제어.
환불액 = 금액 − (금액 × 수수료% // 100). 부분취소 또는 전액취소로 토스 결제 취소 API 호출.

---

## 2. 기술 스택

| 영역 | 선택 |
|---|---|
| 웹 프레임워크 | FastAPI (ASGI, async 전반) |
| DB | PostgreSQL + SQLAlchemy 2 (async, asyncpg) + Alembic 마이그레이션 |
| 캐시/락 | Redis (세션, 분산 락, 레이트리밋, nonce) |
| Admin UI | Jinja2 서버 렌더 + htmx (부분 갱신), SVG 차트(라이브러리 없음) |
| 결제 | TossPayments 빌링 API (`app/toss`) |
| 스케줄러 | APScheduler (인프로세스, 갱신 배치) |
| 메일 | SMTP(Gmail) 또는 콘솔 출력 |
| 패키지/런타임 | uv, Python 3.13 |

---

## 3. 아키텍처 계층

요청은 위에서 아래로 흐르고, **아래 계층은 위 계층을 모른다**.

```
┌─────────────────────────────────────────────────────────────┐
│ 진입점  app/main.py — create_app(): lifespan에서 자원 구성    │
│         (cipher·engine·redis·toss·email·scheduler)            │
└───────────────┬──────────────────────────┬──────────────────┘
                │                          │
   ┌────────────▼──────────┐   ┌───────────▼───────────────┐
   │ 외부 API  /api/v1      │   │ Admin  /admin (htmx)        │
   │ HMAC 서명 + IP 화이트  │   │ 세션 쿠키 + 역할(RBAC)      │
   │ app/api/v1/*           │   │ app/admin/routes/*          │
   └────────────┬──────────┘   └───────────┬───────────────┘
                │                          │
                └───────────┬──────────────┘
                            ▼
         ┌──────────────────────────────────────────┐
         │ 서비스(도메인) 계층  app/services/*        │
         │ 비즈니스 규칙·트랜잭션 경계·감사로그 기록   │
         │ subscriptions/renewals/registry/plans/payments/...│
         └───────────────┬───────────────┬───────────┘
                         │               │
              ┌──────────▼──┐     ┌──────▼─────────┐
              │ 도메인 모델  │     │ 외부 연동       │
              │ app/models/* │     │ app/toss/*      │
              │ (SQLAlchemy) │     │ (빌링키/승인)   │
              └──────────────┘     └────────────────┘

   인프라(횡단)  app/core/*  : 설정·암호화·보안(HMAC/비번)·DB엔진·시계·에러
   배치          app/scheduler/runner.py → app/services/renewals.process_due
```

핵심 원칙: **라우트는 얇게**(파싱·인증·렌더), **서비스 계층이 두껍게**(규칙·DB 커밋·감사로그).
모델은 데이터/제약만, 토스 클라이언트는 외부 호출만 담당한다.

---

## 4. 디렉터리 맵

| 경로 | 책임 |
|---|---|
| `app/main.py` | 앱 팩토리 `create_app`, lifespan 자원 관리, 라우터/정적/에러 핸들러 등록 |
| `app/cli.py` | 운영 CLI (예: 최초 관리자 생성) |
| **`app/core/`** | 횡단 인프라 |
| `core/config.py` | `Settings`(pydantic-settings, `.env`) — DB/Redis/토스/레이트리밋/스케줄러 등 |
| `core/crypto.py` | `AesGcmCipher` — 빌링키·API키 암호화(at-rest) |
| `core/security.py` | HMAC 서명/검증, 비밀번호 해시(argon2), 키·토큰 생성, 상수시간 비교 |
| `core/db.py` | async 엔진/세션 팩토리 |
| `core/clock.py` | `utcnow()`(저장은 UTC), `kst_format()`(표시만 KST) |
| `core/errors.py` | 도메인 예외(`DomainError` 계열: 인증/권한/검증/충돌/결제실패/레이트리밋) |
| **`app/models/`** | SQLAlchemy 엔티티 + enum(상태머신) |
| `models/enums.py` | 모든 상태값과 파생 집합(`ACCESS_ALLOWED_STATUSES` 등) |
| **`app/api/`** | 외부 서비스용 REST |
| `api/deps.py` | `authenticate_service`(HMAC+IP+nonce+레이트리밋), 자원 주입 의존성 |
| `api/v1/*.py` | subscriptions / payments / plans / webhooks 엔드포인트 |
| `api/errors.py` | API 예외→JSON 응답 매핑 |
| `app/schemas/api.py` | 요청/응답 Pydantic 스키마 |
| **`app/admin/`** | 운영 콘솔(htmx) |
| `admin/__init__.py` | Jinja 환경(`kst` 필터), `render`/`render_list`, 라우터 묶음 |
| `admin/deps.py` | 세션 인증 `require_admin`/`require_any`/`require_role`, CSRF, 예외→리다이렉트 |
| `admin/pagination.py` | `PageParams`·`paginate`·`date_range` 공용 목록 헬퍼 |
| `admin/export.py` | `xlsx_response` — 엑셀(.xlsx) 다운로드 공용 유틸(수식 주입 방어 포함) |
| `admin/filters.py` | `plan_name_options` — 요금제 드롭다운 옵션 빌더(스코프·서비스 필터 적용) |
| `admin/routes/*.py` | auth/dashboard/services/plans/subscriptions/users/audit/settlement/**settings** |
| `admin/templates/*` | Jinja 템플릿(+`_charts.html` SVG, `_list.html` 매크로) |
| `admin/templates/payments/detail.html` | 결제 상세 화면(구독 결제·단건 결제 공용) |
| **`app/services/`** | 도메인 로직 |
| `services/registry.py` | 서비스 등록·키발급·담당자 배정·상태/삭제 |
| `services/plans.py` | 요금제 생성/수정/보관/삭제 |
| `services/subscriptions.py` | 구독 생성·취소·재개·카드변경·수동결제(외부 API 경유 동작) |
| `services/payments.py` | 단건(일반) 결제 `create_one_off_payment` — 구독 없이 즉시 빌링키 결제; `cancel_one_off_payment` — 서비스 정책 기반 취소(수수료 공제, 부분/전액취소) |
| `services/renewals.py` | **갱신 배치** — 체험만료/정기결제/재시도/정지/만료/PENDING 정산 |
| `services/billing_math.py` | 첫결제액·정기결제액·기간계산(할인/주기 규칙) |
| `services/accounts.py` | 관리자 계정 CRUD + 서비스 다대다 배정 |
| `services/auth.py` | 로그인/세션/비밀번호 설정·잠금 |
| `services/audit.py` | `record_audit` (감사로그 1행 추가, 커밋은 호출자) |
| `services/dashboard.py` | 대시보드 집계(카드·차트·레일·서비스별 누적) |
| `services/settlement.py` | 정산 집계(승인일 기준 DONE 합산) |
| `services/webhooks.py` | 토스 웹훅 처리(멱등) |
| `services/app_settings.py` | `GlobalSettings` 헬퍼 — 재시도/어드민IP/킬스위치 단일행 접근 |
| **`app/toss/`** | 토스 연동 |
| `toss/client.py` | `TossClient` 프로토콜 + `HttpTossClient`(issue_billing_key/charge/조회/삭제) |
| `toss/fake.py` | 테스트용 가짜 클라이언트 |
| `toss/errors.py` | `TossError`/`TossTimeoutError`(결과 불명 구분) |
| **`app/scheduler/`** | `runner.py` — APScheduler 등록 + 전역 락 배치 실행 |
| **`app/notifications/`** | `email.py` — `EmailSender` 인터페이스(Console/Gmail) |
| `alembic/` | DB 마이그레이션(운영 DB용; 테스트는 `create_all`) |
| `tests/` | unit / integration / security / e2e |

---

## 5. 두 개의 진입 평면과 인증

서버는 **성격이 다른 두 입구**를 가지며 인증 방식이 완전히 다르다.

### (A) 외부 서비스 API — `/api/v1`
- **무상태 + HMAC 서명**. 매 요청에 헤더 `X-Service-Key`·`X-Timestamp`·`X-Nonce`·`X-Signature`.
- 서명 = `HMAC_SHA256(secret, "METHOD\nPATH\nTS\nNONCE\nSHA256(body)")` (hex).
- `app/api/deps.authenticate_service`가 검증: **킬스위치 게이트**(`ensure_server_enabled`) →
  서비스키 해시 조회 → **IP 화이트리스트** →
  타임스탬프 허용오차(±5분) → **nonce 재사용 차단(Redis)** → 상수시간 서명 비교 →
  **레이트리밋(Redis)**. 서비스 비활성(INACTIVE)이면 거부.
- **결제서버 킬스위치** (요청 013): `GlobalSettings.server_disabled=True`이면 모든 외부 API 요청에
  503 `SERVER_DISABLED` 반환. 어드민은 영향 없음. 문서 13 참조.
- 비밀(서비스 API키·HMAC secret)은 발급 시 1회 노출, DB에는 **암호문/해시**로 저장.
- 엔드포인트: 구독 생성/조회/취소/재개/카드변경/수동결제, 요금제 조회, 결제 조회,
  **단건 결제 생성(`POST /api/v1/payments`)**, **웹훅 수신**.
  - `GET /api/v1/payments/{external_user_id}` — 사용자의 구독 결제 목록 조회.
  - `POST /api/v1/payments` — 구독 없이 즉시 결제하는 단건(일반) 결제 생성.
  - `POST /api/v1/payments/{order_id}/cancel` — 단건 결제 취소(환불). `payment_rate_limit` 인증. 서비스 취소 정책 적용.

### (B) Admin 콘솔 — `/admin`
- **세션 쿠키 + 서버측 세션(Redis) + 역할 기반 접근(RBAC)**.
- 역할: `SYSTEM_ADMIN`(전체) / `SERVICE_MANAGER`(담당 서비스만 — **스코프**).
- `require_admin`=시스템관리자 전용, `require_any`=둘 다(스코프 적용), `require_role(...)` 세분.
- 폼 제출은 **CSRF 토큰** 검증. 비밀번호는 argon2 해시, 로그인 실패 누적 시 계정 LOCKED.
- **어드민 접속 IP 제한** (`require_user`, 요청 013): `GlobalSettings.admin_allowed_ips`에 IP가 등록되면
  목록 외 IP는 모든 Admin 요청에서 403 차단(문서 13).
- **전체설정 화면** (`SYSTEM_ADMIN` 전용, 요청 013): `/admin/settings` — 재시도 설정·어드민IP·킬스위치를
  Admin 화면에서 실시간 변경. 자세한 내용은 문서 13 참조.
- 화면 갱신은 htmx 부분 렌더(`render_list`가 `HX-Request`면 `_table.html`만 응답).
- **결제 관련 Admin 화면** (`admin/routes/payments.py`):
  - `GET /admin/payments` — 결제 이력 목록. 필터: 서비스·요금제명·종류(`kind`)·상태·기간.
  - `GET /admin/payments/{id}` — 결제 상세 (구독 결제이면 연결 구독 표시, 단건이면 NULL). 단건 DONE 결제는 취소 버튼(정책 허용 시) 또는 "취소 불가" 배지 표시.
  - `POST /admin/payments/{id}/cancel` — Admin에서 단건 결제 취소(CSRF + 스코프 검증, 감사 actor=USER).
  - `GET /admin/payments/export.xlsx` — 현재 필터 적용 전체 엑셀 다운로드.
- **엑셀 다운로드**: 구독/서비스/요금제/결제/사용자/감사로그/정산 모든 리스트에서
  `export.xlsx` 경로로 제공. 공용 유틸 `admin/export.py`의 `xlsx_response` 사용.
- **대시보드 v2** (`services/dashboard.py`): 매출 섹션(총매출·구독매출·일반매출·환불),
  구독 상태 도넛 차트, 월별 구독수/일반매출 시계열, 서비스별 매출·구독 집계(SYSTEM_ADMIN 전용).

스코프 규칙은 일관됨: `ctx.service_ids`가 `None`이면 시스템관리자(전체),
리스트면 그 서비스들만. 모든 목록/집계/정산이 이 값을 따른다.

---

## 6. 데이터 모델과 상태 머신

### 핵심 엔티티 관계
```
Service 1──N Plan
Service 1──N Subscription 1──N Payment(kind=SUBSCRIPTION)
Service 1──N Payment(kind=ONE_OFF)          ← 단건 결제(구독 없음)
Service.manager_email = 대표 담당자 이메일(알림 수신처)
User ─ Service : 다대다  (User.service_id = 주 서비스, user_services = 추가 배정)
GlobalSettings : id=1 단일 행 — 재시도·어드민IP·킬스위치 런타임 설정
WebhookEvent : 토스 웹훅 멱등 처리 기록
AuditLog : 모든 중요한 행위 기록 (actor_user_id 또는 actor_service_id)
PasswordSetupToken : 관리자 비밀번호 설정 링크
```

**Payment 모델 주요 필드** (`app/models/payment.py`):

| 필드 | 타입 | 설명 |
|---|---|---|
| `kind` | String(20), `PaymentKind` | `SUBSCRIPTION`(구독 결제) / `ONE_OFF`(단건·일반 결제), 기본값 SUBSCRIPTION |
| `service_id` | FK → services, NOT NULL | 결제가 속한 서비스(단건 결제는 subscription_id 없이 이 값만 있음) |
| `external_user_id` | String(255), nullable | 외부 서비스 사용자 식별자(단건 결제 시 전달) |
| `subscription_id` | FK → subscriptions, nullable | 구독 결제이면 연결, 단건 결제이면 NULL |
| `canceled_amount` | BigInteger, nullable | 실제 환불액(금액−수수료). 취소 성공 시 기록 |
| `cancel_fee` | BigInteger, nullable | 차감 수수료(금액 × 수수료% // 100) |
| `canceled_at` | DateTime(timezone=True), nullable | 취소 완료 시각(UTC) |

마이그레이션 `c3d4e5f6a7b8`: `kind`/`service_id`/`external_user_id` 컬럼 추가,
`subscription_id`를 nullable로 변경, 기존 행은 연결된 구독에서 `service_id`/`external_user_id` 백필.

마이그레이션 `d4e5f6a7b8c9`: `services`에 `cancellation_enabled`/`cancellation_fee_percent` 추가,
`payments`에 `canceled_amount`/`cancel_fee`/`canceled_at` 추가.

마이그레이션 `e5f6a7b8c9d0`: `global_settings` 테이블 생성(id=1 단일행, 재시도·어드민IP·킬스위치).

마이그레이션 `f6a7b8c9d0e1`: `plans`에 `auto_renew`(Boolean, default true), `extra_info`(JSONB, default `{}`) 추가.

### 상태 머신 (`app/models/enums.py`)
- **Subscription**: `TRIAL → ACTIVE → PAST_DUE → SUSPENDED → EXPIRED`, 별도 `CANCELED`(만료까지 유지).
  - 접근 허용(`ACCESS_ALLOWED_STATUSES`): TRIAL·ACTIVE·PAST_DUE·CANCELED (SUSPENDED·EXPIRED 차단)
  - "열린 구독"(1개 제약, `OPEN_SUBSCRIPTION_STATUSES`): EXPIRED만 제외
- **Payment**: `PENDING → DONE | FAILED | CANCELED`, 유형 `FIRST | RENEWAL | RETRY | ONE_OFF`,
  종류 `SUBSCRIPTION`(구독 결제) | `ONE_OFF`(단건 결제) — `PaymentKind` enum
- **Plan**: `ACTIVE | ARCHIVED` · 주기 `YEAR|MONTH|WEEK|DAY` · 첫결제 `NONE|FREE|DISCOUNT_AMOUNT|DISCOUNT_PERCENT`
  - 요청 013 추가 컬럼: `auto_renew`(bool, 기본 True — False=(체험 후) 첫 주기 후 만료, **trial과 공존 가능**), `extra_info`(JSONB, 서비스 측 key/value 설명, PlanResponse 노출)
- **GlobalSettings**: id=1 단일 행 — `retry_limit`(기본 4), `retry_interval_hours`(기본 12), `suspended_grace_days`(기본 30), `admin_allowed_ips`(JSONB list, 빈=제한없음), `server_disabled`(bool), `disabled_reason`, `disabled_at`, `disabled_by`
- **User**: `PENDING|ACTIVE|LOCKED|DISABLED|DELETED`, 역할 `SYSTEM_ADMIN|SERVICE_MANAGER`
- **Service**: `ACTIVE|INACTIVE`, 취소 정책 컬럼 `cancellation_enabled`(bool) · `cancellation_fee_percent`(int 0~100)
  **Webhook**: `RECEIVED|PROCESSED|IGNORED|FAILED`

상태 enum이 곧 비즈니스 규칙의 중심 — 이후 프로세스 문서는 대부분 "이 상태 전이를
어떤 코드가 일으키는가"로 환원된다.

---

## 7. 요청 처리 흐름(공통 패턴)

1. **라우트**: 인증/권한 → 폼·쿼리·바디 파싱 → 서비스 계층 호출 → 응답(JSON 또는 렌더).
2. **서비스 계층**: 검증 → 모델 변경/조회 → 필요 시 토스 호출 → **감사로그 기록** →
   `db.commit()`(트랜잭션 경계는 서비스가 묶음).
3. **모델**: 영속 + 제약(유니크/FK).  **토스**: 외부 결제 호출만(여기서 DB 안 만짐).

돈이 오가는 경로(구독 생성, 갱신)는 특수 패턴을 따른다:
- **결제 전에 PENDING 행을 먼저 커밋**(내구성 선점) → 토스 호출 → 결과로 DONE/FAILED 확정.
- 토스 **타임아웃은 "실패"가 아니라 "결과 불명"**으로 다룸(`TossTimeoutError`) — 같은
  결정적 `order_id`/멱등키로 다음 배치가 재시도하거나, **PENDING 정산 스윕**이 확정.
- 결정적 주문번호(`_renewal_order_id`)로 크래시 후 재실행에도 이중결제를 막음.

---

## 8. 횡단 관심사(전 기능에 반복 등장)

- **암호화/비밀**: 빌링키·API키는 `AesGcmCipher`로 at-rest 암호화, 키 해시로 조회.
- **감사로그**: 거의 모든 상태 변경이 `record_audit`로 남음(행위자=USER/SERVICE/SYSTEM).
  Admin 감사 화면에서 한글 라벨·필터·검색·엑셀·기간삭제 지원.
- **엑셀 공용 유틸**: `admin/export.py`의 `xlsx_response`가 모든 리스트 화면의 `.xlsx`
  다운로드를 처리. write-only 워크북, 수식 주입(`=`, `+`, `-`, `@` 접두어) 방어 포함.
- **멱등성·동시성**: 구독별 Redis 락(`lock:renew:{id}`), 스케줄러 전역 락, 토스 멱등키,
  DB 부분 유니크 인덱스(열린 구독 1개), nonce 재사용 차단.
- **스코프(권한)**: `ctx.service_ids`로 SERVICE_MANAGER의 데이터 가시범위 제한.
- **시간**: 저장·연산·필터는 전부 **UTC**, **화면 표시만 KST**(`kst` Jinja 필터). 대시보드 상단 실시간 시계.
- **목록 공통**: `PageParams`(검색 q·필터·정렬·페이지), `paginate`, `date_range`(YYYY-MM-DD 반개구간).
- **에러 처리**: `DomainError` 계열 → API는 JSON, Admin은 flash 토스트/리다이렉트로 변환.

---

## 9. 외부 의존성과 구동

- **PostgreSQL**(기본 5433), **Redis**(기본 6380) — `docker compose up -d`.
- **TossPayments API** — `toss_secret_key`. 테스트는 `FakeTossClient`로 대체.
- **SMTP(Gmail)** — `gmail_id/pw` 있으면 실제 발송, 없으면 콘솔.
- 구동: `alembic upgrade head` → `cli create-admin` → `uvicorn app.main:app`.
- 운영(`environment=prod`)에서는 `/docs`·OpenAPI 스키마 비공개.

---

## 10. 다음 단계: 기능별 프로세스 문서(예정)

이 개요 위에서 아래 기능들을 "프로세스 정의 → 관여 코드 → 처리 흐름" 순으로 분석한다.

| # | 기능(프로세스) | 주 관여 코드 |
|---|---|---|
| 01 | 서비스 등록·키 발급·담당자 배정 | `services/registry.py`, `admin/routes/services.py` |
| 02 | 관리자 계정·로그인·세션·권한 | `services/accounts.py`·`auth.py`, `admin/deps.py` |
| 03 | 요금제 생성·금액 계산(할인/주기) | `services/plans.py`·`billing_math.py` |
| 04 | 구독 생성(첫결제/체험/빌링키) | `api/v1/subscriptions.py`, `services/subscriptions.py` |
| 05 | 자동 갱신·재시도·정지·만료 배치 | `scheduler/runner.py`, `services/renewals.py` |
| 06 | 구독 취소·재개·카드변경·수동결제 | `services/subscriptions.py` |
| 07 | 결제 결과 정합성(PENDING 정산·웹훅) | `services/renewals.py`·`webhooks.py` |
| 08 | 외부 API 인증(HMAC/IP/nonce/레이트리밋) | `api/deps.py`, `core/security.py` |
| 09 | 대시보드·정산 집계 | `services/dashboard.py`·`settlement.py` |
| 10 | 감사로그(기록·조회·엑셀·삭제) | `services/audit.py`, `admin/routes/audit.py` |
| 11 | 단건(일반) 결제 + 결제 취소(수수료·부분환불) | `api/v1/payments.py`, `services/payments.py` |
| 12 | 리스트 엑셀 다운로드(전 화면 공용) | `admin/export.py`, `admin/filters.py`, 각 routes |
| 13 | 전역설정(재시도·어드민IP·킬스위치) | `models/global_settings.py`, `services/app_settings.py`, `admin/routes/settings.py` |
