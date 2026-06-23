# 01. 시작하기 — 구조·실행·테스트·요청 처리 공통 흐름

> **쉽게 말하면**: 어떤 요청이 와도 항상 같은 길을 지나갑니다 — **문지기(인증·`deps`) → 접수창구(API 라우터) → 실제 처리부서(`services`) → 장부(DB)**. 코드를 처음 읽을 땐 이 '길' 하나만 기억하세요. 모든 기능이 같은 구조라 한 기능만 끝까지 따라가 보면 나머지는 금방 익숙해집니다.

> **대상 독자**: 이 프로젝트를 처음 받아 인수인계만으로 유지보수해야 하는 초보 개발자.
> 여기서 다루지 않는 세부 주제는 문서 하단의 **관련 문서** 링크를 참고한다.

---

## 1. 기술 스택

| 구분 | 선택 | 비고 |
|------|------|------|
| 웹 프레임워크 | FastAPI 0.115+ | 비동기(asyncio) |
| DB | PostgreSQL 16 (asyncpg 드라이버) | 포트 5433 (로컬 docker) |
| ORM / 마이그레이션 | SQLAlchemy 2.x async + Alembic | |
| 캐시 / 세션 / 잠금 | Redis 7 | 포트 6380 (로컬 docker) |
| 결제 모듈 | TossPayments 빌링 API | `app/toss/` |
| 어드민 화면 | Jinja2 + htmx | 서버사이드 렌더 |
| 스케줄러 | APScheduler 3.x (AsyncIOScheduler) | 구독 자동 갱신 |
| 패키지 관리 | uv | `pyproject.toml` |
| 런타임 | Python 3.13+ | |

의존성 전체 목록: `pyproject.toml:6-35`

---

## 2. 디렉터리 구조

```
payment_system/
├── app/
│   ├── main.py             # FastAPI 앱 팩토리 + lifespan (진입점)
│   ├── api/
│   │   ├── deps.py         # 외부 API 공통 의존성(DB·Redis·Toss·인증)
│   │   ├── errors.py       # DomainError → JSON 응답 핸들러
│   │   └── v1/             # 외부 서비스가 호출하는 REST API 라우터
│   │       ├── subscriptions.py
│   │       ├── payments.py
│   │       ├── plans.py
│   │       ├── services.py
│   │       └── webhooks.py
│   ├── admin/
│   │   ├── __init__.py     # Jinja2 템플릿 엔진, render() 헬퍼
│   │   ├── deps.py         # 어드민 세션·CSRF·역할 인증
│   │   └── routes/         # 어드민 화면 라우터 (auth, services, plans, ...)
│   ├── services/           # 비즈니스 로직·트랜잭션 (핵심 계층)
│   │   ├── subscriptions.py
│   │   ├── renewals.py     # 정기 갱신 배치 로직
│   │   ├── payments.py
│   │   ├── plans.py
│   │   ├── registry.py     # 서비스 등록·키 관리
│   │   ├── auth.py         # 어드민 로그인·세션
│   │   ├── billing_math.py # 결제 금액 계산
│   │   ├── audit.py        # 감사 로그 기록
│   │   └── ...
│   ├── models/             # SQLAlchemy ORM 테이블 정의
│   │   ├── base.py         # DeclarativeBase + TimestampMixin
│   │   ├── enums.py        # 모든 StrEnum (상태값)
│   │   ├── service.py / plan.py / subscription.py
│   │   ├── payment.py / user.py / audit_log.py
│   │   └── global_settings.py  # 전역 운영 설정(단일 행)
│   ├── schemas/
│   │   └── api.py          # 외부 API 요청·응답 Pydantic 스키마
│   ├── core/
│   │   ├── config.py       # Settings (pydantic-settings, .env 읽기)
│   │   ├── db.py           # 엔진·세션 팩토리 생성
│   │   ├── clock.py        # utcnow(), kst_format() — UTC/KST 규약
│   │   ├── crypto.py       # AES-256-GCM 암복호화 (빌링키·HMAC secret)
│   │   ├── security.py     # HMAC 서명, 해시, 비밀번호 검증
│   │   └── errors.py       # DomainError 계층 (예외 클래스)
│   ├── toss/
│   │   ├── client.py       # TossClient Protocol + HttpTossClient 구현체
│   │   ├── fake.py         # 테스트용 FakeTossClient
│   │   └── types.py / errors.py
│   ├── scheduler/
│   │   └── runner.py       # APScheduler 시작 + 갱신 배치 잡 등록
│   └── notifications/
│       └── email.py        # EmailSender Protocol + Console/Gmail/Recording 구현체
├── tests/
│   ├── conftest.py         # 세션 픽스처 (engine·Redis·앱·client 등)
│   ├── unit/               # 순수 함수 단위 테스트 (DB 없음)
│   ├── integration/        # 서비스 레이어 + DB/Redis 통합 테스트
│   ├── e2e/                # HTTP 엔드포인트 전체 흐름 테스트
│   ├── security/           # HMAC 인증·어드민 보안 테스트
│   ├── factories.py        # 테스트용 ORM 객체 생성 헬퍼
│   └── helpers.py          # HMAC 서명 헤더 생성, admin 로그인 헬퍼
├── alembic/
│   ├── env.py              # Alembic 비동기 마이그레이션 설정
│   └── versions/           # 마이그레이션 스크립트 (순서 있음)
├── docs/                   # 설계 문서, 토스 API 참고, 개발 매뉴얼
├── docker-compose.yml      # Postgres(5433) + Redis(6380) 로컬 환경
└── pyproject.toml          # 의존성 + pytest 설정
```

---

## 3. 로컬 환경 구축 및 실행

### 3-1. 사전 준비

- Python 3.13+, [uv](https://docs.astral.sh/uv/) 설치 필요
- Docker / Docker Compose (Postgres + Redis 기동)

### 3-2. 의존성 설치

```bash
uv sync
# 테스트·개발 도구 포함 시
uv sync --group dev
```

### 3-3. Postgres · Redis 기동

```bash
docker compose up -d
```

`docker-compose.yml`에 정의된 서비스:
- **postgres**: `localhost:5433` (user=`payment`, db=`payment`)
- **redis**: `localhost:6380`

### 3-4. 환경변수 설정 (.env — dev/prod 분리)

`app/core/config.py`의 `Settings` 클래스가 환경별로 파일을 읽는다. **공통 `.env`를 먼저 읽은 뒤 환경별 `.env.<env>`(예: `.env.dev`, `.env.prod`)로 덮어쓴다**(뒤 파일이 우선). 환경은 OS 환경변수 `APP_ENV`(없으면 `ENVIRONMENT`, 기본 `dev`)로 결정한다 — `config.py`의 `_env_files()` / `_active_env()` 참조.

```text
로드 순서: .env  →  .env.<APP_ENV>
  APP_ENV=prod uvicorn app.main:app   →  .env + .env.prod
  uvicorn app.main:app (미지정)        →  .env + .env.dev
```

- **`.env`** : dev/prod 공통값(토스 API URL·웹훅 IP·rate limit·재시도 정책·SMTP 등)
- **`.env.dev`** : 개발 전용(로컬 DB/Redis, `test_sk_*`, dev 로그인 자동입력, Swagger 계정)
- **`.env.prod`** : 운영 전용(운영 URL/DB, `live_sk_*`, `TRUST_PROXY=true` 등 — 실제 값으로 교체).
  `TRUST_PROXY=true`일 때는 **반드시 `TRUST_PROXY_HOPS`를 실제 프록시 단 수에 맞게** 설정한다
  (기본 1 = 프록시 1단). 프록시가 XFF를 append만 해도 안전하도록 '오른쪽에서 n번째' 방식으로
  클라이언트 IP를 판별한다 — [03. 인증과 보안](03-auth-and-security.md) 참조.

시작: `cp .env.example .env.dev` 후 값 채우기. 모든 키 예시는 `.env.example`에 있다.

필수 항목(환경별 파일에):

```dotenv
# AES-256-GCM 키: python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
ENCRYPTION_KEY=<base64 32바이트>     # ⚠️ dev/prod 서로 다른 키 사용 권장
# 토스페이먼츠 시크릿 키 (dev=test_sk_..., prod=live_sk_...)
TOSS_SECRET_KEY=test_sk_...
```

주요 설정 항목 (기본값 포함): `app/core/config.py`

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `environment` | `dev` | `dev` / `test` / `prod` |
| `database_url` | `postgresql+asyncpg://payment:payment@localhost:5433/payment` | asyncpg 비동기 URL |
| `redis_url` | `redis://localhost:6380/0` | |
| `encryption_key` | (필수) | base64 32바이트 AES 키 |
| `toss_secret_key` | (필수) | 토스페이먼츠 시크릿 키 |
| `scheduler_enabled` | `True` | False이면 갱신 배치 비활성화 |
| `scheduler_interval_minutes` | `5` | 갱신 배치 실행 주기 |
| `rate_limit_per_minute` | `120` | 일반 API 분당 허용 건수 |
| `rate_limit_payment_per_minute` | `20` | 결제 API 분당 허용 건수 |
| `db_pool_size` / `db_max_overflow` | `10` / `20` | DB 커넥션 풀(총 최대 = 합). 감사 Phase 1에서 명시 설정 도입 |
| `db_pool_timeout` / `db_pool_recycle` | `30` / `1800` | 풀 고갈 대기 한도(초) / 커넥션 재활용 주기(초) |
| `trust_proxy` | `False` | True면 X-Forwarded-For로 클라이언트 IP 판별(리버스 프록시 환경) |
| `trust_proxy_hops` | `1` | 신뢰 프록시 단 수 — XFF의 **오른쪽에서 n번째**를 클라이언트 IP로 사용(위조 방어, 03 문서 참조) |
| `session_absolute_ttl_seconds` | `43200` | 어드민 세션 절대 수명(12시간) — 유휴 연장과 무관하게 초과 시 파기(감사 Phase 2) |
| `public_service_list_enabled` | `True` | 무인증 `GET /api/v1/services` 노출 여부. 인터넷 직노출 운영에서는 `false` 권장 |
| `gmail_id` / `gmail_pw` | `` (빈값) | 설정 시 실제 메일 발송, 미설정 시 콘솔 출력 |
| `swagger_id` / `swagger_pw` | `` (빈값) | **Swagger 문서 접근용 HTTP Basic 계정.** 둘 다 설정해야 `/docs`·`/openapi.json` 노출, 비우면 404 |

> **주의**: `/docs`와 `/openapi.json`은 **`SWAGGER_ID`/`SWAGGER_PW`가 둘 다 설정된 경우에만** 노출되며 HTTP Basic 인증을 요구한다. 둘 중 하나라도 비면 환경과 무관하게 404다 (`main.py`의 `_register_protected_docs`).

### 3-5. DB 마이그레이션

```bash
uv run alembic upgrade head
```

`alembic/env.py:33-34`: 환경변수 `DATABASE_URL`이 있으면 `.ini` 기본값을 덮어쓴다.

처음 팀에 합류해서 스키마가 없다면 위 명령 한 번으로 전체 테이블이 생성된다.

### 3-6. 서버 기동

```bash
uv run uvicorn app.main:app --reload --port 8000
```

기동 후 접속:
- 루트: http://localhost:8000/ → **`/admin`으로 자동 리다이렉트**(미로그인 시 로그인 화면). `main.py`의 `root()`(307 redirect)
- API 문서: http://localhost:8000/docs → **HTTP Basic 인증**(`SWAGGER_ID`/`SWAGGER_PW`). 미설정 시 404
- 어드민: http://localhost:8000/admin
- 헬스체크: http://localhost:8000/health

### 3-7. lifespan에서 초기화되는 것

`app/main.py:44-66`의 `lifespan` 함수가 서버 기동 시 다음 순서로 초기화한다:

```
1. app.state.settings   — Settings 인스턴스 (config.py)         main.py:46
2. app.state.cipher     — AesGcmCipher (ENCRYPTION_KEY 검증)    main.py:48
3. app.state.engine     — SQLAlchemy 비동기 엔진                 main.py:49
4. app.state.session_factory — AsyncSession 팩토리               main.py:50
5. app.state.redis      — Redis.from_url(redis_url)             main.py:51
6. app.state.toss       — HttpTossClient (토스 API 클라이언트)   main.py:52-53
7. app.state.email_sender — Gmail 또는 Console 발송체            main.py:54
8. scheduler            — APScheduler 시작 (scheduler_enabled=True 시)  main.py:55
```

> **cipher 초기화 실패 = 즉시 종료**: `ENCRYPTION_KEY`가 없거나 32바이트가 아니면 `AesGcmCipher.__init__`(`crypto.py:27-30`)에서 `ValueError`가 발생해 서버가 기동되지 않는다. 의도적 설계다.

종료 시에는 스케줄러 → Redis → Toss HTTP 클라이언트 → DB 엔진 순으로 정리한다 (`main.py:57-66`).

### 3-8. 스케줄러 동작

`app/scheduler/runner.py:49-62`의 `start_scheduler`가 `AsyncIOScheduler`를 시작하고, `scheduler_interval_minutes`(기본 5분) 주기로 `run_renewals`를 실행한다.

`run_renewals`는 Redis `SET NX` 전역 락(`GLOBAL_LOCK_KEY`)으로 다중 인스턴스 중복 실행을 방지한다 (`runner.py:36`). 락 획득 실패 시 해당 주기는 건너뛴다. 배치 완료·예외 모두 `finally`에서 락을 해제한다 (`runner.py:45-46`).

---

## 4. 테스트 실행

```bash
# 전체 테스트
uv run pytest

# 특정 폴더만
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/e2e/
uv run pytest tests/security/

# 커버리지 포함
uv run pytest --cov=app --cov-report=term-missing
```

`pyproject.toml:37-40`: `asyncio_mode = "auto"` 설정이므로 `async def` 테스트 함수에 별도 데코레이터가 필요 없다.

### 테스트 구조

| 폴더 | 범위 | DB/Redis 필요 |
|------|------|--------------|
| `tests/unit/` | 순수 함수 (billing_math, crypto, security 등) | 아니오 |
| `tests/integration/` | 서비스 레이어 + 실제 DB/Redis | 예 |
| `tests/e2e/` | HTTP 엔드포인트 전체 흐름 (ASGI) | 예 |
| `tests/security/` | HMAC 인증, 어드민 보안 경계 | 예 |

### conftest.py 핵심 픽스처 (`tests/conftest.py`)

| 픽스처 | 범위 | 역할 |
|--------|------|------|
| `settings` | session | `environment=test`, `.env` 무시, 스케줄러 비활성화 (`conftest.py:22-34`) |
| `engine` | session | 테스트 DB 스키마 drop_all → create_all (`conftest.py:43-49`) |
| `session_factory` | session | `expire_on_commit=False` 팩토리 |
| `db` | function | 요청별 `AsyncSession` |
| `redis_client` | function | DB 15번 (테스트 전용) |
| `clean_db` | function | 테스트 후 전 테이블 TRUNCATE |
| `clean_redis` | function | 테스트 후 `flushdb` |
| `fake_toss` | function | `FakeTossClient` — 실제 토스 API 미호출 |
| `email_sender` | function | `RecordingEmailSender` — 발송 내역 메모리 기록 |
| `app` | function | `create_app(settings, ..., engine=engine)` + `LifespanManager` |
| `client` | function | `httpx.AsyncClient(ASGITransport(app))` — 실제 HTTP 서버 불필요 |

`tests/integration/conftest.py`와 `tests/e2e/conftest.py`에는 각각 `autouse=True`로 `clean_db + clean_redis` 조합이 걸려 있어 테스트 간 데이터가 격리된다.

HMAC 서명 헤더는 `tests/helpers.py:8-21`의 `signed_headers()`로 생성한다.

---

## 5. 요청 처리 공통 파이프라인

### 5-1. 외부 API 요청 흐름 (`/api/v1/...`)

```
외부 서비스 (HTTP 요청)
       │  X-Service-Key / X-Timestamp / X-Nonce / X-Signature 헤더 포함
       ▼
app/main.py:78
  app.include_router(api_v1_router, prefix="/api/v1")
       │
       ▼
app/api/errors.py:26  ← register_error_handlers
  DomainError → JSON, RequestValidationError → 422, Exception → 500
       │
       ▼
app/api/v1/<라우터>.py  (예: subscriptions.py, payments.py)
  @router.post("/subscriptions", ...)
       │
       ├─ Depends(payment_rate_limit)         ← 결제성 엔드포인트
       │      또는 Depends(authenticate_service)  ← 읽기/상태변경 엔드포인트
       │
       ▼
app/api/deps.py:77  authenticate_service
  1. ensure_server_enabled(db)  — 킬스위치 확인 (ServerDisabledError → 503)
  2. X-Service-Key → SHA-256 해시 → DB Service 조회
  3. IP 화이트리스트 확인 (service.allowed_ips)
  4. Redis sliding-window rate limit  (rl:{service_id}:{window})
  5. X-Timestamp 윈도우 검증 (±300초, hmac_timestamp_tolerance_seconds)
  6. HMAC-SHA256 서명 검증 (method + path + timestamp + nonce + body)
  7. Redis nonce 1회용 소비  (nonce:{service_id}:{nonce}, TTL 600s)
  → Service 객체 반환
       │
       ▼
app/api/deps.py:141  payment_rate_limit  (결제성 엔드포인트 추가)
  Redis sliding-window (rlp:{service_id}:{window}, 분당 20건)
  → Service 객체 반환
       │
       ▼
app/services/<도메인>.py  (예: subscriptions.py, payments.py)
  비즈니스 로직 처리
  DomainError 발생 시 상위로 전파 → errors.py 핸들러가 JSON 변환
  성공 시 db.commit() 또는 session.flush() → commit()
       │
       ▼
app/models/<모델>.py  (SQLAlchemy ORM)
  SELECT / INSERT / UPDATE → asyncpg → PostgreSQL
       │
       ▼
JSON 응답 반환
```

#### 핵심 의존성 주입 함수 (`app/api/deps.py`)

| 함수 | 반환 | 설명 |
|------|------|------|
| `get_settings` | `Settings` | `app.state.settings` 반환 (`deps.py:32`) |
| `get_db` | `AsyncSession` | 요청 범위 세션, 완료 후 close (`deps.py:37`) |
| `get_redis` | `Redis` | 앱 공유 Redis 클라이언트 (`deps.py:43`) |
| `get_cipher` | `AesGcmCipher` | AES-GCM 암복호화 객체 (`deps.py:48`) |
| `get_toss` | `TossClient` | 토스페이먼츠 API 클라이언트 (`deps.py:53`) |
| `get_email_sender` | `EmailSender` | 이메일 발송 구현체 (`deps.py:58`) |
| `authenticate_service` | `Service` | 3중 인증 후 Service ORM 반환 (`deps.py:77`) |
| `payment_rate_limit` | `Service` | 결제 전용 추가 제한 (`deps.py:141`) |

### 5-2. 어드민 화면 요청 흐름 (`/admin/...`)

```
브라우저 (HTTP 요청, admin_session 쿠키 포함)
       │
       ▼
app/main.py:79
  app.include_router(admin_router, prefix="/admin")
       │
       ▼
app/admin/__init__.py:74-100
  router.include_router(auth.router)
  router.include_router(services.router)
  ...
       │
       ▼
app/admin/routes/<화면>.py  (예: routes/services.py, routes/plans.py)
  @router.get("/services")
       │
       ├─ Depends(require_admin)   ← SYSTEM_ADMIN 전용
       │  또는 Depends(require_any)  ← SYSTEM_ADMIN + SERVICE_MANAGER
       │
       ▼
app/admin/deps.py:60  require_user
  1. 쿠키 admin_session → Redis "session:{id}" 조회
  2. user_id → DB User 조회 → user.status == ACTIVE 확인
  3. GlobalSettings.admin_allowed_ips IP 검사 (설정 시)
  → AdminContext(user, session_id, csrf_token, service_ids) 반환

app/admin/deps.py:86  require_role
  ctx.user.role in 허용 목록 확인 → PermissionDeniedError(403)
       │
       ▼
app/services/<도메인>.py
  비즈니스 로직 + db.commit()
       │
       ▼
app/admin/__init__.py:51  render()  또는  render_list()
  Jinja2 TemplateResponse
  htmx 요청(HX-Request 헤더)이면 partial 템플릿만 렌더
  saved= 쿼리 파라미터 있으면 HX-Trigger: showSaved 헤더 추가
       │
       ▼
HTML 응답 (또는 303 RedirectResponse)
```

#### 어드민 미인증 처리

`AdminAuthRequired` 예외 발생 시 (`admin/deps.py:34`):
- 일반 요청: `303 → /admin/login` 리다이렉트
- htmx 요청: `204 + HX-Redirect: /admin/login` 헤더 (`admin/deps.py:118-130`)

#### POST 요청 CSRF 보호

모든 어드민 POST는 `validate_csrf()`를 통과해야 한다 (`admin/deps.py:105-110`).
폼의 hidden `csrf_token` 필드 또는 `X-CSRF-Token` 헤더 중 하나를 세션 토큰과 비교한다.

---

## 6. 계층별 책임 — "어디를 고쳐야 하나"

### 계층 구조

```
┌─────────────────────────────────────────────┐
│  app/api/v1/*.py  또는  app/admin/routes/*  │  ← 라우터 계층 (얇게)
│  역할: 요청 역직렬화·Pydantic 검증·         │
│        의존성 주입·응답 직렬화·렌더         │
└──────────────────────┬──────────────────────┘
                       │ 함수 호출 (서비스 함수 직접 호출)
┌──────────────────────▼──────────────────────┐
│  app/services/*.py                          │  ← 서비스 계층 (핵심)
│  역할: 비즈니스 규칙·트랜잭션·커밋         │
│        DomainError 발생                     │
│        db.commit() 은 여기서만 호출         │
└──────────────────────┬──────────────────────┘
                       │ SELECT / INSERT / UPDATE
┌──────────────────────▼──────────────────────┐
│  app/models/*.py                            │  ← 모델 계층
│  역할: 테이블 정의·관계·열거형              │
│        비즈니스 로직 없음                   │
└──────────────────────┬──────────────────────┘
                       │
                   PostgreSQL
```

### 수정 위치 가이드

| 상황 | 수정 위치 |
|------|-----------|
| HTTP 상태코드나 응답 필드 변경 | `app/api/v1/*.py` 또는 `app/schemas/api.py` |
| 어드민 화면 레이아웃·필드 변경 | `app/admin/templates/**/*.html` |
| 비즈니스 규칙 변경 (할인 계산, 구독 제한 등) | `app/services/*.py` |
| 결제 금액 계산 | `app/services/billing_math.py` |
| DB 컬럼·테이블 추가/변경 | `app/models/*.py` → `alembic revision --autogenerate` → `alembic upgrade head` |
| 설정값 추가 | `app/core/config.py` `Settings` 클래스에 필드 추가 |
| 에러 코드 추가 | `app/core/errors.py`에 `DomainError` 서브클래스 추가 |
| 갱신 배치 로직 | `app/services/renewals.py` |
| 토스 API 호출 방식 | `app/toss/client.py` |

### 중요 규칙

- **`db.commit()`은 서비스 레이어에서만** 호출한다. 라우터에서 직접 commit하지 않는다.
- **금액은 서버가 계산**한다. `app/schemas/api.py:5-6` 주석 참고 — 클라이언트가 금액을 전달하면 검증 없이 거부된다.
- **시간은 UTC 저장, KST 표시**: DB에는 항상 UTC로 저장하고, 화면 출력 시에만 `kst_format()`으로 변환한다. 자세한 내용은 아래 섹션 참고.

---

## 7. 초보자 팁

### 7-1. 로그 보는 법

코드 전반에서 `logging.getLogger("payment.<모듈명>")`을 사용한다.

| logger 이름 | 파일 |
|-------------|------|
| `payment.api` | `app/api/errors.py:23` |
| `payment.subscriptions` | `app/services/subscriptions.py:51` |
| `payment.renewals` | `app/services/renewals.py:44` |
| `payment.scheduler` | `app/scheduler/runner.py:17` |
| `payment.email` | `app/notifications/email.py:27` |

개발 중 특정 모듈만 상세하게 보려면:

```bash
PYTHONPATH=. python -c "import logging; logging.basicConfig(level=logging.DEBUG)"
# 또는 uvicorn 실행 시
uv run uvicorn app.main:app --reload --log-level debug
```

### 7-2. 자주 쓰는 명령

```bash
# 새 마이그레이션 생성 (모델 변경 후)
uv run alembic revision --autogenerate -m "add_some_column"

# 마이그레이션 적용
uv run alembic upgrade head

# 마이그레이션 1단계 되돌리기
uv run alembic downgrade -1

# 현재 마이그레이션 상태
uv run alembic current

# 테스트 DB만 초기화
TEST_DATABASE_URL=... uv run pytest tests/integration/ -x

# 특정 테스트만 실행
uv run pytest tests/integration/test_subscription_create.py -v
```

### 7-3. 흔한 함정

#### UTC 저장 / KST 표시

**잘못된 패턴**:
```python
# 절대 하지 말 것
from datetime import datetime
now = datetime.now()          # naive datetime, UTC 보장 안 됨
now = datetime.utcnow()       # naive datetime 반환, 의미적으로 불안전
```

**올바른 패턴**:
```python
from app.core.clock import utcnow, kst_format

now = utcnow()                         # timezone-aware UTC datetime (clock.py:15-21)
display = kst_format(now)              # "2026-06-10 14:30" (clock.py:24-31)
display = kst_format(now, "%m-%d")     # "06-10"
```

Jinja2 템플릿에서는 `{{ created_at|kst }}` 필터를 사용한다 (`admin/__init__.py:17`).

#### ENCRYPTION_KEY 없으면 기동 불가

`.env`에 `ENCRYPTION_KEY`가 없거나 base64 32바이트가 아니면 `lifespan`의 `AesGcmCipher` 생성 단계에서 즉시 실패한다. 새 환경 구축 시 반드시 키를 먼저 생성해야 한다:

```bash
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

#### 비동기 ORM lazy-load 금지

SQLAlchemy 비동기 세션에서 관계 필드를 직접 접근하면 `greenlet_spawn` 오류가 발생한다. 반드시 `selectinload` 또는 별도 쿼리로 미리 로드해야 한다.

#### /docs 가 404 또는 로그인 팝업

`/docs`·`/openapi.json`은 `SWAGGER_ID`/`SWAGGER_PW`가 **둘 다 설정된 경우에만** 등록되며 HTTP Basic 인증을 요구한다(`main.py`의 `_register_protected_docs`). 증상별 원인:
- **404**: 두 값 중 하나라도 비어 있음 → 환경별 `.env`에 `SWAGGER_ID`/`SWAGGER_PW`를 모두 설정.
- **로그인 팝업/401**: 정상. 설정한 id/pw를 입력하면 Swagger UI가 열린다. Swagger의 *Try it out*은 서명을 자동 계산하지 못하므로 외부 API 호출은 HMAC 헤더를 직접 만들어야 한다(문서 상단 설명 참조).

#### 구독은 서비스+사용자 당 1개

`EXPIRED`를 제외한 '열린' 상태(`TRIAL`, `ACTIVE`, `PAST_DUE`, `SUSPENDED`, `CANCELED`)의 구독이 이미 있으면 새 구독 생성이 `ConflictError(409)`로 거부된다 (`models/enums.py:82-86`, `services/subscriptions.py:54`).

#### 어드민 세션 TTL

기본 30분(`session_ttl_seconds=1800`). 유휴 상태로 30분 경과 시 자동 로그아웃된다. 개발 중 번거로우면 `.env`에서 값을 늘릴 수 있다 (`config.py:42`).

---

## 관련 문서

- **테이블 구조·ORM 상세** → `02-database.md`
- **인증·HMAC·세션·역할** → `03-auth-and-security.md`
- **구독 생성** → `04-subscription-create.md`
- **갱신·만료·재시도(스케줄러 배치)** → `05-subscription-renewal.md`
- **구독 취소·재개·수동결제·카드변경** → `06-subscription-manage.md`
- **단건 결제** → `07-one-off-payment.md`
- **어드민 화면별 매뉴얼** → `admin/README.md`
- **외부 API·샘플 연동** → `15-external-api-and-sample.md`
- **토스 API 참고 문서** → `docs/toss/`
- **설계 요청 이력** → `docs/requests/`
