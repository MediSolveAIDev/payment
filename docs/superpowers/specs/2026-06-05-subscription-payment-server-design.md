# 구독/결제 API 서버 설계 문서

- 작성일: 2026-06-05
- 상태: 승인됨
- 근거: `CLAUDE.md` 요구사항 + 브레인스토밍 질의응답

## 1. 개요

사내 다양한 서비스가 공통으로 사용하는 구독/결제 API 서버.
외부(사내) 서비스가 HMAC 인증 API로 구독을 생성·관리하고, 서버는 토스페이먼츠
빌링키 기반 자동결제로 구독을 자동 연장한다. 관리 화면(htmx)에서 서비스 등록,
요금제 관리, 구독/결제 현황을 다룬다.

### 확정된 핵심 결정

| 결정 사항 | 선택 |
|---|---|
| 빌링키 발급 플로우 | 외부 서비스가 토스 SDK 결제창 호출 → `authKey`를 우리 API로 전달 → 서버가 빌링키 발급·저장·첫 결제. 본 서버는 순수 API 서버 |
| Admin 인증 | 이메일+비밀번호(argon2id), role 구분(SYSTEM_ADMIN/SERVICE_MANAGER), Redis 서버사이드 세션 |
| 외부 API 보안 | API 키 + IP 화이트리스트 + HMAC-SHA256 요청 서명(타임스탬프+nonce 재전송 방어) |
| 갱신 실패 정책 | 1일 간격 최대 3회 재시도, 기간 중 PAST_DUE 유지, 최종 실패 시 EXPIRED + 담당자 이메일 알림 |
| 이메일 발송 | EmailSender 인터페이스 추상화, 개발/테스트는 콘솔 출력, SMTP 구현체는 환경변수 교체 |
| 실행 구조 | 단일 프로세스: FastAPI + APScheduler(lifespan) + Redis 분산 락. 빌링 로직은 서비스 레이어 격리(추후 워커 분리 용이) |

## 2. 기술 스택

| 영역 | 선택 | 비고 |
|---|---|---|
| 웹 프레임워크 | FastAPI (Python 3.13, uv) | 전체 async |
| DB | PostgreSQL + SQLAlchemy 2.0 async + Alembic | asyncpg |
| 캐시/세션/락 | Redis (redis-py async) | 세션, nonce 재전송 방지, 분산 락, rate limit |
| HTTP 클라이언트 | httpx (async) | 토스 API. 자동결제 승인 타임아웃 60초(명세) |
| Admin UI | Jinja2 + htmx | Centurion Suite 디자인 토큰(`docs/design/centurion-suite-handoff/`) |
| 비밀번호 해시 | argon2id | |
| 민감정보 암호화 | AES-256-GCM, 키는 환경변수 | 빌링키, HMAC secret 저장 시 |
| 스케줄러 | APScheduler + Redis 분산 락 | |
| 테스트 | pytest + pytest-asyncio + respx + 실제 PG/Redis | |
| 로컬 인프라 | docker-compose (postgres, redis) | |

## 3. 디렉토리 구조

```
app/
  main.py              # 앱 팩토리, lifespan(스케줄러 시작/종료)
  core/                # config, db, redis, 암호화, 보안 유틸
  models/              # SQLAlchemy 모델
  schemas/             # Pydantic 요청/응답
  api/v1/              # 외부 서비스용 API (HMAC 인증)
  admin/               # htmx admin 라우트 + templates/
  services/            # 비즈니스 로직 (구독, 결제, 요금제, 서비스, 인증)
  toss/                # 토스 클라이언트 (인터페이스 + 실구현 + fake)
  scheduler/           # 자동연장/재시도 잡
  notifications/       # EmailSender 인터페이스 + 콘솔/SMTP 구현
alembic/
tests/                 # unit / integration / security / e2e
docker-compose.yml
```

원칙: 비즈니스 로직은 전부 `services/`. API 라우트·admin 라우트·스케줄러는 같은
서비스 함수를 호출하는 얇은 어댑터. 토스 클라이언트는 인터페이스로 추상화해
테스트에서 fake로 교체한다.

## 4. 데이터 모델

```
services            # 구독/결제를 이용하는 사내 서비스
  id (uuid PK), name (unique), allowed_ips (JSON 배열), manager_email,
  api_key_hash (SHA-256, 키 자체는 발급 시 1회만 노출),
  hmac_secret_encrypted (AES-GCM),
  status (ACTIVE/INACTIVE), created_at, updated_at

users               # admin 화면 사용자
  id, email (unique), password_hash (argon2id),
  role (SYSTEM_ADMIN/SERVICE_MANAGER),
  service_id (FK, SYSTEM_ADMIN은 NULL), status (PENDING/ACTIVE/LOCKED),
  failed_login_count, locked_until, created_at

password_setup_tokens  # 초기 비밀번호 설정/재설정 토큰
  id, user_id (FK), token_hash, expires_at, used_at

plans               # 구독 요금제 (서비스별)
  id, service_id (FK), name, price (BigInteger, KRW 정수), currency (KRW 기본),
  billing_cycle (YEAR/MONTH/WEEK/DAY), cycle_days (DAY일 때 일수, 그 외 NULL),
  first_payment_type (NONE/FREE/DISCOUNT_AMOUNT/DISCOUNT_PERCENT),
  first_payment_value, status (ACTIVE/ARCHIVED), created_at, updated_at

subscriptions
  id, service_id (FK), plan_id (FK), external_user_id,
  customer_key (외부 서비스가 SDK 호출 시 생성, 토스용),
  billing_key_encrypted (AES-GCM),
  billing_key_hash (SHA-256 — BILLING_DELETED 웹훅의 billingKey 매칭용 조회 인덱스),
  card_info (마스킹 카드번호/발급사 표시용 JSON),
  status (ACTIVE/PAST_DUE/CANCELED/EXPIRED),
  current_period_start, current_period_end, next_billing_at,
  retry_count, created_at, updated_at
  UNIQUE (service_id, external_user_id) WHERE status IN (ACTIVE, PAST_DUE, CANCELED)

payments            # 결제 시도 이력 (성공/실패 모두 기록)
  id, subscription_id (FK), order_id (unique, 서버 생성),
  toss_payment_key, amount, payment_type (FIRST/RENEWAL/RETRY),
  status (PENDING/DONE/FAILED/CANCELED),
  failure_code, failure_message, idempotency_key,
  requested_at, approved_at, raw_response (JSONB)

webhook_events      # 토스 웹훅 수신 이력
  id, transmission_id (unique — 중복 수신 방지), event_type, payload (JSONB),
  status (RECEIVED/PROCESSED/IGNORED/FAILED), received_at, processed_at

audit_logs          # 관리 행위 감사 로그
  id, actor_user_id, actor_type (USER/SERVICE/SYSTEM), action,
  target_type, target_id, detail (JSONB), ip_address, created_at
```

주요 결정:

- 빌링키·HMAC secret은 AES-256-GCM 암호화 저장, API 키는 해시만 저장.
- "서비스+사용자 당 1개 구독" 규칙은 partial unique index로 DB가 강제.
  EXPIRED만 제외해 재구독을 허용한다 (동시 요청 경쟁 조건 차단).
- payments에 토스 원본 응답(JSONB) 보존 — 분쟁/정산 대비.
- 요금제/서비스 삭제 방지: ACTIVE/PAST_DUE/CANCELED 구독 존재 시 거부
  (서비스 레이어 검증 + FK RESTRICT 이중 방어).

## 5. 외부 서비스 API (`/api/v1`)

```
POST /api/v1/subscriptions                  # 구독 생성 (authKey 전달)
  body: { external_user_id, plan_id, auth_key, customer_key }
  처리: 토스 빌링키 발급 → 암호화 저장 → 첫 결제(FREE면 생략, DISCOUNT면 할인가)
        → 구독 ACTIVE
  * customer_key는 외부 서비스가 토스 SDK(requestBillingAuth) 호출 시 생성한
    값(UUID 권장). SDK 리다이렉트로 authKey와 함께 돌아오므로 둘 다 전달받아야
    한다. 서버는 토스 형식 규칙(2~300자, 허용 문자)을 검증 후 저장.

GET  /api/v1/subscriptions/{external_user_id}            # 구독 상태 조회
POST /api/v1/subscriptions/{external_user_id}/cancel     # 취소(만료일까지 유지)
POST /api/v1/subscriptions/{external_user_id}/resume     # 취소 철회(만료 전)
POST /api/v1/subscriptions/{external_user_id}/change-card # 새 authKey로 카드 교체
  body: { auth_key, customer_key }  # 새 SDK 호출로 받은 쌍
GET  /api/v1/plans                          # 해당 서비스의 ACTIVE 요금제 목록
GET  /api/v1/payments/{external_user_id}    # 결제 이력 조회
POST /api/v1/webhooks/toss                  # 토스 웹훅 수신
```

### 토스페이먼츠 연동 (docs/toss 명세 기준)

| 작업 | 엔드포인트 |
|---|---|
| 빌링키 발급 | `POST /v1/billing/authorizations/issue` (authKey + customerKey) |
| 자동결제 승인 | `POST /v1/billing/{billingKey}` (amount, customerKey, orderId, orderName) |
| 빌링키 삭제 | `DELETE /v1/billing/{billingKey}` |
| 결제 취소 | `POST /v1/payments/{paymentKey}/cancel` |
| 인증 | `Authorization: Basic base64(secret_key + ':')` |
| 멱등성 | POST 요청에 `Idempotency-Key` 헤더 (15일 유효) |

- `customerKey`는 외부 서비스가 SDK 호출 시 생성(UUID 권장, 유추 가능 값 금지 —
  토스 명세). 서버는 형식 검증 후 구독에 저장.
- `orderId`는 `[A-Za-z0-9-_]` 6~64자, 서버가 생성·저장.
- 자동결제 승인은 웹훅이 오지 않으므로(토스 정책) 응답을 직접 처리.

## 6. 보안 설계

### 외부 API 인증 (3중 방어)

```
X-Service-Key: svc_xxxx          # 서비스 식별 (SHA-256 해시 대조, 상수시간 비교)
X-Timestamp: 1717570800          # Unix epoch
X-Nonce: <uuid>
X-Signature: HMAC-SHA256(secret,
    method + "\n" + path + "\n" + timestamp + "\n" + nonce + "\n" + sha256(body))
```

1. API 키 해시 대조 (상수시간 비교)
2. IP 화이트리스트 대조 (`X-Forwarded-For` 처리는 신뢰 프록시 설정으로 제어)
3. HMAC 서명 검증: 타임스탬프 ±5분 허용, nonce는 Redis 10분 TTL 저장으로
   재전송 공격 차단

### 추가 방어

- Rate limiting: Redis 기반 서비스별 제한, 결제 엔드포인트는 더 엄격.
- 결제 금액은 외부 입력이 아닌 서버의 plan에서만 계산 — 금액 조작 원천 차단.
- 토스 호출에 멱등키 사용 — 타임아웃 후 재시도해도 이중 결제 방지.
- 토스 웹훅: 토스 인바운드 IP 검증 + `transmission_id` 중복 차단 +
  페이로드를 신뢰하지 않고 토스 API 재조회로 상태 확정.
  - `transmission_id` 헤더가 없으면 거부(422) — 합성 ID로 dedup을 우회한
    위조 재전송의 무한 적재를 막는다. (토스는 항상 헤더를 보냄)
  - `BILLING_DELETED`/`PAYMENT_STATUS_CHANGED`는 토스 웹훅 서명이 제공되지
    않는 이벤트라 IP 허용목록 + 재조회가 인증을 대신한다(서명은 지급대행
    이벤트에만 포함 — 토스 명세).
  - 처리 중 일시 오류(토스 재조회 실패 등)는 이벤트 기록을 롤백하고 500을
    반환해 토스 재전송을 유도한다. 영구 오류만 FAILED로 기록 후 200.
  - `trust_proxy=True`는 인바운드 XFF를 덮어쓰는 신뢰 프록시 뒤에서만 켤 것
    (append형 프록시면 IP 검증 우회 가능).
- 에러 응답에 내부 정보 비노출. 모든 관리/결제 행위 audit_logs 기록.

### Admin 보안

- Redis 서버사이드 세션, `HttpOnly·Secure·SameSite=Lax` 쿠키, 유휴 30분 만료.
- 모든 htmx 폼에 CSRF 토큰. 로그인 5회 실패 시 15분 잠금. argon2id.
- 역할 분리: SYSTEM_ADMIN(서비스 등록/키 발급/전체 조회),
  SERVICE_MANAGER(자기 서비스의 요금제/구독/결제만).
- API 키·HMAC secret은 발급 직후 1회만 표시, 재발급 시 기존 키 즉시 무효화.

## 7. 구독 라이프사이클

```
(생성) → ACTIVE ──결제실패──→ PAST_DUE ──3회 재시도 실패──→ EXPIRED
            │                    │ 재시도 성공: → ACTIVE (기간 갱신)
            │                    └─취소 요청──→ CANCELED (재시도 중단)
            ├─취소 요청─→ CANCELED ──만료일──→ EXPIRED
            │               └─resume(만료 전)─→ ACTIVE
            └─만료일 도래─→ 자동연장 결제 → 성공: 기간 갱신 / 실패: PAST_DUE
```

- 기간 계산: MONTH/YEAR는 `dateutil.relativedelta`(1/31→2/28 등 월말 처리),
  WEEK는 7일, DAY는 `cycle_days`일.
- 첫 구독 혜택: 해당 (service, external_user_id) 조합으로 **혜택을 소진한
  과거 구독이 없을 때만** 적용. 혜택 소진 구독 = ① 결제 성공(DONE) 이력이 있는
  구독, 또는 ② 결제 없이 활성화된 구독(FREE/100% 할인).
  첫 결제가 실패해 즉시 만료된 구독(FAILED 결제만 보유)은 소진으로 보지 않아
  재시도 시 혜택이 유지되고, 무료 첫구독은 만료 후 반복 적용되지 않는다.
  FREE면 첫 기간 무결제(빌링키는 발급·저장, 다음 주기부터 정가 결제),
  DISCOUNT면 할인가 결제. 재구독은 항상 정가.
- 취소: status를 CANCELED로 전환하되 `current_period_end`까지 혜택 유지,
  만료일에 EXPIRED 전환 + 빌링키 삭제. 만료 전에는 resume으로 ACTIVE 복귀.
  PAST_DUE 중 취소하면 재시도를 중단하고 만료일에 EXPIRED 처리.
- 스케줄러: 5분 간격으로 `next_billing_at <= now`인 구독 스캔.
  구독별 Redis 락 + 토스 멱등키로 중복 결제 방지.
  재시도는 실패 시점 +1일 간격, 최대 3회. 최종 실패 시 EXPIRED + 이메일 알림.

## 8. Admin 화면 (htmx)

| 화면 | SYSTEM_ADMIN | SERVICE_MANAGER |
|---|:---:|:---:|
| 로그인 / 비밀번호 설정 | ✓ | ✓ |
| 대시보드 (구독/결제 현황) | 전체 | 자기 서비스 |
| 서비스 등록·키 발급·IP 관리 | ✓ | — |
| 담당자 계정 관리 | ✓ | — |
| 요금제 CRUD | 조회 | ✓ |
| 구독 목록/상세/강제취소 | ✓ | ✓ |
| 결제 이력/실패 내역 | ✓ | ✓ |
| 감사 로그 | ✓ | — |

서비스 등록 시 담당자 계정(PENDING) 자동 생성 → 비밀번호 설정 토큰을 이메일
발송(개발: 콘솔 출력) → 설정 완료 시 ACTIVE.

## 9. 에러 처리

- 토스 API 에러: 에러 코드별 분류 — 카드 거절류(잔액부족/한도초과 등)는
  결제 실패로 기록하고 재시도 정책 적용, 시스템 오류(5xx/타임아웃)는
  멱등키 재시도 후 실패 처리. 타임아웃은 결제 성공 여부가 불명이므로
  `orderId` 재조회로 승인 여부를 확정한 뒤 처리한다.
- 첫 결제(구독 생성 시) **확정 실패**(카드 거절 등): 재시도 없이 구독을 즉시
  EXPIRED 처리하고 결제 FAILED 기록 + 빌링키 삭제 후 호출자에게 402 반환.
  외부 서비스가 사용자에게 재시도(재구독)를 안내한다.
- 첫 결제 **결과 불명**(타임아웃 + 재조회 실패/미확정): 절대 실패로 확정하지
  않는다. 결제 PENDING + 구독 슬롯 점유 유지(재시도 이중결제 차단), 503
  `PAYMENT_UNRESOLVED` 반환. 갱신 배치의 PENDING 정산 스윕이 토스 재조회로
  추후 확정한다(DONE → 결제 완료 / 일정 유예 후 미발견 → FAILED+만료 처리).
- 빌링키 삭제 실패 시 암호문을 제거하지 않고 보존한다(운영자 재시도 가능).
  삭제 성공 시에만 `billing_key_encrypted`를 비운다. `billing_key_hash`는
  웹훅 매칭을 위해 유지.
- 환불 정책 결정: 첫 결제가 환불(CANCELED)되어 DONE 이력이 사라진 사용자는
  만료 후 재구독 시 첫구독 혜택이 다시 적용될 수 있다(환불 = 혜택 미소진).
- 외부 API 에러 응답: `{ "error": { "code": "...", "message": "..." } }`
  일관 포맷, 내부 상세 비노출.
- 스케줄러 잡 실패: 잡 단위 try/except + 로그, 다음 주기에 자연 재시도
  (멱등키로 안전).

## 10. 테스트 전략

1. **유닛**: 기간 계산(월말/윤년 경계), 할인 계산, HMAC 서명/검증,
   암호화 라운드트립, 상태 전이 규칙.
2. **통합** (실제 PostgreSQL+Redis, 토스는 respx 모킹):
   구독 생성→결제→갱신 전체 플로우, 1구독 제약 동시성(동시 2요청 →
   1성공 1실패), 재시도/만료 시나리오, 토스 타임아웃·5xx·카드거절 각각의
   처리, 웹훅 중복 수신.
3. **보안 전용 스위트**: 서명 위조, 타임스탬프 만료, nonce 재사용, IP 위반,
   금액 조작 시도, 권한 우회(SERVICE_MANAGER가 타 서비스 접근), CSRF,
   세션 고정 — 전부 거부 검증.
4. **admin E2E**: 서비스 등록→키 발급→로그인→요금제 생성 핵심 플로우.
5. 전 테스트 통과를 완료 기준으로 하고 커버리지를 측정한다.

## 11. 범위 제외 (YAGNI)

- 카카오페이/네이버페이 등 다른 PG (토스 빌링은 간편결제 미지원 — 토스 명세)
- 부분 환불/일할 계산(proration), 요금제 변경(업/다운그레이드)
- 다중 통화 (KRW 고정, 스키마는 currency 컬럼으로 확장 여지만 둠)
- 웹훅 발송(우리 서버 → 외부 서비스 알림) — 조회 API로 대체, 추후 확장
