# 구독/결제 API 서버 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사내 서비스 공용 구독/결제 API 서버 — 토스페이먼츠 빌링키 기반 자동결제, HMAC 인증 외부 API, htmx admin.

**Architecture:** 단일 FastAPI 프로세스(APScheduler 내장 + Redis 분산 락). 비즈니스 로직은 전부 `app/services/`에 두고 API/admin/스케줄러는 얇은 어댑터. 토스 클라이언트·이메일은 Protocol로 추상화해 테스트에서 fake 교체.

**Tech Stack:** Python 3.13(uv), FastAPI, SQLAlchemy 2.0 async + asyncpg + Alembic, Redis, httpx, Jinja2+htmx, argon2-cffi, cryptography(AES-256-GCM), APScheduler, pytest+respx.

**Spec:** `docs/superpowers/specs/2026-06-05-subscription-payment-server-design.md`

**전제:** `docker compose up -d`로 PostgreSQL(localhost:5433)·Redis(localhost:6380)가 떠 있어야 통합 테스트가 돈다. 토스 API는 절대 실호출하지 않는다(respx 모킹/Fake만).

---

## 파일 구조 (전체 맵)

```
app/
  __init__.py
  main.py                  # create_app 팩토리, lifespan
  cli.py                   # create-admin 명령
  core/
    config.py              # Settings (pydantic-settings)
    clock.py               # utcnow()
    crypto.py              # AesGcmCipher
    security.py            # 키 생성/해시, HMAC 서명, 비밀번호
    errors.py              # DomainError 계층
  models/
    base.py  enums.py  service.py  user.py  plan.py
    subscription.py  payment.py  webhook_event.py  audit_log.py
  schemas/api.py           # 외부 API 요청/응답 Pydantic
  toss/
    types.py  errors.py  client.py  fake.py
  services/
    billing_math.py        # 기간/금액 계산 (순수 함수)
    audit.py  registry.py  auth.py  plans.py
    subscriptions.py  renewals.py  webhooks.py
  notifications/email.py   # EmailSender Protocol + Console/Recording
  api/
    deps.py  errors.py
    v1/__init__.py  v1/subscriptions.py  v1/plans.py  v1/payments.py  v1/webhooks.py
  scheduler/runner.py
  admin/
    deps.py
    routes/ auth.py dashboard.py services.py plans.py subscriptions.py users.py audit.py
    templates/ (base, login, setup_password, dashboard, services/*, plans/*,
                subscriptions/*, payments/*, users/*, audit/*)
  static/admin.css
alembic/  (env.py, versions/xxxx_initial.py)
tests/
  conftest.py  helpers.py  factories.py
  unit/        test_crypto.py test_security.py test_billing_math.py test_toss_client.py
  integration/ conftest.py test_registry.py test_auth_service.py test_plans_service.py
               test_subscription_create.py test_subscription_manage.py test_renewals.py
               test_api_endpoints.py test_webhooks.py
  security/    conftest.py test_hmac_auth.py test_admin_security.py
  e2e/         conftest.py test_full_flow.py
docker-compose.yml  scripts/init-db.sql  .env.example
```

**핵심 타입 계약 (전 태스크 공통 — 시그니처 불일치 금지):**

- `AesGcmCipher(key_b64: str)` / `.encrypt(str) -> str` / `.decrypt(str) -> str`
- `sign_request(secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str`
- `TossClient` Protocol: `issue_billing_key(auth_key, customer_key) -> BillingKeyResult`, `charge(billing_key, customer_key, amount: int, order_id, order_name, idempotency_key) -> ChargeResult`, `get_payment_by_order_id(order_id) -> ChargeResult | None`, `delete_billing_key(billing_key) -> None`
- `EmailSender` Protocol: `async send(to: str, subject: str, body: str) -> None`
- 금액은 전부 **int (KRW)**. 시간은 전부 **timezone-aware UTC** (`app.core.clock.utcnow`).
- 서비스 함수는 `AsyncSession`을 받아 자체 `commit()`. 갱신 배치만 `session_factory`를 받음.

---

### Task 1: 프로젝트 셋업 (의존성, docker-compose, 환경)

**Files:**
- Modify: `pyproject.toml`
- Create: `docker-compose.yml`, `scripts/init-db.sql`, `.env.example`
- Modify: `.env` (올바른 키 이름 추가 — git 미추적)
- Create: `app/__init__.py`, `tests/__init__.py`
- Delete: `main.py` (루트의 보일러플레이트 — `app/main.py`로 대체 예정)

- [ ] **Step 1: pyproject.toml 전체 교체**

```toml
[project]
name = "payment-system"
version = "0.1.0"
description = "사내 구독/결제 API 서버 (TossPayments 빌링)"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "redis>=5.2",
    "httpx>=0.28",
    "jinja2>=3.1",
    "python-multipart>=0.0.20",
    "pydantic-settings>=2.7",
    "argon2-cffi>=23.1",
    "cryptography>=44.0",
    "python-dateutil>=2.9",
    "apscheduler>=3.11",
    "greenlet>=3.1",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.25",
    "pytest-cov>=6.0",
    "respx>=0.22",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
testpaths = ["tests"]

[tool.coverage.run]
source = ["app"]
```

- [ ] **Step 2: docker-compose.yml 작성**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: payment
      POSTGRES_PASSWORD: payment
      POSTGRES_DB: payment
    ports:
      - "5433:5432"
    volumes:
      - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/init-db.sql
      - pgdata:/var/lib/postgresql/data
  redis:
    image: redis:7-alpine
    ports:
      - "6380:6379"
volumes:
  pgdata:
```

- [ ] **Step 3: scripts/init-db.sql 작성**

```sql
CREATE DATABASE payment_test;
```

- [ ] **Step 4: .env.example 작성**

```bash
ENVIRONMENT=dev
BASE_URL=http://localhost:8000
DATABASE_URL=postgresql+asyncpg://payment:payment@localhost:5433/payment
REDIS_URL=redis://localhost:6380/0
# 32바이트 base64. 생성: python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
ENCRYPTION_KEY=
TOSS_SECRET_KEY=test_sk_xxxx
# TOSS_API_BASE_URL=https://api.tosspayments.com
```

- [ ] **Step 5: .env에 올바른 키 추가**

기존 `.env`의 `TOSS_SECREKEY`(오타)는 그대로 두고, 아래 줄을 추가한다
(`TOSS_SECRET_KEY` 값은 기존 `TOSS_SECREKEY` 값과 동일하게,
`ENCRYPTION_KEY`는 위 python 명령으로 새로 생성):

```bash
TOSS_SECRET_KEY=test_sk_DpexMgkW36bOeQZ5dLjE3GbR5ozO
ENCRYPTION_KEY=<생성된 32바이트 base64>
DATABASE_URL=postgresql+asyncpg://payment:payment@localhost:5433/payment
REDIS_URL=redis://localhost:6380/0
```

- [ ] **Step 6: 빈 패키지/루트 정리**

```bash
rm main.py
mkdir -p app tests scripts
touch app/__init__.py tests/__init__.py
```

- [ ] **Step 7: 설치 및 인프라 기동 검증**

```bash
uv sync
docker compose up -d
docker compose ps   # postgres, redis 둘 다 running 확인
uv run python -c "import fastapi, sqlalchemy, redis, httpx, argon2, apscheduler; print('ok')"
```
Expected: `ok`

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock docker-compose.yml scripts/init-db.sql .env.example app/__init__.py tests/__init__.py
git rm --cached main.py 2>/dev/null; git add -u
git commit -m "chore: 프로젝트 의존성/로컬 인프라 셋업"
```

---

### Task 2: 설정(Settings) + AES-GCM 암호화

**Files:**
- Create: `app/core/__init__.py`, `app/core/config.py`, `app/core/clock.py`, `app/core/crypto.py`
- Test: `tests/unit/__init__.py`, `tests/unit/test_crypto.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/unit/test_crypto.py`

```python
import base64

import pytest

from app.core.crypto import AesGcmCipher

KEY = base64.b64encode(b"0" * 32).decode()


def test_encrypt_decrypt_roundtrip():
    cipher = AesGcmCipher(KEY)
    assert cipher.decrypt(cipher.encrypt("billing-key-123")) == "billing-key-123"


def test_ciphertext_differs_each_time():
    cipher = AesGcmCipher(KEY)
    assert cipher.encrypt("same") != cipher.encrypt("same")  # 랜덤 nonce


def test_tampered_ciphertext_raises():
    cipher = AesGcmCipher(KEY)
    token = cipher.encrypt("secret")
    raw = bytearray(base64.b64decode(token))
    raw[-1] ^= 0xFF
    with pytest.raises(Exception):
        cipher.decrypt(base64.b64encode(bytes(raw)).decode())


def test_wrong_key_length_rejected():
    with pytest.raises(ValueError):
        AesGcmCipher(base64.b64encode(b"short").decode())
```

- [ ] **Step 2: 실패 확인**

```bash
mkdir -p app/core tests/unit && touch app/core/__init__.py tests/unit/__init__.py
uv run pytest tests/unit/test_crypto.py -v
```
Expected: FAIL (`ModuleNotFoundError: app.core.crypto`)

- [ ] **Step 3: 구현** — `app/core/clock.py`

```python
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
```

`app/core/crypto.py`

```python
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AesGcmCipher:
    """빌링키·HMAC secret 저장용 AES-256-GCM 암호화."""

    def __init__(self, key_b64: str) -> None:
        key = base64.b64decode(key_b64)
        if len(key) != 32:
            raise ValueError("ENCRYPTION_KEY must decode to 32 bytes")
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode(), None)
        return base64.b64encode(nonce + ct).decode()

    def decrypt(self, token: str) -> str:
        raw = base64.b64decode(token)
        return self._aesgcm.decrypt(raw[:12], raw[12:], None).decode()
```

`app/core/config.py`

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

TOSS_WEBHOOK_IPS = [
    "13.124.18.147", "13.124.108.35", "3.36.173.151", "3.38.81.32",
    "115.92.221.121", "115.92.221.122", "115.92.221.123",
    "115.92.221.125", "115.92.221.126", "115.92.221.127",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "dev"  # dev | test | prod
    base_url: str = "http://localhost:8000"
    database_url: str = "postgresql+asyncpg://payment:payment@localhost:5433/payment"
    redis_url: str = "redis://localhost:6380/0"
    encryption_key: str = ""
    toss_secret_key: str = ""
    toss_api_base_url: str = "https://api.tosspayments.com"
    session_ttl_seconds: int = 1800
    hmac_timestamp_tolerance_seconds: int = 300
    rate_limit_per_minute: int = 120
    rate_limit_payment_per_minute: int = 20
    trust_proxy: bool = False
    scheduler_enabled: bool = True
    scheduler_interval_minutes: int = 5
    webhook_ip_check_enabled: bool = True
    toss_webhook_allowed_ips: list[str] = TOSS_WEBHOOK_IPS
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/unit/test_crypto.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/core tests/unit
git commit -m "feat: 설정/시계/AES-GCM 암호화 코어"
```

---

### Task 3: 보안 유틸 (API 키, HMAC 서명, 비밀번호)

**Files:**
- Create: `app/core/security.py`
- Test: `tests/unit/test_security.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/unit/test_security.py`

```python
from app.core.security import (
    constant_time_equals,
    generate_hmac_secret,
    generate_service_api_key,
    generate_setup_token,
    hash_password,
    sha256_hex,
    sign_request,
    verify_password,
)


def test_api_key_format_and_uniqueness():
    k1, k2 = generate_service_api_key(), generate_service_api_key()
    assert k1.startswith("svc_") and len(k1) > 30
    assert k1 != k2


def test_secret_and_token_generation():
    assert len(generate_hmac_secret()) >= 48
    assert generate_setup_token() != generate_setup_token()


def test_sha256_hex_deterministic():
    assert sha256_hex("abc") == sha256_hex("abc")
    assert len(sha256_hex("abc")) == 64


def test_sign_request_changes_with_each_component():
    base = dict(secret="s3cret", method="POST", path="/api/v1/subscriptions",
                timestamp="1717570800", nonce="n-1", body=b'{"a":1}')
    sig = sign_request(**base)
    assert sig == sign_request(**base)  # 결정적
    for field, value in [("method", "GET"), ("path", "/x"), ("timestamp", "1"),
                         ("nonce", "n-2"), ("body", b'{"a":2}'), ("secret", "other")]:
        changed = {**base, field: value}
        assert sign_request(**changed) != sig


def test_constant_time_equals():
    assert constant_time_equals("abc", "abc")
    assert not constant_time_equals("abc", "abd")


def test_password_hash_and_verify():
    h = hash_password("CorrectHorse9!")
    assert h != "CorrectHorse9!"
    assert verify_password("CorrectHorse9!", h)
    assert not verify_password("wrong", h)
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/unit/test_security.py -v
```
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: 구현** — `app/core/security.py`

```python
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

_ph = PasswordHasher()


def generate_service_api_key() -> str:
    return "svc_" + secrets.token_urlsafe(32)


def generate_hmac_secret() -> str:
    return secrets.token_urlsafe(48)


def generate_setup_token() -> str:
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def sign_request(secret: str, method: str, path: str, timestamp: str,
                 nonce: str, body: bytes) -> str:
    """외부 API 요청 서명: HMAC-SHA256(secret, canonical string)."""
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/unit -v
```
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add app/core/security.py tests/unit/test_security.py
git commit -m "feat: API키/HMAC서명/비밀번호 보안 유틸"
```

---

### Task 4: 도메인 에러 + DB 모델 + Alembic 초기 마이그레이션

**Files:**
- Create: `app/core/errors.py`, `app/core/db.py`
- Create: `app/models/__init__.py`, `app/models/base.py`, `app/models/enums.py`, `app/models/service.py`, `app/models/user.py`, `app/models/plan.py`, `app/models/subscription.py`, `app/models/payment.py`, `app/models/webhook_event.py`, `app/models/audit_log.py`
- Create: `alembic.ini`, `alembic/env.py`, `alembic/versions/` (autogenerate)
- Test: `tests/integration/__init__.py`, `tests/integration/test_models.py`

- [ ] **Step 1: 도메인 에러 작성** — `app/core/errors.py`

```python
class DomainError(Exception):
    code = "DOMAIN_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None,
                 http_status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status


class NotFoundError(DomainError):
    code = "NOT_FOUND"
    http_status = 404


class ConflictError(DomainError):
    code = "CONFLICT"
    http_status = 409


class AuthenticationError(DomainError):
    code = "UNAUTHORIZED"
    http_status = 401


class PermissionDeniedError(DomainError):
    code = "FORBIDDEN"
    http_status = 403


class InputValidationError(DomainError):
    code = "VALIDATION_ERROR"
    http_status = 422


class RateLimitedError(DomainError):
    code = "RATE_LIMITED"
    http_status = 429


class PaymentFailedError(DomainError):
    code = "PAYMENT_FAILED"
    http_status = 402
```

- [ ] **Step 2: DB 코어 작성** — `app/core/db.py`

```python
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 3: 모델 작성** — `app/models/base.py`

```python
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convention)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

`app/models/enums.py`

```python
from enum import StrEnum


class ServiceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class UserRole(StrEnum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    SERVICE_MANAGER = "SERVICE_MANAGER"


class UserStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    LOCKED = "LOCKED"


class BillingCycle(StrEnum):
    YEAR = "YEAR"
    MONTH = "MONTH"
    WEEK = "WEEK"
    DAY = "DAY"


class FirstPaymentType(StrEnum):
    NONE = "NONE"
    FREE = "FREE"
    DISCOUNT_AMOUNT = "DISCOUNT_AMOUNT"
    DISCOUNT_PERCENT = "DISCOUNT_PERCENT"


class PlanStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class SubscriptionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"


class PaymentStatus(StrEnum):
    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class PaymentType(StrEnum):
    FIRST = "FIRST"
    RENEWAL = "RENEWAL"
    RETRY = "RETRY"


class WebhookStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"
```

`app/models/service.py`

```python
import uuid

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import ServiceStatus


class Service(TimestampMixin, Base):
    __tablename__ = "services"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    allowed_ips: Mapped[list] = mapped_column(JSONB, default=list)
    manager_email: Mapped[str] = mapped_column(String(255))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hmac_secret_encrypted: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(20), default=ServiceStatus.ACTIVE)
```

`app/models/user.py`

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import UserStatus


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512), default="")
    role: Mapped[str] = mapped_column(String(20))
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=UserStatus.PENDING)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordSetupToken(Base):
    __tablename__ = "password_setup_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`app/models/plan.py`

```python
import uuid

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import FirstPaymentType, PlanStatus


class Plan(TimestampMixin, Base):
    __tablename__ = "plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[int] = mapped_column(BigInteger)  # KRW 정수
    currency: Mapped[str] = mapped_column(String(3), default="KRW")
    billing_cycle: Mapped[str] = mapped_column(String(10))
    cycle_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_payment_type: Mapped[str] = mapped_column(String(20), default=FirstPaymentType.NONE)
    first_payment_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=PlanStatus.ACTIVE)
```

`app/models/subscription.py`

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import SubscriptionStatus


class Subscription(TimestampMixin, Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    external_user_id: Mapped[str] = mapped_column(String(255))
    customer_key: Mapped[str] = mapped_column(String(300))
    billing_key_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    billing_key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    card_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=SubscriptionStatus.ACTIVE)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_billing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        # 서비스+사용자 당 1개 구독 규칙 (EXPIRED만 제외 → 재구독 허용)
        Index(
            "uq_subscriptions_one_per_user",
            "service_id", "external_user_id",
            unique=True,
            postgresql_where=text("status IN ('ACTIVE','PAST_DUE','CANCELED')"),
        ),
        Index("ix_subscriptions_due", "status", "next_billing_at"),
    )
```

`app/models/payment.py`

```python
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import PaymentStatus


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="RESTRICT"), index=True)
    order_id: Mapped[str] = mapped_column(String(64), unique=True)
    toss_payment_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    payment_type: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), default=PaymentStatus.PENDING)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(300))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

`app/models/webhook_event.py`

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import WebhookStatus


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    transmission_id: Mapped[str] = mapped_column(String(100), unique=True)
    event_type: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default=WebhookStatus.RECEIVED)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

`app/models/audit_log.py`

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor_type: Mapped[str] = mapped_column(String(10))  # USER | SERVICE | SYSTEM
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`app/models/__init__.py`

```python
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.enums import (
    BillingCycle,
    FirstPaymentType,
    PaymentStatus,
    PaymentType,
    PlanStatus,
    ServiceStatus,
    SubscriptionStatus,
    UserRole,
    UserStatus,
    WebhookStatus,
)
from app.models.payment import Payment
from app.models.plan import Plan
from app.models.service import Service
from app.models.subscription import Subscription
from app.models.user import PasswordSetupToken, User
from app.models.webhook_event import WebhookEvent

__all__ = [
    "AuditLog", "Base", "BillingCycle", "FirstPaymentType", "Payment",
    "PaymentStatus", "PaymentType", "Plan", "PlanStatus", "PasswordSetupToken",
    "Service", "ServiceStatus", "Subscription", "SubscriptionStatus", "User",
    "UserRole", "UserStatus", "WebhookEvent", "WebhookStatus",
]
```

- [ ] **Step 4: Alembic 설정**

```bash
uv run alembic init -t async alembic
```

`alembic.ini`에서 `sqlalchemy.url` 줄을 다음으로 교체:

```ini
sqlalchemy.url = postgresql+asyncpg://payment:payment@localhost:5433/payment
```

`alembic/env.py`에서 `target_metadata = None` 부분을 다음으로 교체:

```python
from app.models import Base  # noqa: E402

target_metadata = Base.metadata
```

(`env.py` 상단에 `import app.models`가 동작하도록 프로젝트 루트가 sys.path에 있어야 함 —
alembic을 `uv run alembic`으로 루트에서 실행하면 기본 동작함. 안 되면 env.py 상단에
`import sys; sys.path.insert(0, ".")` 추가.)

- [ ] **Step 5: 초기 마이그레이션 생성 + 검증**

```bash
uv run alembic revision --autogenerate -m "initial schema"
grep -l "postgresql_where" alembic/versions/*initial*.py
```
Expected: 파일 경로 출력 (partial unique index 포함 확인).

만약 grep이 비면 생성된 마이그레이션의 `op.create_index("uq_subscriptions_one_per_user", ...)` 호출에 아래 인자를 추가:

```python
    postgresql_where=sa.text("status IN ('ACTIVE','PAST_DUE','CANCELED')"),
    unique=True,
```

적용:

```bash
uv run alembic upgrade head
```
Expected: 에러 없이 완료

- [ ] **Step 6: 모델 무결성 테스트 작성** — `tests/integration/test_models.py`

(이 테스트는 Task 5의 conftest 픽스처가 필요하므로, **Task 5와 함께 실행 확인**한다.
파일은 지금 작성해 둔다.)

```python
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.clock import utcnow
from app.models import Plan, Service, Subscription
from app.services.billing_math import compute_period_end


async def _mk_service(db, name="svc-a"):
    svc = Service(name=name, allowed_ips=["127.0.0.1"], manager_email=f"{name}@x.com",
                  api_key_hash=f"hash-{name}", hmac_secret_encrypted="enc")
    db.add(svc)
    await db.flush()
    return svc


async def _mk_plan(db, svc):
    plan = Plan(service_id=svc.id, name="basic", price=10000, billing_cycle="MONTH")
    db.add(plan)
    await db.flush()
    return plan


def _mk_sub(svc, plan, status="ACTIVE"):
    now = utcnow()
    return Subscription(
        service_id=svc.id, plan_id=plan.id, external_user_id="u1",
        customer_key="ck-1", status=status,
        current_period_start=now,
        current_period_end=compute_period_end(now, "MONTH"),
        next_billing_at=compute_period_end(now, "MONTH"),
    )


async def test_one_subscription_per_service_user_enforced_by_db(db):
    svc = await _mk_service(db)
    plan = await _mk_plan(db, svc)
    db.add(_mk_sub(svc, plan, "ACTIVE"))
    await db.flush()
    db.add(_mk_sub(svc, plan, "CANCELED"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_expired_subscription_allows_resubscribe(db):
    svc = await _mk_service(db, "svc-b")
    plan = await _mk_plan(db, svc)
    db.add(_mk_sub(svc, plan, "EXPIRED"))
    await db.flush()
    db.add(_mk_sub(svc, plan, "ACTIVE"))
    await db.flush()  # 에러 없어야 함
```

- [ ] **Step 7: Commit**

```bash
git add app/core/errors.py app/core/db.py app/models alembic alembic.ini tests/integration
git commit -m "feat: 도메인 에러/DB 모델/초기 마이그레이션"
```

---

### Task 5: 테스트 인프라 (conftest, 팩토리, 클린업)

**Files:**
- Create: `tests/conftest.py`, `tests/factories.py`, `tests/helpers.py`
- Create: `tests/integration/conftest.py`
- Test: Task 4의 `tests/integration/test_models.py` 실행으로 검증

**의도:** 통합 테스트는 실제 PG(payment_test DB)+Redis(db 15)를 쓴다. 매 테스트 후
TRUNCATE/flushdb. 토스는 FakeTossClient(Task 7), 이메일은 RecordingEmailSender(Task 8).

- [ ] **Step 1: 루트 conftest 작성** — `tests/conftest.py`

```python
import base64
import os
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.crypto import AesGcmCipher
from app.models import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://payment:payment@localhost:5433/payment_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/15")
TEST_ENCRYPTION_KEY = base64.b64encode(b"\x01" * 32).decode()


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
        encryption_key=TEST_ENCRYPTION_KEY,
        toss_secret_key="test_sk_dummy",
        scheduler_enabled=False,
        webhook_ip_check_enabled=True,
        toss_webhook_allowed_ips=["127.0.0.1"],  # httpx ASGITransport 클라이언트 IP
        _env_file=None,  # .env 무시 — 테스트 격리
    )


@pytest.fixture(scope="session")
def cipher(settings) -> AesGcmCipher:
    return AesGcmCipher(settings.encryption_key)


@pytest.fixture(scope="session")
async def engine(settings) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
async def redis_client(settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def clean_db(engine) -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {names} CASCADE"))


@pytest.fixture
async def clean_redis(settings) -> AsyncIterator[None]:
    yield
    client = Redis.from_url(settings.redis_url)
    await client.flushdb()
    await client.aclose()
```

- [ ] **Step 2: 통합 테스트 자동 클린업** — `tests/integration/conftest.py`

```python
import pytest


@pytest.fixture(autouse=True)
def _auto_clean(clean_db, clean_redis):
    """통합 테스트는 매 테스트 후 DB/Redis 초기화."""
```

- [ ] **Step 3: 팩토리 작성** — `tests/factories.py`

(registry 서비스(Task 9) 전에도 쓸 수 있도록 모델 직접 생성. API 키/시크릿 평문도 반환.)

```python
import uuid

from app.core.clock import utcnow
from app.core.crypto import AesGcmCipher
from app.core.security import (
    generate_hmac_secret,
    generate_service_api_key,
    hash_password,
    sha256_hex,
)
from app.models import Plan, Service, Subscription, User
from app.services.billing_math import compute_period_end


async def create_service(db, cipher: AesGcmCipher, *, name=None,
                         allowed_ips=None, manager_email=None):
    """반환: (Service, api_key 평문, hmac_secret 평문)"""
    name = name or f"svc-{uuid.uuid4().hex[:8]}"
    api_key = generate_service_api_key()
    secret = generate_hmac_secret()
    svc = Service(
        name=name,
        allowed_ips=allowed_ips if allowed_ips is not None else ["127.0.0.1"],
        manager_email=manager_email or f"{name}@medisolveai.com",
        api_key_hash=sha256_hex(api_key),
        hmac_secret_encrypted=cipher.encrypt(secret),
    )
    db.add(svc)
    await db.commit()
    return svc, api_key, secret


async def create_plan(db, service, *, name="기본 요금제", price=10000,
                      billing_cycle="MONTH", cycle_days=None,
                      first_payment_type="NONE", first_payment_value=None,
                      status="ACTIVE"):
    plan = Plan(service_id=service.id, name=name, price=price,
                billing_cycle=billing_cycle, cycle_days=cycle_days,
                first_payment_type=first_payment_type,
                first_payment_value=first_payment_value, status=status)
    db.add(plan)
    await db.commit()
    return plan


async def create_subscription(db, cipher, service, plan, *, external_user_id="user-1",
                              status="ACTIVE", billing_key="bk_test_1",
                              retry_count=0, period_start=None, period_end=None,
                              next_billing_at=None, customer_key=None):
    start = period_start or utcnow()
    end = period_end or compute_period_end(start, plan.billing_cycle, plan.cycle_days)
    sub = Subscription(
        service_id=service.id, plan_id=plan.id, external_user_id=external_user_id,
        customer_key=customer_key or f"ck-{uuid.uuid4()}",
        billing_key_encrypted=cipher.encrypt(billing_key) if billing_key else None,
        billing_key_hash=sha256_hex(billing_key) if billing_key else None,
        card_info={"number": "1234-****-****-5678", "issuerCode": "61"},
        status=status, current_period_start=start, current_period_end=end,
        next_billing_at=end if next_billing_at is None else next_billing_at,
        retry_count=retry_count,
    )
    db.add(sub)
    await db.commit()
    return sub


async def create_user(db, *, email=None, password="Password123!", role="SYSTEM_ADMIN",
                      service_id=None, status="ACTIVE"):
    """반환: (User, password 평문)"""
    user = User(email=email or f"u-{uuid.uuid4().hex[:8]}@medisolveai.com",
                password_hash=hash_password(password), role=role,
                service_id=service_id, status=status)
    db.add(user)
    await db.commit()
    return user, password
```

- [ ] **Step 4: HMAC 서명 헬퍼 작성** — `tests/helpers.py`

```python
import json
import time
import uuid

from app.core.security import sign_request


def signed_headers(api_key: str, secret: str, method: str, path: str,
                   body: bytes = b"", *, timestamp: str | None = None,
                   nonce: str | None = None, signature: str | None = None) -> dict:
    ts = timestamp if timestamp is not None else str(int(time.time()))
    nc = nonce if nonce is not None else str(uuid.uuid4())
    sig = signature if signature is not None else sign_request(
        secret, method, path, ts, nc, body)
    return {
        "X-Service-Key": api_key,
        "X-Timestamp": ts,
        "X-Nonce": nc,
        "X-Signature": sig,
        "Content-Type": "application/json",
    }


async def api_request(client, method: str, path: str, api_key: str, secret: str,
                      json_body: dict | None = None, **header_overrides):
    body = json.dumps(json_body).encode() if json_body is not None else b""
    headers = signed_headers(api_key, secret, method, path, body, **header_overrides)
    return await client.request(method, path, content=body or None, headers=headers)
```

- [ ] **Step 5: 모델 테스트 실행 (Task 4 Step 6 검증)**

주의: `tests/integration/test_models.py`는 `app.services.billing_math`를 import한다 —
Task 6 완료 전이면 임시로 해당 import를 `from datetime import timedelta` +
`compute_period_end(now, "MONTH")` 호출을 `now + timedelta(days=30)`로 바꿔 실행해도 된다.
**Task 6 완료 후 원복할 것.** (순서대로 실행한다면 Task 6을 먼저 끝내고 돌려도 무방)

```bash
docker compose up -d
uv run pytest tests/integration/test_models.py -v
```
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/factories.py tests/helpers.py tests/integration
git commit -m "test: 통합 테스트 인프라(실DB/Redis, 팩토리, HMAC 헬퍼)"
```

---

### Task 6: 기간/금액 계산 (billing_math)

**Files:**
- Create: `app/services/__init__.py`, `app/services/billing_math.py`
- Test: `tests/unit/test_billing_math.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/unit/test_billing_math.py`

```python
from datetime import UTC, datetime

import pytest

from app.core.errors import InputValidationError
from app.services.billing_math import compute_first_amount, compute_period_end


def dt(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


class TestPeriodEnd:
    def test_month_normal(self):
        assert compute_period_end(dt(2026, 6, 5), "MONTH") == dt(2026, 7, 5)

    def test_month_end_clamps(self):
        # 1/31 + 1개월 → 2/28 (월말 클램프)
        assert compute_period_end(dt(2026, 1, 31), "MONTH") == dt(2026, 2, 28)

    def test_month_end_leap_year(self):
        assert compute_period_end(dt(2024, 1, 31), "MONTH") == dt(2024, 2, 29)

    def test_year(self):
        assert compute_period_end(dt(2026, 6, 5), "YEAR") == dt(2027, 6, 5)

    def test_year_leap_day(self):
        assert compute_period_end(dt(2024, 2, 29), "YEAR") == dt(2025, 2, 28)

    def test_week(self):
        assert compute_period_end(dt(2026, 6, 5), "WEEK") == dt(2026, 6, 12)

    def test_day_with_cycle_days(self):
        assert compute_period_end(dt(2026, 6, 5), "DAY", 10) == dt(2026, 6, 15)

    def test_day_requires_cycle_days(self):
        with pytest.raises(InputValidationError):
            compute_period_end(dt(2026, 6, 5), "DAY", None)
        with pytest.raises(InputValidationError):
            compute_period_end(dt(2026, 6, 5), "DAY", 0)

    def test_unknown_cycle_rejected(self):
        with pytest.raises(InputValidationError):
            compute_period_end(dt(2026, 6, 5), "HOUR")


class TestFirstAmount:
    def test_none_is_full_price(self):
        assert compute_first_amount(10000, "NONE", None) == 10000

    def test_free(self):
        assert compute_first_amount(10000, "FREE", None) == 0

    def test_discount_amount(self):
        assert compute_first_amount(10000, "DISCOUNT_AMOUNT", 3000) == 7000

    def test_discount_amount_floors_at_zero(self):
        assert compute_first_amount(10000, "DISCOUNT_AMOUNT", 99999) == 0

    def test_discount_percent(self):
        assert compute_first_amount(10000, "DISCOUNT_PERCENT", 30) == 7000

    def test_discount_percent_rounds_down_remainder(self):
        assert compute_first_amount(9999, "DISCOUNT_PERCENT", 33) == 6700  # 9999-3299

    def test_discount_percent_bounds(self):
        with pytest.raises(InputValidationError):
            compute_first_amount(10000, "DISCOUNT_PERCENT", 101)
        with pytest.raises(InputValidationError):
            compute_first_amount(10000, "DISCOUNT_PERCENT", -1)

    def test_unknown_type_rejected(self):
        with pytest.raises(InputValidationError):
            compute_first_amount(10000, "BOGOF", None)
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/unit/test_billing_math.py -v
```
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: 구현** — `app/services/billing_math.py` (+ 빈 `app/services/__init__.py`)

```python
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from app.core.errors import InputValidationError
from app.models.enums import BillingCycle, FirstPaymentType


def compute_period_end(start: datetime, cycle: str, cycle_days: int | None = None) -> datetime:
    """구독 기간 종료일 계산. MONTH/YEAR는 월말 클램프(relativedelta)."""
    if cycle == BillingCycle.YEAR:
        return start + relativedelta(years=1)
    if cycle == BillingCycle.MONTH:
        return start + relativedelta(months=1)
    if cycle == BillingCycle.WEEK:
        return start + timedelta(weeks=1)
    if cycle == BillingCycle.DAY:
        if not cycle_days or cycle_days < 1:
            raise InputValidationError("DAY 주기는 cycle_days(1 이상)가 필요합니다")
        return start + timedelta(days=cycle_days)
    raise InputValidationError(f"지원하지 않는 결제 주기입니다: {cycle}")


def compute_first_amount(price: int, first_payment_type: str,
                         first_payment_value: int | None) -> int:
    """첫 구독 결제 금액. 금액은 항상 서버가 계산한다(외부 입력 금지)."""
    if first_payment_type == FirstPaymentType.NONE:
        return price
    if first_payment_type == FirstPaymentType.FREE:
        return 0
    if first_payment_type == FirstPaymentType.DISCOUNT_AMOUNT:
        return max(0, price - (first_payment_value or 0))
    if first_payment_type == FirstPaymentType.DISCOUNT_PERCENT:
        value = first_payment_value or 0
        if not 0 <= value <= 100:
            raise InputValidationError("할인율은 0~100 사이여야 합니다")
        return price - (price * value) // 100
    raise InputValidationError(f"지원하지 않는 첫결제 유형입니다: {first_payment_type}")
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/unit/test_billing_math.py -v
```
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add app/services tests/unit/test_billing_math.py
git commit -m "feat: 구독 기간/첫결제 금액 계산"
```

---

### Task 7: 토스페이먼츠 클라이언트 (HTTP 구현 + Fake)

**Files:**
- Create: `app/toss/__init__.py`, `app/toss/types.py`, `app/toss/errors.py`, `app/toss/client.py`, `app/toss/fake.py`
- Test: `tests/unit/test_toss_client.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/unit/test_toss_client.py`

```python
import base64

import httpx
import pytest
import respx

from app.toss.client import HttpTossClient
from app.toss.errors import TossError, TossTimeoutError

BASE = "https://api.tosspayments.test"


@pytest.fixture
async def toss():
    client = HttpTossClient("test_sk_abc", base_url=BASE)
    yield client
    await client.aclose()


@respx.mock
async def test_issue_billing_key(toss):
    route = respx.post(f"{BASE}/v1/billing/authorizations/issue").mock(
        return_value=httpx.Response(200, json={
            "billingKey": "bk_1", "method": "카드", "customerKey": "ck-1",
            "card": {"number": "1234****", "issuerCode": "61"},
        }))
    result = await toss.issue_billing_key("auth-key-1", "ck-1")
    assert result.billing_key == "bk_1"
    assert result.card == {"number": "1234****", "issuerCode": "61"}
    sent = route.calls.last.request
    # Basic base64("test_sk_abc:") 인증 헤더 확인
    assert sent.headers["authorization"] == \
        "Basic " + base64.b64encode(b"test_sk_abc:").decode()


@respx.mock
async def test_charge_sends_idempotency_key_and_parses(toss):
    route = respx.post(f"{BASE}/v1/billing/bk_1").mock(
        return_value=httpx.Response(200, json={
            "paymentKey": "pay_1", "orderId": "order-1", "status": "DONE",
            "approvedAt": "2026-06-05T10:00:00+09:00", "totalAmount": 10000,
        }))
    result = await toss.charge("bk_1", "ck-1", 10000, "order-1", "기본 요금제", "idem-1")
    assert result.payment_key == "pay_1"
    assert result.status == "DONE"
    sent = route.calls.last.request
    assert sent.headers["idempotency-key"] == "idem-1"
    import json
    body = json.loads(sent.content)
    assert body == {"amount": 10000, "customerKey": "ck-1",
                    "orderId": "order-1", "orderName": "기본 요금제"}


@respx.mock
async def test_error_response_raises_toss_error(toss):
    respx.post(f"{BASE}/v1/billing/bk_1").mock(
        return_value=httpx.Response(400, json={
            "code": "EXCEED_MAX_AMOUNT", "message": "한도 초과"}))
    with pytest.raises(TossError) as exc:
        await toss.charge("bk_1", "ck-1", 10000, "order-1", "요금제", "idem-2")
    assert exc.value.code == "EXCEED_MAX_AMOUNT"
    assert exc.value.http_status == 400


@respx.mock
async def test_timeout_raises_toss_timeout(toss):
    respx.post(f"{BASE}/v1/billing/bk_1").mock(side_effect=httpx.ReadTimeout("timeout"))
    with pytest.raises(TossTimeoutError):
        await toss.charge("bk_1", "ck-1", 10000, "order-1", "요금제", "idem-3")


@respx.mock
async def test_get_payment_by_order_id_found_and_missing(toss):
    respx.get(f"{BASE}/v1/payments/orders/order-1").mock(
        return_value=httpx.Response(200, json={
            "paymentKey": "pay_1", "orderId": "order-1", "status": "DONE"}))
    respx.get(f"{BASE}/v1/payments/orders/order-x").mock(
        return_value=httpx.Response(404, json={
            "code": "NOT_FOUND_PAYMENT", "message": "없음"}))
    found = await toss.get_payment_by_order_id("order-1")
    assert found is not None and found.status == "DONE"
    assert await toss.get_payment_by_order_id("order-x") is None


@respx.mock
async def test_delete_billing_key(toss):
    route = respx.delete(f"{BASE}/v1/billing/bk_1").mock(
        return_value=httpx.Response(200, json={}))
    await toss.delete_billing_key("bk_1")
    assert route.called
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/unit/test_toss_client.py -v
```
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: 구현** — `app/toss/types.py`

```python
from dataclasses import dataclass, field


@dataclass
class BillingKeyResult:
    billing_key: str
    method: str | None
    card: dict | None
    raw: dict = field(default_factory=dict)


@dataclass
class ChargeResult:
    payment_key: str
    order_id: str
    status: str
    approved_at: str | None = None
    raw: dict = field(default_factory=dict)
```

`app/toss/errors.py`

```python
class TossError(Exception):
    """토스 API 에러 응답."""

    def __init__(self, code: str, message: str, http_status: int = 0) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.http_status = http_status


class TossTimeoutError(TossError):
    """타임아웃/네트워크 단절 — 결제 성공 여부 불명. orderId 재조회 필요."""

    def __init__(self, message: str = "토스 API 응답 시간 초과") -> None:
        super().__init__("TIMEOUT", message, 0)
```

`app/toss/client.py`

```python
import base64
from typing import Protocol

import httpx

from app.toss.errors import TossError, TossTimeoutError
from app.toss.types import BillingKeyResult, ChargeResult


class TossClient(Protocol):
    async def issue_billing_key(self, auth_key: str, customer_key: str) -> BillingKeyResult: ...

    async def charge(self, billing_key: str, customer_key: str, amount: int,
                     order_id: str, order_name: str, idempotency_key: str) -> ChargeResult: ...

    async def get_payment_by_order_id(self, order_id: str) -> ChargeResult | None: ...

    async def delete_billing_key(self, billing_key: str) -> None: ...


def _charge_result(data: dict) -> ChargeResult:
    return ChargeResult(
        payment_key=data.get("paymentKey", ""),
        order_id=data.get("orderId", ""),
        status=data.get("status", ""),
        approved_at=data.get("approvedAt"),
        raw=data,
    )


class HttpTossClient:
    """토스페이먼츠 코어 API 클라이언트. 자동결제 승인은 최대 60초(명세)."""

    def __init__(self, secret_key: str, base_url: str = "https://api.tosspayments.com") -> None:
        token = base64.b64encode(f"{secret_key}:".encode()).decode()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Basic {token}"},
            timeout=httpx.Timeout(60.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, json: dict | None = None,
                       idempotency_key: str | None = None) -> dict:
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            resp = await self._client.request(method, path, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise TossTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise TossError("NETWORK_ERROR", str(exc)) from exc
        if resp.status_code >= 400:
            try:
                err = resp.json()
            except ValueError:
                err = {}
            raise TossError(err.get("code", "UNKNOWN"),
                            err.get("message", "토스 API 오류"), resp.status_code)
        if not resp.content:
            return {}
        return resp.json()

    async def issue_billing_key(self, auth_key: str, customer_key: str) -> BillingKeyResult:
        data = await self._request("POST", "/v1/billing/authorizations/issue",
                                   json={"authKey": auth_key, "customerKey": customer_key})
        return BillingKeyResult(billing_key=data["billingKey"], method=data.get("method"),
                                card=data.get("card"), raw=data)

    async def charge(self, billing_key: str, customer_key: str, amount: int,
                     order_id: str, order_name: str, idempotency_key: str) -> ChargeResult:
        data = await self._request(
            "POST", f"/v1/billing/{billing_key}",
            json={"amount": amount, "customerKey": customer_key,
                  "orderId": order_id, "orderName": order_name},
            idempotency_key=idempotency_key)
        return _charge_result(data)

    async def get_payment_by_order_id(self, order_id: str) -> ChargeResult | None:
        try:
            data = await self._request("GET", f"/v1/payments/orders/{order_id}")
        except TossError as exc:
            if exc.http_status == 404:
                return None
            raise
        return _charge_result(data)

    async def delete_billing_key(self, billing_key: str) -> None:
        await self._request("DELETE", f"/v1/billing/{billing_key}")
```

`app/toss/fake.py`

```python
import itertools

from app.toss.errors import TossError, TossTimeoutError
from app.toss.types import BillingKeyResult, ChargeResult


class FakeTossClient:
    """테스트용 토스 클라이언트. 호출 기록 + 실패 주입."""

    def __init__(self) -> None:
        self.issued: list[dict] = []
        self.charges: list[dict] = []
        self.deleted: list[str] = []
        self.fail_issue_with: TossError | None = None
        self.fail_charge_with: TossError | None = None       # 상시 실패
        self.charge_failure_queue: list[TossError] = []      # 소진형 실패(앞에서부터 1회씩)
        self.succeed_despite_timeout: bool = False           # 타임아웃이지만 실제 승인된 상황 재현
        self.payments_by_order: dict[str, ChargeResult] = {}  # get_payment_by_order_id 응답
        self._seq = itertools.count(1)

    @staticmethod
    def _result_for(order_id: str, amount: int) -> ChargeResult:
        return ChargeResult(payment_key=f"pay_{order_id}", order_id=order_id,
                            status="DONE", approved_at="2026-06-05T10:00:00+09:00",
                            raw={"paymentKey": f"pay_{order_id}", "orderId": order_id,
                                 "status": "DONE", "totalAmount": amount})

    async def issue_billing_key(self, auth_key: str, customer_key: str) -> BillingKeyResult:
        if self.fail_issue_with is not None:
            raise self.fail_issue_with
        billing_key = f"bk_{next(self._seq)}"
        self.issued.append({"auth_key": auth_key, "customer_key": customer_key,
                            "billing_key": billing_key})
        return BillingKeyResult(
            billing_key=billing_key, method="카드",
            card={"number": "1234-****-****-5678", "issuerCode": "61"},
            raw={"billingKey": billing_key})

    async def charge(self, billing_key: str, customer_key: str, amount: int,
                     order_id: str, order_name: str, idempotency_key: str) -> ChargeResult:
        self.charges.append({"billing_key": billing_key, "customer_key": customer_key,
                             "amount": amount, "order_id": order_id,
                             "order_name": order_name, "idempotency_key": idempotency_key})
        if self.charge_failure_queue:
            error = self.charge_failure_queue.pop(0)
            if self.succeed_despite_timeout and isinstance(error, TossTimeoutError):
                # 타임아웃으로 응답은 못 받았지만 토스 쪽에선 승인된 케이스
                self.payments_by_order[order_id] = self._result_for(order_id, amount)
            raise error
        if self.fail_charge_with is not None:
            raise self.fail_charge_with
        result = self._result_for(order_id, amount)
        self.payments_by_order[order_id] = result
        return result

    async def get_payment_by_order_id(self, order_id: str) -> ChargeResult | None:
        return self.payments_by_order.get(order_id)

    async def delete_billing_key(self, billing_key: str) -> None:
        self.deleted.append(billing_key)
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/unit/test_toss_client.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/toss tests/unit/test_toss_client.py
git commit -m "feat: 토스페이먼츠 클라이언트(HTTP+Fake, 멱등키/타임아웃 처리)"
```

---

### Task 8: 이메일 발송 추상화 + 감사 로그 서비스

**Files:**
- Create: `app/notifications/__init__.py`, `app/notifications/email.py`, `app/services/audit.py`
- Test: `tests/integration/test_audit.py`

- [ ] **Step 1: 구현** — `app/notifications/email.py` (+ 빈 `app/notifications/__init__.py`)

```python
import logging
from typing import Protocol

logger = logging.getLogger("payment.email")


class EmailSender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    """개발/로컬용 — 콘솔(로그)로 출력. 운영 SMTP 구현체는 추후 교체."""

    async def send(self, to: str, subject: str, body: str) -> None:
        logger.info("EMAIL to=%s subject=%s\n%s", to, subject, body)


class RecordingEmailSender:
    """테스트용 — 발송 내역 기록."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})
```

`app/services/audit.py`

```python
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def record_audit(db: AsyncSession, *, actor_type: str, action: str,
                       actor_user_id: uuid.UUID | None = None,
                       target_type: str | None = None, target_id: str | None = None,
                       detail: dict | None = None, ip_address: str | None = None) -> None:
    """감사 로그 한 건 추가. commit은 호출자가 묶어서 수행."""
    db.add(AuditLog(actor_type=actor_type, action=action, actor_user_id=actor_user_id,
                    target_type=target_type, target_id=target_id,
                    detail=detail, ip_address=ip_address))
```

- [ ] **Step 2: 테스트 작성 + 실행** — `tests/integration/test_audit.py`

```python
from sqlalchemy import select

from app.models import AuditLog
from app.notifications.email import RecordingEmailSender
from app.services.audit import record_audit


async def test_record_audit_persists(db):
    await record_audit(db, actor_type="SYSTEM", action="test.action",
                       target_type="service", target_id="t-1",
                       detail={"k": "v"}, ip_address="127.0.0.1")
    await db.commit()
    row = await db.scalar(select(AuditLog).where(AuditLog.action == "test.action"))
    assert row is not None
    assert row.detail == {"k": "v"}


async def test_recording_email_sender():
    sender = RecordingEmailSender()
    await sender.send("a@b.com", "제목", "본문")
    assert sender.sent == [{"to": "a@b.com", "subject": "제목", "body": "본문"}]
```

```bash
uv run pytest tests/integration/test_audit.py -v
```
Expected: 2 passed

- [ ] **Step 3: Commit**

```bash
git add app/notifications app/services/audit.py tests/integration/test_audit.py
git commit -m "feat: 이메일 추상화(콘솔/기록)와 감사 로그"
```

---

### Task 9: 서비스 등록/키 관리 (registry)

**Files:**
- Create: `app/services/registry.py`
- Test: `tests/integration/test_registry.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_registry.py`

```python
import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, InputValidationError
from app.core.security import sha256_hex
from app.models import PasswordSetupToken, Service, User
from app.notifications.email import RecordingEmailSender
from app.services.registry import (
    delete_service,
    register_service,
    rotate_keys,
    set_service_status,
    update_allowed_ips,
)
from tests.factories import create_plan, create_service, create_subscription


@pytest.fixture
def email():
    return RecordingEmailSender()


async def test_register_service_creates_keys_user_and_token(db, cipher, email):
    creds = await register_service(
        db, cipher, email, name="mediness", allowed_ips=["10.0.0.1"],
        manager_email="mgr@medisolveai.com", base_url="http://localhost:8000")
    assert creds.api_key.startswith("svc_")
    assert len(creds.hmac_secret) >= 48

    svc = await db.scalar(select(Service).where(Service.name == "mediness"))
    assert svc.api_key_hash == sha256_hex(creds.api_key)
    assert cipher.decrypt(svc.hmac_secret_encrypted) == creds.hmac_secret

    user = await db.scalar(select(User).where(User.email == "mgr@medisolveai.com"))
    assert user.role == "SERVICE_MANAGER"
    assert user.status == "PENDING"
    assert user.service_id == svc.id

    token_row = await db.scalar(select(PasswordSetupToken).where(
        PasswordSetupToken.user_id == user.id))
    assert token_row is not None
    assert len(email.sent) == 1
    assert creds.setup_token in email.sent[0]["body"]  # 설정 링크 포함


async def test_register_duplicate_name_conflicts(db, cipher, email):
    await register_service(db, cipher, email, name="dup", allowed_ips=["10.0.0.1"],
                           manager_email="a@x.com", base_url="")
    with pytest.raises(ConflictError):
        await register_service(db, cipher, email, name="dup", allowed_ips=["10.0.0.1"],
                               manager_email="b@x.com", base_url="")


async def test_register_rejects_invalid_ip(db, cipher, email):
    with pytest.raises(InputValidationError):
        await register_service(db, cipher, email, name="bad-ip",
                               allowed_ips=["not-an-ip"], manager_email="a@x.com", base_url="")


async def test_rotate_keys_invalidates_old(db, cipher, email):
    creds = await register_service(db, cipher, email, name="rot", allowed_ips=["10.0.0.1"],
                                   manager_email="r@x.com", base_url="")
    new_api_key, new_secret = await rotate_keys(db, cipher, creds.service.id)
    svc = await db.get(Service, creds.service.id)
    assert svc.api_key_hash == sha256_hex(new_api_key)
    assert svc.api_key_hash != sha256_hex(creds.api_key)
    assert cipher.decrypt(svc.hmac_secret_encrypted) == new_secret


async def test_update_allowed_ips(db, cipher, email):
    creds = await register_service(db, cipher, email, name="ips", allowed_ips=["10.0.0.1"],
                                   manager_email="i@x.com", base_url="")
    await update_allowed_ips(db, creds.service.id, ["10.0.0.2", "10.0.0.3"])
    svc = await db.get(Service, creds.service.id)
    assert svc.allowed_ips == ["10.0.0.2", "10.0.0.3"]


async def test_delete_service_blocked_when_subscription_exists(db, cipher, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)
    with pytest.raises(ConflictError):
        await delete_service(db, svc.id)


async def test_delete_service_without_subscriptions(db, cipher, email):
    creds = await register_service(db, cipher, email, name="deletable",
                                   allowed_ips=["10.0.0.1"], manager_email="d@x.com", base_url="")
    await delete_service(db, creds.service.id)
    assert await db.get(Service, creds.service.id) is None


async def test_set_service_status(db, cipher, email):
    svc, _, _ = await create_service(db, cipher)
    await set_service_status(db, svc.id, "INACTIVE")
    assert (await db.get(Service, svc.id)).status == "INACTIVE"
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/integration/test_registry.py -v
```
Expected: FAIL (`ModuleNotFoundError: app.services.registry`)

- [ ] **Step 3: 구현** — `app/services/registry.py`

```python
import ipaddress
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.crypto import AesGcmCipher
from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.core.security import (
    generate_hmac_secret,
    generate_service_api_key,
    generate_setup_token,
    sha256_hex,
)
from app.models import (
    PasswordSetupToken,
    Plan,
    Service,
    ServiceStatus,
    Subscription,
    User,
    UserRole,
    UserStatus,
)
from app.notifications.email import EmailSender
from app.services.audit import record_audit

SETUP_TOKEN_TTL = timedelta(hours=48)


@dataclass
class IssuedCredentials:
    service: Service
    api_key: str
    hmac_secret: str
    setup_token: str | None  # 신규 담당자 계정이 만들어졌을 때만


def _validate_ips(ips: list[str]) -> list[str]:
    if not ips:
        raise InputValidationError("허용 IP를 1개 이상 등록해야 합니다")
    for ip in ips:
        try:
            ipaddress.ip_address(ip)
        except ValueError as exc:
            raise InputValidationError(f"유효하지 않은 IP: {ip}") from exc
    return ips


async def register_service(db: AsyncSession, cipher: AesGcmCipher, email_sender: EmailSender,
                           *, name: str, allowed_ips: list[str], manager_email: str,
                           base_url: str, actor_user_id: uuid.UUID | None = None) -> IssuedCredentials:
    if not name or not name.strip():
        raise InputValidationError("서비스명은 필수입니다")
    _validate_ips(allowed_ips)
    if await db.scalar(select(Service).where(Service.name == name)):
        raise ConflictError("이미 등록된 서비스명입니다")

    api_key = generate_service_api_key()
    hmac_secret = generate_hmac_secret()
    service = Service(name=name.strip(), allowed_ips=allowed_ips,
                      manager_email=manager_email,
                      api_key_hash=sha256_hex(api_key),
                      hmac_secret_encrypted=cipher.encrypt(hmac_secret))
    db.add(service)
    await db.flush()

    setup_token: str | None = None
    user = await db.scalar(select(User).where(User.email == manager_email))
    if user is None:
        user = User(email=manager_email, role=UserRole.SERVICE_MANAGER,
                    service_id=service.id, status=UserStatus.PENDING)
        db.add(user)
        await db.flush()
        setup_token = generate_setup_token()
        db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(setup_token),
                                  expires_at=utcnow() + SETUP_TOKEN_TTL))
        await email_sender.send(
            manager_email, "[결제시스템] 관리자 계정 설정 안내",
            f"안녕하세요. {name} 서비스의 구독/결제 관리자 계정이 생성되었습니다.\n"
            f"아래 링크에서 비밀번호를 설정해주세요 (48시간 유효):\n"
            f"{base_url}/admin/setup-password?token={setup_token}")

    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.register", target_type="service",
                       target_id=str(service.id), detail={"name": name})
    await db.commit()
    return IssuedCredentials(service=service, api_key=api_key,
                             hmac_secret=hmac_secret, setup_token=setup_token)


async def _get_service(db: AsyncSession, service_id: uuid.UUID) -> Service:
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    return service


async def rotate_keys(db: AsyncSession, cipher: AesGcmCipher, service_id: uuid.UUID,
                      actor_user_id: uuid.UUID | None = None) -> tuple[str, str]:
    """API 키/HMAC secret 재발급. 기존 키는 즉시 무효."""
    service = await _get_service(db, service_id)
    api_key = generate_service_api_key()
    hmac_secret = generate_hmac_secret()
    service.api_key_hash = sha256_hex(api_key)
    service.hmac_secret_encrypted = cipher.encrypt(hmac_secret)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.rotate_keys", target_type="service",
                       target_id=str(service.id))
    await db.commit()
    return api_key, hmac_secret


async def update_allowed_ips(db: AsyncSession, service_id: uuid.UUID, ips: list[str],
                             actor_user_id: uuid.UUID | None = None) -> Service:
    service = await _get_service(db, service_id)
    service.allowed_ips = _validate_ips(ips)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.update_ips", target_type="service",
                       target_id=str(service.id), detail={"ips": ips})
    await db.commit()
    return service


async def set_service_status(db: AsyncSession, service_id: uuid.UUID, status: str,
                             actor_user_id: uuid.UUID | None = None) -> Service:
    if status not in (ServiceStatus.ACTIVE, ServiceStatus.INACTIVE):
        raise InputValidationError(f"유효하지 않은 상태: {status}")
    service = await _get_service(db, service_id)
    service.status = status
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.set_status", target_type="service",
                       target_id=str(service.id), detail={"status": status})
    await db.commit()
    return service


async def delete_service(db: AsyncSession, service_id: uuid.UUID,
                         actor_user_id: uuid.UUID | None = None) -> None:
    """구독 이력이 하나라도 있으면 삭제 불가(스펙 + FK RESTRICT). 비활성화를 권장."""
    service = await _get_service(db, service_id)
    sub_count = await db.scalar(select(func.count()).select_from(Subscription)
                                .where(Subscription.service_id == service_id))
    if sub_count:
        raise ConflictError("구독 이력이 있는 서비스는 삭제할 수 없습니다. 비활성화를 사용하세요.")
    # 요금제 먼저 제거(구독이 없으므로 안전)
    for plan in (await db.scalars(select(Plan).where(Plan.service_id == service_id))).all():
        await db.delete(plan)
    await db.delete(service)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="service.delete", target_type="service",
                       target_id=str(service_id), detail={"name": service.name})
    await db.commit()


async def list_services(db: AsyncSession) -> list[Service]:
    return list((await db.scalars(select(Service).order_by(Service.created_at))).all())
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/integration/test_registry.py -v
```
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/registry.py tests/integration/test_registry.py
git commit -m "feat: 서비스 등록/키 회전/IP 관리/삭제 규칙"
```

---

### Task 10: Admin 인증 서비스 (로그인/잠금/세션/비밀번호 설정)

**Files:**
- Create: `app/services/auth.py`
- Test: `tests/integration/test_auth_service.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_auth_service.py`

```python
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.errors import AuthenticationError, InputValidationError
from app.core.security import sha256_hex, verify_password
from app.models import PasswordSetupToken, User
from app.services import auth
from tests.factories import create_user


async def test_login_success_creates_redis_session(db, redis_client, settings):
    user, password = await create_user(db, role="SYSTEM_ADMIN")
    session_id, logged_in = await auth.login(
        db, redis_client, settings, email=user.email, password=password, ip="127.0.0.1")
    assert logged_in.id == user.id
    data = await auth.get_session(redis_client, settings, session_id)
    assert data["user_id"] == str(user.id)
    assert data["role"] == "SYSTEM_ADMIN"
    assert len(data["csrf_token"]) > 20


async def test_login_wrong_password_generic_error(db, redis_client, settings):
    user, _ = await create_user(db)
    with pytest.raises(AuthenticationError):
        await auth.login(db, redis_client, settings,
                         email=user.email, password="wrong", ip="127.0.0.1")


async def test_login_unknown_email_same_error_shape(db, redis_client, settings):
    with pytest.raises(AuthenticationError):
        await auth.login(db, redis_client, settings,
                         email="ghost@x.com", password="x", ip="127.0.0.1")


async def test_lockout_after_5_failures(db, redis_client, settings):
    user, password = await create_user(db)
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            await auth.login(db, redis_client, settings,
                             email=user.email, password="wrong", ip="127.0.0.1")
    refreshed = await db.get(User, user.id)
    await db.refresh(refreshed)
    assert refreshed.status == "LOCKED"
    # 잠금 중엔 올바른 비밀번호도 거부
    with pytest.raises(AuthenticationError):
        await auth.login(db, redis_client, settings,
                         email=user.email, password=password, ip="127.0.0.1")


async def test_lock_expires_and_allows_login(db, redis_client, settings):
    user, password = await create_user(db, status="LOCKED")
    user.locked_until = utcnow() - timedelta(minutes=1)  # 이미 만료된 잠금
    await db.commit()
    session_id, _ = await auth.login(db, redis_client, settings,
                                     email=user.email, password=password, ip="127.0.0.1")
    assert session_id


async def test_pending_user_cannot_login(db, redis_client, settings):
    user, password = await create_user(db, status="PENDING")
    with pytest.raises(AuthenticationError):
        await auth.login(db, redis_client, settings,
                         email=user.email, password=password, ip="127.0.0.1")


async def test_logout_destroys_session(db, redis_client, settings):
    user, password = await create_user(db)
    session_id, _ = await auth.login(db, redis_client, settings,
                                     email=user.email, password=password, ip="127.0.0.1")
    await auth.logout(redis_client, session_id)
    assert await auth.get_session(redis_client, settings, session_id) is None


async def test_setup_password_with_valid_token(db):
    user, _ = await create_user(db, status="PENDING")
    token = "tok-" + "a" * 30
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()
    await auth.setup_password(db, token=token, password="NewPassword123!")
    await db.refresh(user)
    assert user.status == "ACTIVE"
    assert verify_password("NewPassword123!", user.password_hash)
    # 토큰 재사용 불가
    with pytest.raises(InputValidationError):
        await auth.setup_password(db, token=token, password="Another123!")


async def test_setup_password_rejects_expired_token(db):
    user, _ = await create_user(db, status="PENDING")
    token = "tok-" + "b" * 30
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() - timedelta(hours=1)))
    await db.commit()
    with pytest.raises(InputValidationError):
        await auth.setup_password(db, token=token, password="NewPassword123!")


async def test_setup_password_rejects_weak_password(db):
    user, _ = await create_user(db, status="PENDING")
    token = "tok-" + "c" * 30
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()
    with pytest.raises(InputValidationError):
        await auth.setup_password(db, token=token, password="short")
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/integration/test_auth_service.py -v
```
Expected: FAIL

- [ ] **Step 3: 구현** — `app/services/auth.py`

```python
import secrets
import uuid
from datetime import timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import AuthenticationError, InputValidationError
from app.core.security import hash_password, sha256_hex, verify_password
from app.models import PasswordSetupToken, User, UserStatus
from app.services.audit import record_audit

MAX_FAILED_LOGINS = 5
LOCK_DURATION = timedelta(minutes=15)
SESSION_PREFIX = "session:"
LOGIN_FAILED_MESSAGE = "이메일 또는 비밀번호가 올바르지 않습니다"
MIN_PASSWORD_LENGTH = 10


async def login(db: AsyncSession, redis: Redis, settings: Settings,
                *, email: str, password: str, ip: str) -> tuple[str, User]:
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        raise AuthenticationError(LOGIN_FAILED_MESSAGE)

    now = utcnow()
    if user.status == UserStatus.LOCKED:
        if user.locked_until is not None and user.locked_until > now:
            raise AuthenticationError("계정이 잠겼습니다. 잠시 후 다시 시도해주세요")
        user.status = UserStatus.ACTIVE
        user.failed_login_count = 0
        user.locked_until = None

    if user.status == UserStatus.PENDING:
        raise AuthenticationError("비밀번호 설정이 필요합니다. 안내 메일을 확인해주세요")

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= MAX_FAILED_LOGINS:
            user.status = UserStatus.LOCKED
            user.locked_until = now + LOCK_DURATION
        await record_audit(db, actor_type="USER", actor_user_id=user.id,
                           action="auth.login_failed", ip_address=ip)
        await db.commit()
        raise AuthenticationError(LOGIN_FAILED_MESSAGE)

    user.failed_login_count = 0
    user.locked_until = None
    session_id = secrets.token_urlsafe(32)
    await redis.hset(SESSION_PREFIX + session_id, mapping={
        "user_id": str(user.id),
        "role": user.role,
        "service_id": str(user.service_id) if user.service_id else "",
        "csrf_token": secrets.token_urlsafe(32),
    })
    await redis.expire(SESSION_PREFIX + session_id, settings.session_ttl_seconds)
    await record_audit(db, actor_type="USER", actor_user_id=user.id,
                       action="auth.login", ip_address=ip)
    await db.commit()
    return session_id, user


async def get_session(redis: Redis, settings: Settings, session_id: str) -> dict | None:
    if not session_id:
        return None
    key = SESSION_PREFIX + session_id
    data = await redis.hgetall(key)
    if not data:
        return None
    await redis.expire(key, settings.session_ttl_seconds)  # 유휴 타임아웃 연장
    return data


async def logout(redis: Redis, session_id: str) -> None:
    await redis.delete(SESSION_PREFIX + session_id)


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InputValidationError(f"비밀번호는 {MIN_PASSWORD_LENGTH}자 이상이어야 합니다")


async def setup_password(db: AsyncSession, *, token: str, password: str) -> User:
    """초기 비밀번호 설정/재설정. 토큰은 1회용 + 만료 검증."""
    _validate_password(password)
    row = await db.scalar(select(PasswordSetupToken).where(
        PasswordSetupToken.token_hash == sha256_hex(token),
        PasswordSetupToken.used_at.is_(None)))
    if row is None or row.expires_at < utcnow():
        raise InputValidationError("유효하지 않거나 만료된 토큰입니다")
    user = await db.get(User, row.user_id)
    user.password_hash = hash_password(password)
    user.status = UserStatus.ACTIVE
    user.failed_login_count = 0
    user.locked_until = None
    row.used_at = utcnow()
    await record_audit(db, actor_type="USER", actor_user_id=user.id,
                       action="auth.password_set")
    await db.commit()
    return user


async def get_user(db: AsyncSession, user_id: str) -> User | None:
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        return None
    return await db.get(User, uid)
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/integration/test_auth_service.py -v
```
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/auth.py tests/integration/test_auth_service.py
git commit -m "feat: admin 인증(로그인 잠금/Redis 세션/비밀번호 설정)"
```

---

### Task 11: 요금제 서비스 (plans)

**Files:**
- Create: `app/services/plans.py`
- Test: `tests/integration/test_plans_service.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_plans_service.py`

```python
import pytest

from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.services.plans import archive_plan, create_plan, delete_plan, list_plans, update_plan
from tests.factories import create_service
from tests.factories import create_plan as make_plan
from tests.factories import create_subscription


async def test_create_plan_month(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, service_id=svc.id, name="베이직", price=9900,
                             billing_cycle="MONTH")
    assert plan.id is not None
    assert plan.status == "ACTIVE"
    assert plan.currency == "KRW"


async def test_create_plan_day_requires_cycle_days(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="일단위", price=1000,
                          billing_cycle="DAY")
    plan = await create_plan(db, service_id=svc.id, name="일단위", price=1000,
                             billing_cycle="DAY", cycle_days=15)
    assert plan.cycle_days == 15


async def test_create_plan_non_day_rejects_cycle_days(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000,
                          billing_cycle="MONTH", cycle_days=10)


async def test_create_plan_validates_price_and_discount(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=0, billing_cycle="MONTH")
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000, billing_cycle="MONTH",
                          first_payment_type="DISCOUNT_PERCENT", first_payment_value=150)
    with pytest.raises(InputValidationError):
        await create_plan(db, service_id=svc.id, name="x", price=1000, billing_cycle="MONTH",
                          first_payment_type="DISCOUNT_AMOUNT", first_payment_value=None)


async def test_update_plan(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    updated = await update_plan(db, plan_id=plan.id, service_id=svc.id,
                                name="프로", price=19900)
    assert updated.name == "프로"
    assert updated.price == 19900


async def test_delete_plan_blocked_when_subscription_exists(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)
    with pytest.raises(ConflictError):
        await delete_plan(db, plan_id=plan.id, service_id=svc.id)


async def test_delete_plan_without_subscriptions(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    await delete_plan(db, plan_id=plan.id, service_id=svc.id)
    assert await list_plans(db, service_id=svc.id) == []


async def test_archive_plan_hides_from_active_list(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await make_plan(db, svc)
    await archive_plan(db, plan_id=plan.id, service_id=svc.id)
    assert await list_plans(db, service_id=svc.id, only_active=True) == []
    assert len(await list_plans(db, service_id=svc.id)) == 1


async def test_plan_scoped_to_service(db, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="svc-a2")
    svc_b, _, _ = await create_service(db, cipher, name="svc-b2")
    plan = await make_plan(db, svc_a)
    with pytest.raises(NotFoundError):
        await update_plan(db, plan_id=plan.id, service_id=svc_b.id, name="해킹")
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/integration/test_plans_service.py -v
```
Expected: FAIL

- [ ] **Step 3: 구현** — `app/services/plans.py`

```python
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InputValidationError, NotFoundError
from app.models import (
    BillingCycle,
    FirstPaymentType,
    Plan,
    PlanStatus,
    Subscription,
)
from app.services.audit import record_audit


def _validate_plan_fields(*, price: int, billing_cycle: str, cycle_days: int | None,
                          first_payment_type: str, first_payment_value: int | None) -> None:
    if price <= 0:
        raise InputValidationError("가격은 1원 이상이어야 합니다")
    if billing_cycle not in tuple(BillingCycle):
        raise InputValidationError(f"지원하지 않는 결제 주기입니다: {billing_cycle}")
    if billing_cycle == BillingCycle.DAY:
        if not cycle_days or cycle_days < 1:
            raise InputValidationError("DAY 주기는 cycle_days(1 이상)가 필요합니다")
    elif cycle_days is not None:
        raise InputValidationError("cycle_days는 DAY 주기에서만 사용합니다")
    if first_payment_type not in tuple(FirstPaymentType):
        raise InputValidationError(f"지원하지 않는 첫결제 유형입니다: {first_payment_type}")
    if first_payment_type in (FirstPaymentType.NONE, FirstPaymentType.FREE):
        if first_payment_value is not None:
            raise InputValidationError("첫결제 값은 할인 유형에서만 사용합니다")
    else:
        if first_payment_value is None or first_payment_value < 0:
            raise InputValidationError("할인 값이 필요합니다")
        if first_payment_type == FirstPaymentType.DISCOUNT_PERCENT and first_payment_value > 100:
            raise InputValidationError("할인율은 0~100 사이여야 합니다")


async def create_plan(db: AsyncSession, *, service_id: uuid.UUID, name: str, price: int,
                      billing_cycle: str, cycle_days: int | None = None,
                      first_payment_type: str = "NONE",
                      first_payment_value: int | None = None,
                      actor_user_id: uuid.UUID | None = None) -> Plan:
    if not name or not name.strip():
        raise InputValidationError("요금제 이름은 필수입니다")
    _validate_plan_fields(price=price, billing_cycle=billing_cycle, cycle_days=cycle_days,
                          first_payment_type=first_payment_type,
                          first_payment_value=first_payment_value)
    plan = Plan(service_id=service_id, name=name.strip(), price=price,
                billing_cycle=billing_cycle, cycle_days=cycle_days,
                first_payment_type=first_payment_type,
                first_payment_value=first_payment_value)
    db.add(plan)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.create", target_type="plan",
                       detail={"name": name, "price": price})
    await db.commit()
    return plan


async def _get_plan(db: AsyncSession, plan_id: uuid.UUID, service_id: uuid.UUID) -> Plan:
    plan = await db.get(Plan, plan_id)
    if plan is None or plan.service_id != service_id:
        raise NotFoundError("요금제를 찾을 수 없습니다")
    return plan


async def update_plan(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID,
                      name: str | None = None, price: int | None = None,
                      first_payment_type: str | None = None,
                      first_payment_value: int | None = None,
                      actor_user_id: uuid.UUID | None = None) -> Plan:
    plan = await _get_plan(db, plan_id, service_id)
    new_name = name if name is not None else plan.name
    new_price = price if price is not None else plan.price
    new_fpt = first_payment_type if first_payment_type is not None else plan.first_payment_type
    new_fpv = first_payment_value if first_payment_type is not None else plan.first_payment_value
    _validate_plan_fields(price=new_price, billing_cycle=plan.billing_cycle,
                          cycle_days=plan.cycle_days, first_payment_type=new_fpt,
                          first_payment_value=new_fpv)
    plan.name, plan.price = new_name, new_price
    plan.first_payment_type, plan.first_payment_value = new_fpt, new_fpv
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.update", target_type="plan", target_id=str(plan.id))
    await db.commit()
    return plan


async def archive_plan(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID,
                       actor_user_id: uuid.UUID | None = None) -> Plan:
    plan = await _get_plan(db, plan_id, service_id)
    plan.status = PlanStatus.ARCHIVED
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.archive", target_type="plan", target_id=str(plan.id))
    await db.commit()
    return plan


async def delete_plan(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID,
                      actor_user_id: uuid.UUID | None = None) -> None:
    plan = await _get_plan(db, plan_id, service_id)
    count = await db.scalar(select(func.count()).select_from(Subscription)
                            .where(Subscription.plan_id == plan_id))
    if count:
        raise ConflictError("구독이 있는 요금제는 삭제할 수 없습니다. 보관(아카이브)을 사용하세요.")
    await db.delete(plan)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="plan.delete", target_type="plan", target_id=str(plan_id))
    await db.commit()


async def list_plans(db: AsyncSession, *, service_id: uuid.UUID,
                     only_active: bool = False) -> list[Plan]:
    query = select(Plan).where(Plan.service_id == service_id).order_by(Plan.created_at)
    if only_active:
        query = query.where(Plan.status == PlanStatus.ACTIVE)
    return list((await db.scalars(query)).all())


async def get_plan(db: AsyncSession, *, plan_id: uuid.UUID, service_id: uuid.UUID) -> Plan:
    return await _get_plan(db, plan_id, service_id)
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/integration/test_plans_service.py -v
```
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/plans.py tests/integration/test_plans_service.py
git commit -m "feat: 요금제 CRUD(검증/삭제 규칙/아카이브)"
```

---

### Task 12: 구독 생성 서비스 (빌링키 발급 + 첫 결제)

**Files:**
- Create: `app/services/subscriptions.py`
- Test: `tests/integration/test_subscription_create.py`

**핵심 안전장치:**
1. 유니크 슬롯(부분 유니크 인덱스)을 **결제 전에 commit**으로 선점 — 동시 요청/크래시에도 이중 결제 불가
2. 토스 호출에 안정적 멱등키(`first-{sub.id}`) — 재시도에도 같은 결과
3. 타임아웃 시 `orderId` 재조회로 승인 여부 확정 후 처리
4. 첫 결제 실패 → 구독 EXPIRED + 결제 FAILED 기록 + 빌링키 삭제. "첫 구독" 판정은
   **혜택 소진 이력**(DONE 결제 보유 또는 결제 없이 활성화된 무료 구독) 기준 —
   결제 실패 후 재시도엔 혜택이 유지되고, 무료 첫구독은 만료 후 반복 적용되지 않음

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_subscription_create.py`

```python
import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, InputValidationError, NotFoundError, PaymentFailedError
from app.models import Payment, Service, Subscription
from app.services import subscriptions as subs
from app.toss.errors import TossError, TossTimeoutError
from app.toss.fake import FakeTossClient
from tests.factories import create_plan, create_service, create_subscription


@pytest.fixture
def fake():
    return FakeTossClient()


async def test_create_with_full_price(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    sub = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-1", customer_key="ck-valid-1", auth_key="auth-1")

    assert sub.status == "ACTIVE"
    assert sub.next_billing_at == sub.current_period_end
    assert cipher.decrypt(sub.billing_key_encrypted).startswith("bk_")
    assert sub.card_info["issuerCode"] == "61"
    assert fake.charges[0]["amount"] == 10000
    assert fake.charges[0]["idempotency_key"] == f"first-{sub.id}"

    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "DONE"
    assert payment.payment_type == "FIRST"
    assert payment.amount == 10000
    assert payment.toss_payment_key.startswith("pay_")


async def test_first_subscription_free_skips_charge(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, first_payment_type="FREE")
    sub = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-free", customer_key="ck-free", auth_key="a")
    assert sub.status == "ACTIVE"
    assert fake.charges == []  # 무결제
    assert await db.scalar(select(Payment).where(Payment.subscription_id == sub.id)) is None


async def test_free_benefit_not_repeatable(db, cipher, fake):
    """무료 첫구독을 쓰고 만료된 뒤 재구독하면 정가 결제 (무한 무료 방지)."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, first_payment_type="FREE")
    sub1 = await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-free2", customer_key="ck-free2a", auth_key="a")
    assert fake.charges == []  # 첫 구독은 무료
    sub1.status = "EXPIRED"
    await db.commit()

    await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="u-free2", customer_key="ck-free2b", auth_key="a")
    assert len(fake.charges) == 1
    assert fake.charges[0]["amount"] == 10000  # 재구독은 정가


async def test_first_subscription_discount_amount(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000,
                             first_payment_type="DISCOUNT_AMOUNT", first_payment_value=3000)
    await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                   external_user_id="u-dc", customer_key="ck-dc", auth_key="a")
    assert fake.charges[0]["amount"] == 7000


async def test_resubscribe_after_expiry_pays_full_price(db, cipher, fake):
    """DONE 결제 이력이 있으면 재구독은 정가."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000,
                             first_payment_type="DISCOUNT_AMOUNT", first_payment_value=9000)
    sub1 = await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                          external_user_id="u-re", customer_key="ck-re1",
                                          auth_key="a")
    assert fake.charges[0]["amount"] == 1000  # 첫구독 할인
    sub1.status = "EXPIRED"
    await db.commit()

    await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                   external_user_id="u-re", customer_key="ck-re2", auth_key="a")
    assert fake.charges[1]["amount"] == 10000  # 정가


async def test_duplicate_subscription_conflicts(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-dup")
    with pytest.raises(ConflictError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-dup", customer_key="ck-d", auth_key="a")
    assert fake.issued == []  # 충돌이면 빌링키 발급 자체를 안 함


async def test_concurrent_create_only_one_wins(session_factory, cipher):
    """DB 부분 유니크 인덱스가 동시 요청을 차단한다."""
    async with session_factory() as setup_db:
        svc, _, _ = await create_service(setup_db, cipher, name=f"svc-cc-{uuid.uuid4().hex[:6]}")
        plan = await create_plan(setup_db, svc)
        svc_id, plan_id = svc.id, plan.id
    fake = FakeTossClient()

    async def attempt(n: int) -> str:
        async with session_factory() as session:
            service = await session.get(Service, svc_id)
            try:
                await subs.create_subscription(
                    session, fake, cipher, service=service, plan_id=plan_id,
                    external_user_id="u-race", customer_key=f"ck-race-{n}", auth_key="a")
                return "ok"
            except ConflictError:
                return "conflict"

    results = await asyncio.gather(attempt(1), attempt(2))
    assert sorted(results) == ["conflict", "ok"]
    # 살아있는 빌링키는 정확히 1개 (패자가 발급했다면 삭제됨)
    assert len(fake.issued) - len(fake.deleted) == 1


async def test_billing_key_issue_failure(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    fake.fail_issue_with = TossError("INVALID_AUTH_KEY", "잘못된 인증키", 400)
    with pytest.raises(PaymentFailedError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-bk", customer_key="ck-bk", auth_key="bad")
    assert await db.scalar(select(Subscription)) is None  # 구독 미생성


async def test_first_charge_failure_expires_and_keeps_benefit(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000,
                             first_payment_type="DISCOUNT_AMOUNT", first_payment_value=5000)
    fake.charge_failure_queue = [TossError("EXCEED_MAX_AMOUNT", "한도 초과", 400)]
    with pytest.raises(PaymentFailedError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-fail", customer_key="ck-f1", auth_key="a")
    sub = await db.scalar(select(Subscription))
    assert sub.status == "EXPIRED"
    assert sub.billing_key_encrypted is None
    payment = await db.scalar(select(Payment))
    assert payment.status == "FAILED"
    assert payment.failure_code == "EXCEED_MAX_AMOUNT"
    assert fake.deleted  # 빌링키 정리됨

    # 재시도: DONE 이력이 없으므로 여전히 첫구독 할인 적용
    sub2 = await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                          external_user_id="u-fail", customer_key="ck-f2",
                                          auth_key="a")
    assert sub2.status == "ACTIVE"
    assert fake.charges[-1]["amount"] == 5000


async def test_timeout_with_actual_approval_resolves_done(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000)
    fake.succeed_despite_timeout = True
    fake.charge_failure_queue = [TossTimeoutError()]
    sub = await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                         external_user_id="u-to", customer_key="ck-to", auth_key="a")
    assert sub.status == "ACTIVE"
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "DONE"  # 재조회로 승인 확인


async def test_timeout_without_approval_fails(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000)
    fake.charge_failure_queue = [TossTimeoutError()]
    with pytest.raises(PaymentFailedError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-to2", customer_key="ck-to2", auth_key="a")


async def test_invalid_customer_key_rejected(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    for bad in ["a", "한글키", "key with space", "x" * 301]:
        with pytest.raises(InputValidationError):
            await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                           external_user_id="u-ck", customer_key=bad, auth_key="a")


async def test_plan_of_other_service_not_found(db, cipher, fake):
    svc_a, _, _ = await create_service(db, cipher, name="svc-cs-a")
    svc_b, _, _ = await create_service(db, cipher, name="svc-cs-b")
    plan_b = await create_plan(db, svc_b)
    with pytest.raises(NotFoundError):
        await subs.create_subscription(db, fake, cipher, service=svc_a, plan_id=plan_b.id,
                                       external_user_id="u-x", customer_key="ck-x", auth_key="a")


async def test_archived_plan_not_subscribable(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, status="ARCHIVED")
    with pytest.raises(NotFoundError):
        await subs.create_subscription(db, fake, cipher, service=svc, plan_id=plan.id,
                                       external_user_id="u-a", customer_key="ck-a", auth_key="a")
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/integration/test_subscription_create.py -v
```
Expected: FAIL (`ModuleNotFoundError: app.services.subscriptions`)

- [ ] **Step 3: 구현** — `app/services/subscriptions.py`

```python
import re
import uuid

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.crypto import AesGcmCipher
from app.core.errors import (
    ConflictError,
    InputValidationError,
    NotFoundError,
    PaymentFailedError,
)
from app.core.security import sha256_hex
from app.models import (
    Payment,
    PaymentStatus,
    PaymentType,
    Plan,
    PlanStatus,
    Service,
    Subscription,
    SubscriptionStatus,
)
from app.services.audit import record_audit
from app.services.billing_math import compute_first_amount, compute_period_end
from app.toss.client import TossClient
from app.toss.errors import TossError, TossTimeoutError
from app.toss.types import ChargeResult

CUSTOMER_KEY_RE = re.compile(r"^[A-Za-z0-9\-_=.@]{2,300}$")
OPEN_STATUSES = (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE,
                 SubscriptionStatus.CANCELED)


def new_order_id(prefix: str) -> str:
    """토스 orderId 규칙: [A-Za-z0-9-_] 6~64자."""
    return f"{prefix}{uuid.uuid4().hex}"


async def safe_delete_billing_key(toss: TossClient, billing_key: str) -> None:
    """베스트 에포트 빌링키 삭제 — 실패해도 로컬 상태는 이미 안전."""
    try:
        await toss.delete_billing_key(billing_key)
    except TossError:
        pass


async def resolve_charge(toss: TossClient, *, billing_key: str, customer_key: str,
                         amount: int, order_id: str, order_name: str,
                         idempotency_key: str) -> ChargeResult:
    """결제 시도. 타임아웃이면 orderId 재조회로 승인 여부를 확정한다."""
    try:
        return await toss.charge(billing_key, customer_key, amount,
                                 order_id, order_name, idempotency_key)
    except TossTimeoutError:
        found = await toss.get_payment_by_order_id(order_id)
        if found is not None and found.status == "DONE":
            return found
        raise


async def get_open_subscription(db: AsyncSession, *, service_id: uuid.UUID,
                                external_user_id: str) -> Subscription | None:
    return await db.scalar(select(Subscription).where(
        Subscription.service_id == service_id,
        Subscription.external_user_id == external_user_id,
        Subscription.status.in_(OPEN_STATUSES)))


async def get_latest_subscription(db: AsyncSession, *, service_id: uuid.UUID,
                                  external_user_id: str) -> Subscription | None:
    return await db.scalar(select(Subscription).where(
        Subscription.service_id == service_id,
        Subscription.external_user_id == external_user_id,
    ).order_by(Subscription.created_at.desc()).limit(1))


async def _is_first_subscription(db: AsyncSession, *, service_id: uuid.UUID,
                                 external_user_id: str) -> bool:
    """첫 구독 판정 — '혜택을 소진한' 과거 구독이 없을 때만 True.

    혜택 소진 구독 = (a) DONE 결제가 있는 구독, 또는
                     (b) 결제 시도 자체가 없는 구독(FREE/100% 할인으로 활성화된 것).
    첫 결제가 실패해 즉시 만료된 구독은 FAILED 결제만 가지므로 어느 쪽에도
    해당하지 않는다 → 재시도 시 첫구독 혜택 유지.
    무료 첫구독은 (b)에 걸리므로 만료 후 재구독해도 무료가 반복되지 않는다.
    """
    has_done_payment = exists().where(
        Payment.subscription_id == Subscription.id,
        Payment.status == PaymentStatus.DONE)
    has_any_payment = exists().where(Payment.subscription_id == Subscription.id)
    benefit_used = await db.scalar(select(exists(
        select(Subscription.id).where(
            Subscription.service_id == service_id,
            Subscription.external_user_id == external_user_id,
            or_(has_done_payment, ~has_any_payment)))))
    return not benefit_used


def _validate_inputs(customer_key: str, external_user_id: str) -> None:
    if not CUSTOMER_KEY_RE.fullmatch(customer_key or ""):
        raise InputValidationError("customer_key 형식이 올바르지 않습니다")
    if not external_user_id or len(external_user_id) > 255:
        raise InputValidationError("external_user_id가 올바르지 않습니다")


async def create_subscription(db: AsyncSession, toss: TossClient, cipher: AesGcmCipher,
                              *, service: Service, plan_id: uuid.UUID,
                              external_user_id: str, customer_key: str,
                              auth_key: str) -> Subscription:
    _validate_inputs(customer_key, external_user_id)

    plan = await db.get(Plan, plan_id)
    if plan is None or plan.service_id != service.id or plan.status != PlanStatus.ACTIVE:
        raise NotFoundError("요금제를 찾을 수 없습니다")

    if await get_open_subscription(db, service_id=service.id,
                                   external_user_id=external_user_id):
        raise ConflictError("이미 구독이 존재합니다")

    is_first = await _is_first_subscription(db, service_id=service.id,
                                            external_user_id=external_user_id)
    amount = (compute_first_amount(plan.price, plan.first_payment_type,
                                   plan.first_payment_value)
              if is_first else plan.price)

    try:
        bk = await toss.issue_billing_key(auth_key, customer_key)
    except TossError as exc:
        raise PaymentFailedError(f"빌링키 발급 실패: {exc.message}", code=exc.code) from exc

    now = utcnow()
    period_end = compute_period_end(now, plan.billing_cycle, plan.cycle_days)
    sub = Subscription(
        service_id=service.id, plan_id=plan.id, external_user_id=external_user_id,
        customer_key=customer_key,
        billing_key_encrypted=cipher.encrypt(bk.billing_key),
        billing_key_hash=sha256_hex(bk.billing_key),
        card_info=bk.card, status=SubscriptionStatus.ACTIVE,
        current_period_start=now, current_period_end=period_end,
        next_billing_at=period_end)
    db.add(sub)
    try:
        await db.flush()
    except IntegrityError:
        # 동시 요청 경쟁 — DB 부분 유니크 인덱스가 최종 심판
        await db.rollback()
        await safe_delete_billing_key(toss, bk.billing_key)
        raise ConflictError("이미 구독이 존재합니다") from None

    payment: Payment | None = None
    if amount > 0:
        payment = Payment(subscription_id=sub.id, order_id=new_order_id("f"),
                          amount=amount, payment_type=PaymentType.FIRST,
                          status=PaymentStatus.PENDING,
                          idempotency_key=f"first-{sub.id}", requested_at=now)
        db.add(payment)
    await record_audit(db, actor_type="SERVICE", action="subscription.create",
                       target_type="subscription", target_id=str(sub.id),
                       detail={"external_user_id": external_user_id,
                               "plan_id": str(plan.id), "amount": amount,
                               "is_first": is_first})
    # 결제 전에 commit: 유니크 슬롯/PENDING 기록을 내구성 있게 선점
    await db.commit()

    if payment is not None:
        try:
            result = await resolve_charge(
                toss, billing_key=bk.billing_key, customer_key=customer_key,
                amount=amount, order_id=payment.order_id, order_name=plan.name,
                idempotency_key=payment.idempotency_key)
        except TossError as exc:
            payment.status = PaymentStatus.FAILED
            payment.failure_code = exc.code
            payment.failure_message = exc.message
            sub.status = SubscriptionStatus.EXPIRED  # 첫 결제 실패 → 즉시 종료(재구독으로 재시도)
            sub.next_billing_at = None
            sub.billing_key_encrypted = None
            await record_audit(db, actor_type="SERVICE",
                               action="subscription.first_payment_failed",
                               target_type="subscription", target_id=str(sub.id),
                               detail={"code": exc.code})
            await db.commit()
            await safe_delete_billing_key(toss, bk.billing_key)
            raise PaymentFailedError(f"첫 결제 실패: {exc.message}", code=exc.code) from exc

        payment.status = PaymentStatus.DONE
        payment.toss_payment_key = result.payment_key
        payment.approved_at = utcnow()
        payment.raw_response = result.raw
        await db.commit()

    return sub
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/integration/test_subscription_create.py -v
```
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/subscriptions.py tests/integration/test_subscription_create.py
git commit -m "feat: 구독 생성(빌링키 발급/첫결제/동시성·타임아웃 안전장치)"
```

---

### Task 13: 구독 관리 (취소/재개/카드변경/조회)

**Files:**
- Modify: `app/services/subscriptions.py` (함수 추가)
- Test: `tests/integration/test_subscription_manage.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_subscription_manage.py`

```python
from datetime import timedelta

import pytest

from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError, PaymentFailedError
from app.services import subscriptions as subs
from app.toss.errors import TossError
from app.toss.fake import FakeTossClient
from tests.factories import create_plan, create_service, create_subscription


@pytest.fixture
def fake():
    return FakeTossClient()


async def test_cancel_active_subscription(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-c")
    sub = await subs.cancel_subscription(db, service=svc, external_user_id="u-c")
    assert sub.status == "CANCELED"
    assert sub.next_billing_at is None


async def test_cancel_past_due_stops_retries(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-pd",
                              status="PAST_DUE", retry_count=2)
    sub = await subs.cancel_subscription(db, service=svc, external_user_id="u-pd")
    assert sub.status == "CANCELED"
    assert sub.next_billing_at is None


async def test_cancel_already_canceled_conflicts(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-cc",
                              status="CANCELED")
    with pytest.raises(ConflictError):
        await subs.cancel_subscription(db, service=svc, external_user_id="u-cc")


async def test_cancel_nonexistent_not_found(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    with pytest.raises(NotFoundError):
        await subs.cancel_subscription(db, service=svc, external_user_id="ghost")


async def test_resume_before_period_end(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    end = utcnow() + timedelta(days=10)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-r",
                              status="CANCELED", period_end=end, next_billing_at=None)
    sub = await subs.resume_subscription(db, service=svc, external_user_id="u-r")
    assert sub.status == "ACTIVE"
    assert sub.next_billing_at == sub.current_period_end


async def test_resume_canceled_past_due_resumes_retry(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    end = utcnow() + timedelta(days=10)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-rpd",
                              status="CANCELED", retry_count=1,
                              period_end=end, next_billing_at=None)
    sub = await subs.resume_subscription(db, service=svc, external_user_id="u-rpd")
    assert sub.status == "PAST_DUE"
    assert sub.next_billing_at is not None
    assert sub.next_billing_at <= utcnow()  # 즉시 재시도 대상


async def test_resume_after_period_end_conflicts(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    start = utcnow() - timedelta(days=40)
    end = utcnow() - timedelta(days=1)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-late",
                              status="CANCELED", period_start=start, period_end=end,
                              next_billing_at=None)
    with pytest.raises(ConflictError):
        await subs.resume_subscription(db, service=svc, external_user_id="u-late")


async def test_change_card(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-cd",
                              billing_key="bk_old")
    sub = await subs.change_card(db, fake, cipher, service=svc, external_user_id="u-cd",
                                 auth_key="new-auth", customer_key="ck-new")
    assert cipher.decrypt(sub.billing_key_encrypted).startswith("bk_")  # 새 키
    assert sub.customer_key == "ck-new"
    assert fake.deleted == ["bk_old"]  # 이전 키 정리


async def test_change_card_on_past_due_schedules_immediate_retry(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-cdp",
                              status="PAST_DUE", retry_count=1,
                              next_billing_at=utcnow() + timedelta(days=1))
    sub = await subs.change_card(db, fake, cipher, service=svc, external_user_id="u-cdp",
                                 auth_key="a", customer_key="ck-cdp")
    assert sub.next_billing_at <= utcnow()


async def test_change_card_issue_failure_keeps_old_key(db, cipher, fake):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-cdf",
                              billing_key="bk_keep")
    fake.fail_issue_with = TossError("INVALID_AUTH_KEY", "bad", 400)
    with pytest.raises(PaymentFailedError):
        await subs.change_card(db, fake, cipher, service=svc, external_user_id="u-cdf",
                               auth_key="a", customer_key="ck-cdf")
    sub = await subs.get_latest_subscription(db, service_id=svc.id,
                                             external_user_id="u-cdf")
    assert cipher.decrypt(sub.billing_key_encrypted) == "bk_keep"
    assert fake.deleted == []


async def test_get_latest_subscription_returns_most_recent(db, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-g",
                              status="EXPIRED")
    newer = await create_subscription(db, cipher, svc, plan, external_user_id="u-g",
                                      status="ACTIVE")
    found = await subs.get_latest_subscription(db, service_id=svc.id,
                                               external_user_id="u-g")
    assert found.id == newer.id
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/integration/test_subscription_manage.py -v
```
Expected: FAIL (함수 없음)

- [ ] **Step 3: 구현** — `app/services/subscriptions.py`에 아래 함수 추가

```python
async def cancel_subscription(db: AsyncSession, *, service: Service, external_user_id: str,
                              actor_type: str = "SERVICE",
                              actor_user_id: uuid.UUID | None = None) -> Subscription:
    """취소 — CANCELED로 전환하되 기간 만료까지 혜택 유지. 갱신/재시도 중단."""
    sub = await db.scalar(select(Subscription).where(
        Subscription.service_id == service.id,
        Subscription.external_user_id == external_user_id,
        Subscription.status.in_((SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE))))
    if sub is None:
        existing = await get_open_subscription(db, service_id=service.id,
                                               external_user_id=external_user_id)
        if existing is not None and existing.status == SubscriptionStatus.CANCELED:
            raise ConflictError("이미 취소된 구독입니다")
        raise NotFoundError("구독을 찾을 수 없습니다")
    sub.status = SubscriptionStatus.CANCELED
    sub.next_billing_at = None
    await record_audit(db, actor_type=actor_type, actor_user_id=actor_user_id,
                       action="subscription.cancel", target_type="subscription",
                       target_id=str(sub.id))
    await db.commit()
    return sub


async def resume_subscription(db: AsyncSession, *, service: Service,
                              external_user_id: str) -> Subscription:
    """만료 전 취소 철회. 미수금(retry_count>0)이면 PAST_DUE로 복귀해 즉시 재시도."""
    sub = await db.scalar(select(Subscription).where(
        Subscription.service_id == service.id,
        Subscription.external_user_id == external_user_id,
        Subscription.status == SubscriptionStatus.CANCELED))
    if sub is None:
        raise NotFoundError("취소된 구독이 없습니다")
    now = utcnow()
    if sub.current_period_end <= now:
        raise ConflictError("만료된 구독은 재개할 수 없습니다")
    if sub.retry_count > 0:
        sub.status = SubscriptionStatus.PAST_DUE
        sub.next_billing_at = now
    else:
        sub.status = SubscriptionStatus.ACTIVE
        sub.next_billing_at = sub.current_period_end
    await record_audit(db, actor_type="SERVICE", action="subscription.resume",
                       target_type="subscription", target_id=str(sub.id))
    await db.commit()
    return sub


async def change_card(db: AsyncSession, toss: TossClient, cipher: AesGcmCipher,
                      *, service: Service, external_user_id: str,
                      auth_key: str, customer_key: str) -> Subscription:
    """새 authKey로 빌링키 교체. 기존 키는 새 키 저장 성공 후 베스트에포트 삭제."""
    _validate_inputs(customer_key, external_user_id)
    sub = await get_open_subscription(db, service_id=service.id,
                                      external_user_id=external_user_id)
    if sub is None:
        raise NotFoundError("구독을 찾을 수 없습니다")
    try:
        bk = await toss.issue_billing_key(auth_key, customer_key)
    except TossError as exc:
        raise PaymentFailedError(f"빌링키 발급 실패: {exc.message}", code=exc.code) from exc

    old_key = cipher.decrypt(sub.billing_key_encrypted) if sub.billing_key_encrypted else None
    sub.billing_key_encrypted = cipher.encrypt(bk.billing_key)
    sub.billing_key_hash = sha256_hex(bk.billing_key)
    sub.customer_key = customer_key
    sub.card_info = bk.card
    if sub.status == SubscriptionStatus.PAST_DUE:
        sub.next_billing_at = utcnow()  # 새 카드로 즉시 재시도
    await record_audit(db, actor_type="SERVICE", action="subscription.change_card",
                       target_type="subscription", target_id=str(sub.id))
    await db.commit()
    if old_key:
        await safe_delete_billing_key(toss, old_key)
    return sub
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/integration/test_subscription_manage.py tests/integration/test_subscription_create.py -v
```
Expected: 25 passed (회귀 포함)

- [ ] **Step 5: Commit**

```bash
git add app/services/subscriptions.py tests/integration/test_subscription_manage.py
git commit -m "feat: 구독 취소/재개/카드변경/조회"
```

---

### Task 14: 자동연장/재시도/만료 (renewals)

**Files:**
- Create: `app/services/renewals.py`
- Test: `tests/integration/test_renewals.py`

**정책 (스펙 §7):** 만료일 도래 → 갱신 결제. 실패 시 `retry_count` 증가 + PAST_DUE +
다음 시도 1일 뒤. `retry_count`가 한도(3)에 도달한 상태에서 또 실패하면 EXPIRED +
빌링키 삭제 + 이메일. 총 시도 = 1회 정기 + 3회 재시도. 재시도 성공 시 기간은
**원래 만료일부터 연속**(공백 없음). CANCELED는 만료일에 EXPIRED 처리.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_renewals.py`

```python
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.models import Payment, Subscription
from app.notifications.email import RecordingEmailSender
from app.services.renewals import RETRY_LIMIT, _renewal_order_id, process_due
from app.toss.errors import TossError, TossTimeoutError
from app.toss.fake import FakeTossClient
from tests.factories import create_plan, create_service, create_subscription


@pytest.fixture
def fake():
    return FakeTossClient()


@pytest.fixture
def email():
    return RecordingEmailSender()


async def _due_subscription(db, cipher, svc, plan, **kw):
    """만료일이 지난 ACTIVE 구독."""
    start = utcnow() - timedelta(days=31)
    end = utcnow() - timedelta(minutes=5)
    defaults = dict(external_user_id="u-due", period_start=start, period_end=end,
                    next_billing_at=end)
    defaults.update(kw)
    return await create_subscription(db, cipher, svc, plan, **defaults)


async def test_renews_due_subscription(db, session_factory, redis_client, cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    sub = await _due_subscription(db, cipher, svc, plan)
    old_end = sub.current_period_end

    stats = await process_due(session_factory, redis_client, fake, cipher, email)

    assert stats["renewed"] == 1
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
    assert sub.current_period_start == old_end          # 기간 연속
    assert sub.next_billing_at == sub.current_period_end
    assert fake.charges[0]["amount"] == 10000
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "DONE"
    assert payment.payment_type == "RENEWAL"


async def test_not_due_untouched(db, session_factory, redis_client, cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)  # next_billing 미래
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats == {"renewed": 0, "failed": 0, "expired": 0, "skipped": 0}
    assert fake.charges == []


async def test_failure_moves_to_past_due_and_notifies(db, session_factory, redis_client,
                                                      cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan)
    fake.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)

    now = utcnow()
    stats = await process_due(session_factory, redis_client, fake, cipher, email, now=now)

    assert stats["failed"] == 1
    await db.refresh(sub)
    assert sub.status == "PAST_DUE"
    assert sub.retry_count == 1
    assert sub.next_billing_at == now + timedelta(days=1)
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.status == "FAILED"
    assert payment.failure_code == "INSUFFICIENT_FUNDS"
    assert len(email.sent) == 1
    assert svc.manager_email == email.sent[0]["to"]


async def test_retry_success_restores_active_continuous_period(
        db, session_factory, redis_client, cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, billing_cycle="MONTH")
    end = utcnow() - timedelta(days=2)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-retry",
                                    status="PAST_DUE", retry_count=1,
                                    period_start=end - timedelta(days=31), period_end=end,
                                    next_billing_at=utcnow() - timedelta(minutes=1))
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["renewed"] == 1
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
    assert sub.retry_count == 0
    assert sub.current_period_start == end  # 원래 만료일부터 연속
    payment = await db.scalar(select(Payment).where(Payment.subscription_id == sub.id))
    assert payment.payment_type == "RETRY"


async def test_retries_exhausted_expires_and_cleans_up(
        db, session_factory, redis_client, cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, status="PAST_DUE",
                                  retry_count=RETRY_LIMIT)
    fake.fail_charge_with = TossError("CARD_EXPIRED", "카드 만료", 400)

    stats = await process_due(session_factory, redis_client, fake, cipher, email)

    assert stats["expired"] == 1
    await db.refresh(sub)
    assert sub.status == "EXPIRED"
    assert sub.billing_key_encrypted is None
    assert sub.next_billing_at is None
    assert fake.deleted  # 빌링키 삭제
    assert "만료" in email.sent[0]["subject"]


async def test_full_retry_storyline(db, session_factory, redis_client, cipher, fake, email):
    """정기 1회 + 재시도 3회 = 총 4회 시도 후 만료."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, external_user_id="u-story")
    fake.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)

    now = utcnow()
    for attempt in range(4):
        await process_due(session_factory, redis_client, fake, cipher, email, now=now)
        await db.refresh(sub)
        now = now + timedelta(days=1, minutes=1)

    assert len(fake.charges) == 4
    assert sub.status == "EXPIRED"
    # 더 돌려도 결제 시도 없음
    await process_due(session_factory, redis_client, fake, cipher, email, now=now)
    assert len(fake.charges) == 4


async def test_canceled_expires_at_period_end_without_charge(
        db, session_factory, redis_client, cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-cx",
                                    status="CANCELED",
                                    period_start=utcnow() - timedelta(days=31),
                                    period_end=utcnow() - timedelta(minutes=1),
                                    next_billing_at=None)
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["expired"] == 1
    await db.refresh(sub)
    assert sub.status == "EXPIRED"
    assert sub.billing_key_encrypted is None
    assert fake.charges == []
    assert fake.deleted


async def test_canceled_before_period_end_kept(db, session_factory, redis_client,
                                               cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-ck",
                                    status="CANCELED", next_billing_at=None)
    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["expired"] == 0
    await db.refresh(sub)
    assert sub.status == "CANCELED"


async def test_redis_lock_prevents_double_charge(db, session_factory, redis_client,
                                                 cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan)
    await redis_client.set(f"lock:renew:{sub.id}", "1", ex=60)  # 다른 워커가 처리 중인 상황

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["skipped"] == 1
    assert fake.charges == []


async def test_crash_recovery_done_payment_advances_without_recharge(
        db, session_factory, redis_client, cipher, fake, email):
    """직전 실행이 '결제 성공 후 커밋 전' 크래시 → 같은 order_id의 DONE 결제 발견 시 재결제 없이 기간만 복구."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, external_user_id="u-crash")
    db.add(Payment(subscription_id=sub.id, order_id=_renewal_order_id(sub),
                   amount=plan.price, payment_type="RENEWAL", status="DONE",
                   toss_payment_key="pay_recovered", idempotency_key="ik",
                   requested_at=utcnow(), approved_at=utcnow()))
    await db.commit()

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["renewed"] == 1
    assert fake.charges == []  # 재결제 없음
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
    assert sub.next_billing_at == sub.current_period_end


async def test_renewal_timeout_resolved_by_lookup(db, session_factory, redis_client,
                                                  cipher, fake, email):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await _due_subscription(db, cipher, svc, plan, external_user_id="u-rt")
    fake.succeed_despite_timeout = True
    fake.charge_failure_queue = [TossTimeoutError()]

    stats = await process_due(session_factory, redis_client, fake, cipher, email)
    assert stats["renewed"] == 1
    await db.refresh(sub)
    assert sub.status == "ACTIVE"
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/integration/test_renewals.py -v
```
Expected: FAIL (`ModuleNotFoundError: app.services.renewals`)

- [ ] **Step 3: 구현** — `app/services/renewals.py`

```python
import logging
import uuid
from datetime import datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.clock import utcnow
from app.core.crypto import AesGcmCipher
from app.models import (
    Payment,
    PaymentStatus,
    PaymentType,
    Plan,
    Service,
    Subscription,
    SubscriptionStatus,
)
from app.notifications.email import EmailSender
from app.services.audit import record_audit
from app.services.billing_math import compute_period_end
from app.services.subscriptions import resolve_charge, safe_delete_billing_key
from app.toss.client import TossClient
from app.toss.errors import TossError

logger = logging.getLogger("payment.renewals")

RETRY_LIMIT = 3
RETRY_INTERVAL = timedelta(days=1)
RENEW_LOCK_TTL = 120


def _renewal_order_id(sub: Subscription) -> str:
    """(구독, 기간, 시도)에 대해 결정적 — 크래시 후 재실행해도 같은 주문/멱등키."""
    return f"r{sub.id.hex}p{int(sub.current_period_end.timestamp())}a{sub.retry_count}"


def _advance_period(sub: Subscription, plan: Plan) -> None:
    new_start = sub.current_period_end
    sub.current_period_start = new_start
    sub.current_period_end = compute_period_end(new_start, plan.billing_cycle, plan.cycle_days)
    sub.next_billing_at = sub.current_period_end
    sub.retry_count = 0
    sub.status = SubscriptionStatus.ACTIVE


async def process_due(session_factory: async_sessionmaker, redis: Redis, toss: TossClient,
                      cipher: AesGcmCipher, email_sender: EmailSender,
                      *, now: datetime | None = None) -> dict:
    """갱신 배치 1회 실행. 스케줄러/관리 명령에서 호출."""
    now = now or utcnow()
    stats = {"renewed": 0, "failed": 0, "expired": 0, "skipped": 0}
    async with session_factory() as db:
        canceled_due = list((await db.scalars(select(Subscription.id).where(
            Subscription.status == SubscriptionStatus.CANCELED,
            Subscription.current_period_end <= now))).all())
        renew_due = list((await db.scalars(select(Subscription.id).where(
            Subscription.status.in_((SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)),
            Subscription.next_billing_at.is_not(None),
            Subscription.next_billing_at <= now))).all())
    for sub_id in canceled_due:
        await _expire_canceled(session_factory, redis, toss, cipher, sub_id, now=now, stats=stats)
    for sub_id in renew_due:
        await _renew_one(session_factory, redis, toss, cipher, email_sender,
                         sub_id, now=now, stats=stats)
    return stats


async def _expire_canceled(session_factory, redis, toss, cipher,
                           sub_id: uuid.UUID, *, now: datetime, stats: dict) -> None:
    lock_key = f"lock:renew:{sub_id}"
    if not await redis.set(lock_key, "1", nx=True, ex=RENEW_LOCK_TTL):
        stats["skipped"] += 1
        return
    try:
        async with session_factory() as db:
            sub = await db.get(Subscription, sub_id, with_for_update=True)
            if (sub is None or sub.status != SubscriptionStatus.CANCELED
                    or sub.current_period_end > now):
                stats["skipped"] += 1
                return
            billing_key = (cipher.decrypt(sub.billing_key_encrypted)
                           if sub.billing_key_encrypted else None)
            sub.status = SubscriptionStatus.EXPIRED
            sub.billing_key_encrypted = None
            sub.next_billing_at = None
            await record_audit(db, actor_type="SYSTEM", action="subscription.expired",
                               target_type="subscription", target_id=str(sub.id),
                               detail={"reason": "canceled_period_end"})
            await db.commit()
        if billing_key:
            await safe_delete_billing_key(toss, billing_key)
        stats["expired"] += 1
    finally:
        await redis.delete(lock_key)


async def _renew_one(session_factory, redis, toss, cipher, email_sender,
                     sub_id: uuid.UUID, *, now: datetime, stats: dict) -> None:
    lock_key = f"lock:renew:{sub_id}"
    if not await redis.set(lock_key, "1", nx=True, ex=RENEW_LOCK_TTL):
        stats["skipped"] += 1
        return
    try:
        async with session_factory() as db:
            sub = await db.get(Subscription, sub_id, with_for_update=True)
            if (sub is None
                    or sub.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE)
                    or sub.next_billing_at is None or sub.next_billing_at > now
                    or sub.billing_key_encrypted is None):
                stats["skipped"] += 1
                return
            plan = await db.get(Plan, sub.plan_id)
            service = await db.get(Service, sub.service_id)
            order_id = _renewal_order_id(sub)

            payment = await db.scalar(select(Payment).where(Payment.order_id == order_id))
            if payment is not None and payment.status == PaymentStatus.DONE:
                # 직전 실행이 '결제 후 커밋 전' 크래시 — 재결제 없이 기간 복구
                _advance_period(sub, plan)
                await record_audit(db, actor_type="SYSTEM", action="subscription.renewed",
                                   target_type="subscription", target_id=str(sub.id),
                                   detail={"recovered": True})
                await db.commit()
                stats["renewed"] += 1
                return
            if payment is None:
                payment = Payment(
                    subscription_id=sub.id, order_id=order_id, amount=plan.price,
                    payment_type=(PaymentType.RENEWAL if sub.retry_count == 0
                                  else PaymentType.RETRY),
                    status=PaymentStatus.PENDING,
                    idempotency_key=f"renew-{order_id}", requested_at=now)
                db.add(payment)
                await db.commit()  # 결제 전 내구성 확보

            billing_key = cipher.decrypt(sub.billing_key_encrypted)
            try:
                result = await resolve_charge(
                    toss, billing_key=billing_key, customer_key=sub.customer_key,
                    amount=plan.price, order_id=order_id, order_name=plan.name,
                    idempotency_key=payment.idempotency_key)
            except TossError as exc:
                await _handle_charge_failure(db, toss, email_sender, sub, service,
                                             payment, billing_key, exc, now=now, stats=stats)
                return

            payment.status = PaymentStatus.DONE
            payment.toss_payment_key = result.payment_key
            payment.approved_at = utcnow()
            payment.raw_response = result.raw
            _advance_period(sub, plan)
            await record_audit(db, actor_type="SYSTEM", action="subscription.renewed",
                               target_type="subscription", target_id=str(sub.id),
                               detail={"order_id": order_id, "amount": plan.price})
            await db.commit()
            stats["renewed"] += 1
    finally:
        await redis.delete(lock_key)


async def _handle_charge_failure(db, toss, email_sender, sub: Subscription,
                                 service: Service, payment: Payment, billing_key: str,
                                 exc: TossError, *, now: datetime, stats: dict) -> None:
    payment.status = PaymentStatus.FAILED
    payment.failure_code = exc.code
    payment.failure_message = exc.message

    if sub.retry_count >= RETRY_LIMIT:
        sub.status = SubscriptionStatus.EXPIRED
        sub.next_billing_at = None
        sub.billing_key_encrypted = None
        await record_audit(db, actor_type="SYSTEM", action="subscription.expired",
                           target_type="subscription", target_id=str(sub.id),
                           detail={"reason": "retries_exhausted", "code": exc.code})
        await db.commit()
        await safe_delete_billing_key(toss, billing_key)
        await email_sender.send(
            service.manager_email,
            f"[결제시스템] 구독 만료 안내 — {service.name}",
            f"사용자 {sub.external_user_id}의 구독이 결제 재시도 {RETRY_LIMIT}회 실패로 "
            f"만료되었습니다.\n사유: [{exc.code}] {exc.message}")
        stats["expired"] += 1
    else:
        sub.retry_count += 1
        sub.status = SubscriptionStatus.PAST_DUE
        sub.next_billing_at = now + RETRY_INTERVAL
        await record_audit(db, actor_type="SYSTEM", action="subscription.payment_failed",
                           target_type="subscription", target_id=str(sub.id),
                           detail={"code": exc.code, "retry_count": sub.retry_count})
        await db.commit()
        await email_sender.send(
            service.manager_email,
            f"[결제시스템] 결제 실패 안내 — {service.name}",
            f"사용자 {sub.external_user_id}의 갱신 결제가 실패했습니다 "
            f"(재시도 {sub.retry_count}/{RETRY_LIMIT}).\n사유: [{exc.code}] {exc.message}")
        stats["failed"] += 1
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/integration/test_renewals.py -v
```
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/renewals.py tests/integration/test_renewals.py
git commit -m "feat: 자동연장 배치(재시도/만료/락/크래시 복구/타임아웃 확정)"
```

---

### Task 15: 앱 팩토리 + 외부 API 인증(HMAC) + 에러 핸들러

**Files:**
- Create: `app/main.py`, `app/api/__init__.py`, `app/api/deps.py`, `app/api/errors.py`, `app/api/v1/__init__.py`, `app/api/v1/plans.py`, `app/schemas/__init__.py`, `app/schemas/api.py`
- Modify: `pyproject.toml` (dev 의존성 asgi-lifespan), `tests/conftest.py` (app/client 픽스처)
- Test: `tests/integration/test_api_auth.py`

- [ ] **Step 1: dev 의존성 추가**

```bash
uv add --dev asgi-lifespan
```

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/integration/test_api_auth.py`

```python
import time

from tests.factories import create_plan, create_service
from tests.helpers import api_request, signed_headers


async def test_valid_signed_request_returns_plans(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    await create_plan(db, svc, name="베이직", price=9900)
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 200
    body = resp.json()
    assert body["plans"][0]["name"] == "베이직"
    assert body["plans"][0]["price"] == 9900


async def test_missing_auth_headers_rejected(client, db, cipher):
    resp = await client.get("/api/v1/plans")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_unknown_api_key_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    resp = await api_request(client, "GET", "/api/v1/plans", "svc_wrong-key", secret)
    assert resp.status_code == 401


async def test_bad_signature_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, "wrong-secret")
    assert resp.status_code == 401


async def test_stale_timestamp_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    stale = str(int(time.time()) - 3600)
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret,
                             timestamp=stale)
    assert resp.status_code == 401


async def test_nonce_replay_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    headers = signed_headers(api_key, secret, "GET", "/api/v1/plans")
    first = await client.get("/api/v1/plans", headers=headers)
    assert first.status_code == 200
    replay = await client.get("/api/v1/plans", headers=headers)  # 같은 헤더 재사용
    assert replay.status_code == 401


async def test_ip_not_in_whitelist_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher, allowed_ips=["10.0.0.1"])
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 403


async def test_inactive_service_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    svc.status = "INACTIVE"
    await db.commit()
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret)
    assert resp.status_code == 401


async def test_health_endpoint_is_public(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 3: conftest에 app/client 픽스처 추가** — `tests/conftest.py` 끝에 추가

```python
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.notifications.email import RecordingEmailSender
from app.toss.fake import FakeTossClient


@pytest.fixture
def fake_toss() -> FakeTossClient:
    return FakeTossClient()


@pytest.fixture
def email_sender() -> RecordingEmailSender:
    return RecordingEmailSender()


@pytest.fixture
async def app(settings, engine, fake_toss, email_sender):
    application = create_app(settings, toss_client=fake_toss,
                             email_sender=email_sender, engine=engine)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c
```

- [ ] **Step 4: 구현** — `app/schemas/api.py` (+ 빈 `app/schemas/__init__.py`)

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import Plan, Subscription


class SubscriptionCreateRequest(BaseModel):
    external_user_id: str = Field(min_length=1, max_length=255)
    plan_id: uuid.UUID
    auth_key: str = Field(min_length=1, max_length=300)
    customer_key: str = Field(min_length=2, max_length=300)
    # 주의: 금액 필드는 없다 — 금액은 서버가 plan에서 계산(조작 차단)


class CardChangeRequest(BaseModel):
    auth_key: str = Field(min_length=1, max_length=300)
    customer_key: str = Field(min_length=2, max_length=300)


class PlanResponse(BaseModel):
    id: uuid.UUID
    name: str
    price: int
    currency: str
    billing_cycle: str
    cycle_days: int | None
    first_payment_type: str
    first_payment_value: int | None

    @classmethod
    def from_model(cls, plan: Plan) -> "PlanResponse":
        return cls(id=plan.id, name=plan.name, price=plan.price, currency=plan.currency,
                   billing_cycle=plan.billing_cycle, cycle_days=plan.cycle_days,
                   first_payment_type=plan.first_payment_type,
                   first_payment_value=plan.first_payment_value)


class SubscriptionResponse(BaseModel):
    id: uuid.UUID
    external_user_id: str
    plan_id: uuid.UUID
    plan_name: str
    status: str
    current_period_start: datetime
    current_period_end: datetime
    next_billing_at: datetime | None
    card: dict | None
    retry_count: int

    @classmethod
    def from_model(cls, sub: Subscription, plan: Plan) -> "SubscriptionResponse":
        return cls(id=sub.id, external_user_id=sub.external_user_id, plan_id=sub.plan_id,
                   plan_name=plan.name, status=sub.status,
                   current_period_start=sub.current_period_start,
                   current_period_end=sub.current_period_end,
                   next_billing_at=sub.next_billing_at, card=sub.card_info,
                   retry_count=sub.retry_count)


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order_id: str
    amount: int
    status: str
    payment_type: str
    failure_code: str | None
    failure_message: str | None
    requested_at: datetime
    approved_at: datetime | None
```

`app/api/errors.py`

```python
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import DomainError

logger = logging.getLogger("payment.api")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        return JSONResponse(status_code=exc.http_status,
                            content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in e["loc"] if p != "body")
                         for e in exc.errors()})
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR",
            "message": f"요청 형식이 올바르지 않습니다: {', '.join(fields)}"}})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("unhandled error")
        # 내부 정보 비노출
        return JSONResponse(status_code=500, content={"error": {
            "code": "INTERNAL_ERROR", "message": "서버 오류가 발생했습니다"}})
```

`app/api/deps.py` (+ 빈 `app/api/__init__.py`)

```python
import time
from collections.abc import AsyncIterator

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import AesGcmCipher
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitedError
from app.core.security import constant_time_equals, sha256_hex, sign_request
from app.models import Service, ServiceStatus
from app.notifications.email import EmailSender
from app.toss.client import TossClient

AUTH_FAILED = "인증에 실패했습니다"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_cipher(request: Request) -> AesGcmCipher:
    return request.app.state.cipher


def get_toss(request: Request) -> TossClient:
    return request.app.state.toss


def get_email_sender(request: Request) -> EmailSender:
    return request.app.state.email_sender


def get_client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


async def authenticate_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    cipher: AesGcmCipher = Depends(get_cipher),
) -> Service:
    """외부 API 3중 인증: API키 + IP 화이트리스트 + HMAC 서명(타임스탬프/nonce)."""
    api_key = request.headers.get("x-service-key", "")
    timestamp = request.headers.get("x-timestamp", "")
    nonce = request.headers.get("x-nonce", "")
    signature = request.headers.get("x-signature", "")
    if not (api_key and timestamp and nonce and signature):
        raise AuthenticationError(AUTH_FAILED)

    # 1) API 키 (해시 대조)
    service = await db.scalar(select(Service).where(
        Service.api_key_hash == sha256_hex(api_key)))
    if service is None or service.status != ServiceStatus.ACTIVE:
        raise AuthenticationError(AUTH_FAILED)

    # 2) IP 화이트리스트
    ip = get_client_ip(request, settings)
    if ip not in service.allowed_ips:
        raise PermissionDeniedError("허용되지 않은 IP입니다")

    # 3) 타임스탬프 윈도우 (재전송 방어 1차)
    try:
        ts = int(timestamp)
    except ValueError:
        raise AuthenticationError(AUTH_FAILED) from None
    if abs(time.time() - ts) > settings.hmac_timestamp_tolerance_seconds:
        raise AuthenticationError(AUTH_FAILED)

    # 4) nonce 1회용 (재전송 방어 2차)
    nonce_key = f"nonce:{service.id}:{nonce}"
    if not await redis.set(nonce_key, "1", nx=True, ex=600):
        raise AuthenticationError(AUTH_FAILED)

    # 5) HMAC 서명 검증 (본문 무결성 포함)
    body = await request.body()
    secret = cipher.decrypt(service.hmac_secret_encrypted)
    expected = sign_request(secret, request.method, request.url.path,
                            timestamp, nonce, body)
    if not constant_time_equals(expected, signature):
        raise AuthenticationError(AUTH_FAILED)

    # 6) 서비스별 rate limit
    window = int(time.time() // 60)
    rl_key = f"rl:{service.id}:{window}"
    count = await redis.incr(rl_key)
    if count == 1:
        await redis.expire(rl_key, 90)
    if count > settings.rate_limit_per_minute:
        raise RateLimitedError("요청 한도를 초과했습니다")

    return service


async def payment_rate_limit(
    request: Request,
    service: Service = Depends(authenticate_service),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> Service:
    """결제성 엔드포인트 전용 추가 제한."""
    window = int(time.time() // 60)
    key = f"rlp:{service.id}:{window}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 90)
    if count > settings.rate_limit_payment_per_minute:
        raise RateLimitedError("결제 요청 한도를 초과했습니다")
    return service
```

`app/api/v1/plans.py`

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import authenticate_service, get_db
from app.models import Service
from app.schemas.api import PlanResponse
from app.services.plans import list_plans

router = APIRouter()


@router.get("/plans")
async def get_plans(service: Service = Depends(authenticate_service),
                    db: AsyncSession = Depends(get_db)):
    plans = await list_plans(db, service_id=service.id, only_active=True)
    return {"plans": [PlanResponse.from_model(p) for p in plans]}
```

`app/api/v1/__init__.py`

```python
from fastapi import APIRouter

from app.api.v1 import plans

router = APIRouter()
router.include_router(plans.router, tags=["plans"])
```

`app/main.py`

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.errors import register_error_handlers
from app.api.v1 import router as api_v1_router
from app.core.config import Settings
from app.core.crypto import AesGcmCipher
from app.core.db import create_engine, create_session_factory
from app.notifications.email import ConsoleEmailSender, EmailSender
from app.toss.client import HttpTossClient, TossClient


def create_app(settings: Settings | None = None, *,
               toss_client: TossClient | None = None,
               email_sender: EmailSender | None = None,
               engine: AsyncEngine | None = None) -> FastAPI:
    app_settings = settings or Settings()
    own_engine = engine is None
    own_toss = toss_client is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = app_settings
        app.state.engine = engine or create_engine(app_settings.database_url)
        app.state.session_factory = create_session_factory(app.state.engine)
        app.state.redis = Redis.from_url(app_settings.redis_url, decode_responses=True)
        app.state.cipher = AesGcmCipher(app_settings.encryption_key)
        app.state.toss = toss_client or HttpTossClient(
            app_settings.toss_secret_key, app_settings.toss_api_base_url)
        app.state.email_sender = email_sender or ConsoleEmailSender()
        yield
        await app.state.redis.aclose()
        if own_toss and isinstance(app.state.toss, HttpTossClient):
            await app.state.toss.aclose()
        if own_engine:
            await app.state.engine.dispose()

    app = FastAPI(
        title="구독/결제 API 서버",
        lifespan=lifespan,
        docs_url="/docs" if app_settings.environment != "prod" else None,
        redoc_url=None)
    register_error_handlers(app)
    app.include_router(api_v1_router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 5: 통과 확인**

```bash
uv run pytest tests/integration/test_api_auth.py -v
```
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/api app/schemas tests/conftest.py tests/integration/test_api_auth.py pyproject.toml uv.lock
git commit -m "feat: 앱 팩토리/HMAC 3중 인증/에러 핸들러/요금제 API"
```

---

### Task 16: 외부 API — 구독/결제 엔드포인트

**Files:**
- Create: `app/api/v1/subscriptions.py`, `app/api/v1/payments.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/integration/test_api_endpoints.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_api_endpoints.py`

```python
from app.toss.errors import TossError
from tests.factories import create_plan, create_service, create_subscription
from tests.helpers import api_request


async def _setup(db, cipher, **plan_kw):
    svc, api_key, secret = await create_service(db, cipher)
    plan = await create_plan(db, svc, **plan_kw)
    return svc, api_key, secret, plan


async def test_create_subscription_endpoint(client, db, cipher, fake_toss):
    svc, api_key, secret, plan = await _setup(db, cipher, price=12000)
    resp = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                             json_body={"external_user_id": "u-api-1",
                                        "plan_id": str(plan.id),
                                        "auth_key": "auth-from-sdk",
                                        "customer_key": "ck-api-1"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ACTIVE"
    assert body["plan_name"] == plan.name
    assert body["card"]["issuerCode"] == "61"
    assert fake_toss.charges[0]["amount"] == 12000


async def test_create_subscription_ignores_injected_amount(client, db, cipher, fake_toss):
    """본문에 amount를 넣어도 서버는 plan 가격으로 결제한다 (금액 조작 차단)."""
    svc, api_key, secret, plan = await _setup(db, cipher, price=50000)
    resp = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                             json_body={"external_user_id": "u-amt",
                                        "plan_id": str(plan.id),
                                        "auth_key": "a", "customer_key": "ck-amt",
                                        "amount": 1})
    assert resp.status_code == 201
    assert fake_toss.charges[0]["amount"] == 50000


async def test_duplicate_subscription_409(client, db, cipher):
    svc, api_key, secret, plan = await _setup(db, cipher)
    body = {"external_user_id": "u-dup", "plan_id": str(plan.id),
            "auth_key": "a", "customer_key": "ck-dup"}
    first = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                              json_body=body)
    assert first.status_code == 201
    second = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                               json_body={**body, "customer_key": "ck-dup2"})
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONFLICT"


async def test_payment_failure_402_with_code(client, db, cipher, fake_toss):
    svc, api_key, secret, plan = await _setup(db, cipher)
    fake_toss.fail_charge_with = TossError("INSUFFICIENT_FUNDS", "잔액 부족", 400)
    resp = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                             json_body={"external_user_id": "u-pf",
                                        "plan_id": str(plan.id),
                                        "auth_key": "a", "customer_key": "ck-pf"})
    assert resp.status_code == 402
    assert resp.json()["error"]["code"] == "INSUFFICIENT_FUNDS"


async def test_malformed_body_422_error_format(client, db, cipher):
    svc, api_key, secret, plan = await _setup(db, cipher)
    resp = await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                             json_body={"external_user_id": "u-bad"})  # 필수 필드 누락
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_get_subscription_status(client, db, cipher):
    svc, api_key, secret, plan = await _setup(db, cipher)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-get")
    resp = await api_request(client, "GET", "/api/v1/subscriptions/u-get",
                             api_key, secret)
    assert resp.status_code == 200
    assert resp.json()["external_user_id"] == "u-get"
    missing = await api_request(client, "GET", "/api/v1/subscriptions/ghost",
                                api_key, secret)
    assert missing.status_code == 404


async def test_cancel_and_resume_endpoints(client, db, cipher):
    svc, api_key, secret, plan = await _setup(db, cipher)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-cr")
    cancel = await api_request(client, "POST", "/api/v1/subscriptions/u-cr/cancel",
                               api_key, secret)
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "CANCELED"
    resume = await api_request(client, "POST", "/api/v1/subscriptions/u-cr/resume",
                               api_key, secret)
    assert resume.status_code == 200
    assert resume.json()["status"] == "ACTIVE"


async def test_change_card_endpoint(client, db, cipher, fake_toss):
    svc, api_key, secret, plan = await _setup(db, cipher)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-card",
                              billing_key="bk_before")
    resp = await api_request(client, "POST", "/api/v1/subscriptions/u-card/change-card",
                             api_key, secret,
                             json_body={"auth_key": "new-a", "customer_key": "ck-new-card"})
    assert resp.status_code == 200
    assert fake_toss.deleted == ["bk_before"]


async def test_list_payments_endpoint(client, db, cipher, fake_toss):
    svc, api_key, secret, plan = await _setup(db, cipher, price=7000)
    await api_request(client, "POST", "/api/v1/subscriptions", api_key, secret,
                      json_body={"external_user_id": "u-pay", "plan_id": str(plan.id),
                                 "auth_key": "a", "customer_key": "ck-pay"})
    resp = await api_request(client, "GET", "/api/v1/payments/u-pay", api_key, secret)
    assert resp.status_code == 200
    payments = resp.json()["payments"]
    assert len(payments) == 1
    assert payments[0]["amount"] == 7000
    assert payments[0]["status"] == "DONE"


async def test_cross_service_isolation(client, db, cipher):
    """서비스 A의 키로는 서비스 B의 구독을 볼 수 없다."""
    svc_a, key_a, secret_a = await create_service(db, cipher, name="svc-iso-a")
    svc_b, _, _ = await create_service(db, cipher, name="svc-iso-b")
    plan_b = await create_plan(db, svc_b)
    await create_subscription(db, cipher, svc_b, plan_b, external_user_id="u-iso")
    resp = await api_request(client, "GET", "/api/v1/subscriptions/u-iso",
                             key_a, secret_a)
    assert resp.status_code == 404  # A 범위에는 존재하지 않음
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/integration/test_api_endpoints.py -v
```
Expected: FAIL (404 — 라우트 없음)

- [ ] **Step 3: 구현** — `app/api/v1/subscriptions.py`

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    authenticate_service,
    get_cipher,
    get_db,
    get_toss,
    payment_rate_limit,
)
from app.core.crypto import AesGcmCipher
from app.core.errors import NotFoundError
from app.models import Plan, Service, Subscription
from app.schemas.api import (
    CardChangeRequest,
    SubscriptionCreateRequest,
    SubscriptionResponse,
)
from app.services import subscriptions as subscription_service
from app.toss.client import TossClient

router = APIRouter()


async def _to_response(db: AsyncSession, sub: Subscription) -> SubscriptionResponse:
    plan = await db.get(Plan, sub.plan_id)
    return SubscriptionResponse.from_model(sub, plan)


@router.post("/subscriptions", status_code=201)
async def create_subscription(
    payload: SubscriptionCreateRequest,
    service: Service = Depends(payment_rate_limit),
    db: AsyncSession = Depends(get_db),
    toss: TossClient = Depends(get_toss),
    cipher: AesGcmCipher = Depends(get_cipher),
):
    sub = await subscription_service.create_subscription(
        db, toss, cipher, service=service, plan_id=payload.plan_id,
        external_user_id=payload.external_user_id,
        customer_key=payload.customer_key, auth_key=payload.auth_key)
    return await _to_response(db, sub)


@router.get("/subscriptions/{external_user_id}")
async def get_subscription(
    external_user_id: str,
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
):
    sub = await subscription_service.get_latest_subscription(
        db, service_id=service.id, external_user_id=external_user_id)
    if sub is None:
        raise NotFoundError("구독을 찾을 수 없습니다")
    return await _to_response(db, sub)


@router.post("/subscriptions/{external_user_id}/cancel")
async def cancel_subscription(
    external_user_id: str,
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
):
    sub = await subscription_service.cancel_subscription(
        db, service=service, external_user_id=external_user_id)
    return await _to_response(db, sub)


@router.post("/subscriptions/{external_user_id}/resume")
async def resume_subscription(
    external_user_id: str,
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
):
    sub = await subscription_service.resume_subscription(
        db, service=service, external_user_id=external_user_id)
    return await _to_response(db, sub)


@router.post("/subscriptions/{external_user_id}/change-card")
async def change_card(
    external_user_id: str,
    payload: CardChangeRequest,
    service: Service = Depends(payment_rate_limit),
    db: AsyncSession = Depends(get_db),
    toss: TossClient = Depends(get_toss),
    cipher: AesGcmCipher = Depends(get_cipher),
):
    sub = await subscription_service.change_card(
        db, toss, cipher, service=service, external_user_id=external_user_id,
        auth_key=payload.auth_key, customer_key=payload.customer_key)
    return await _to_response(db, sub)
```

`app/api/v1/payments.py`

```python
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import authenticate_service, get_db
from app.models import Payment, Service, Subscription
from app.schemas.api import PaymentResponse

router = APIRouter()


@router.get("/payments/{external_user_id}")
async def list_payments(
    external_user_id: str,
    service: Service = Depends(authenticate_service),
    db: AsyncSession = Depends(get_db),
):
    rows = await db.scalars(
        select(Payment)
        .join(Subscription, Payment.subscription_id == Subscription.id)
        .where(Subscription.service_id == service.id,
               Subscription.external_user_id == external_user_id)
        .order_by(Payment.requested_at.desc())
        .limit(50))
    return {"payments": [PaymentResponse.model_validate(p) for p in rows.all()]}
```

`app/api/v1/__init__.py` 전체 교체:

```python
from fastapi import APIRouter

from app.api.v1 import payments, plans, subscriptions

router = APIRouter()
router.include_router(plans.router, tags=["plans"])
router.include_router(subscriptions.router, tags=["subscriptions"])
router.include_router(payments.router, tags=["payments"])
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/integration/test_api_endpoints.py tests/integration/test_api_auth.py -v
```
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add app/api tests/integration/test_api_endpoints.py
git commit -m "feat: 외부 구독/결제 API 엔드포인트"
```

---

### Task 17: 토스 웹훅 수신

**Files:**
- Create: `app/services/webhooks.py`, `app/api/v1/webhooks.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/integration/test_webhooks.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_webhooks.py`

```python
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.main import create_app
from app.models import Payment, WebhookEvent
from app.toss.types import ChargeResult
from tests.factories import create_plan, create_service, create_subscription


def _billing_deleted(billing_key: str) -> dict:
    return {"eventType": "BILLING_DELETED",
            "createdAt": "2026-06-05T00:00:00.000000",
            "data": {"billingKey": billing_key, "reason": "삭제 API 요청"}}


async def test_billing_deleted_notifies_manager(client, db, cipher, email_sender):
    svc, _, _ = await create_service(db, cipher, manager_email="mgr@x.com")
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, billing_key="bk_hooked")
    resp = await client.post("/api/v1/webhooks/toss", json=_billing_deleted("bk_hooked"),
                             headers={"tosspayments-webhook-transmission-id": "wh-1"})
    assert resp.status_code == 200
    event = await db.scalar(select(WebhookEvent))
    assert event.status == "PROCESSED"
    assert len(email_sender.sent) == 1
    assert email_sender.sent[0]["to"] == "mgr@x.com"


async def test_duplicate_transmission_processed_once(client, db, cipher, email_sender):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, billing_key="bk_dup")
    payload = _billing_deleted("bk_dup")
    headers = {"tosspayments-webhook-transmission-id": "wh-same"}
    await client.post("/api/v1/webhooks/toss", json=payload, headers=headers)
    await client.post("/api/v1/webhooks/toss", json=payload, headers=headers)
    events = (await db.scalars(select(WebhookEvent))).all()
    assert len(events) == 1
    assert len(email_sender.sent) == 1  # 한 번만 처리


async def test_unknown_event_ignored(client, db, cipher):
    resp = await client.post("/api/v1/webhooks/toss",
                             json={"eventType": "DEPOSIT_CALLBACK", "data": {}},
                             headers={"tosspayments-webhook-transmission-id": "wh-ig"})
    assert resp.status_code == 200
    event = await db.scalar(select(WebhookEvent))
    assert event.status == "IGNORED"


async def test_payment_status_changed_verified_by_refetch(client, db, cipher, fake_toss):
    """페이로드를 믿지 않고 토스 재조회로 확정 — 재조회가 CANCELED일 때만 반영."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan)
    payment = Payment(subscription_id=sub.id, order_id="order-wh-1", amount=plan.price,
                      payment_type="RENEWAL", status="DONE", idempotency_key="ik",
                      requested_at=sub.current_period_start)
    db.add(payment)
    await db.commit()
    fake_toss.payments_by_order["order-wh-1"] = ChargeResult(
        payment_key="pay_wh", order_id="order-wh-1", status="CANCELED",
        raw={"status": "CANCELED"})

    resp = await client.post(
        "/api/v1/webhooks/toss",
        json={"eventType": "PAYMENT_STATUS_CHANGED",
              "data": {"orderId": "order-wh-1", "status": "CANCELED"}},
        headers={"tosspayments-webhook-transmission-id": "wh-pay"})
    assert resp.status_code == 200
    await db.refresh(payment)
    assert payment.status == "CANCELED"


async def test_payment_status_changed_spoofed_payload_not_applied(client, db, cipher, fake_toss):
    """재조회 결과가 없으면(위조 의심) 로컬 상태 불변."""
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan)
    payment = Payment(subscription_id=sub.id, order_id="order-spoof", amount=plan.price,
                      payment_type="RENEWAL", status="DONE", idempotency_key="ik2",
                      requested_at=sub.current_period_start)
    db.add(payment)
    await db.commit()
    # fake_toss.payments_by_order 에 미등록 → 재조회 None

    await client.post(
        "/api/v1/webhooks/toss",
        json={"eventType": "PAYMENT_STATUS_CHANGED",
              "data": {"orderId": "order-spoof", "status": "CANCELED"}},
        headers={"tosspayments-webhook-transmission-id": "wh-spoof"})
    await db.refresh(payment)
    assert payment.status == "DONE"  # 변조 반영 안 됨


async def test_webhook_from_unallowed_ip_rejected(settings, engine, fake_toss, email_sender):
    """토스 인바운드 IP 목록 밖에서 온 웹훅은 403."""
    blocked = settings.model_copy(update={"toss_webhook_allowed_ips": ["10.0.0.1"]})
    application = create_app(blocked, toss_client=fake_toss,
                             email_sender=email_sender, engine=engine)
    async with LifespanManager(application):
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as c:
            resp = await c.post("/api/v1/webhooks/toss",
                                json={"eventType": "BILLING_DELETED", "data": {}})
    assert resp.status_code == 403
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/integration/test_webhooks.py -v
```
Expected: FAIL

- [ ] **Step 3: 구현** — `app/services/webhooks.py`

```python
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.security import sha256_hex
from app.models import (
    Payment,
    PaymentStatus,
    Service,
    Subscription,
    SubscriptionStatus,
    WebhookEvent,
    WebhookStatus,
)
from app.notifications.email import EmailSender
from app.toss.client import TossClient

logger = logging.getLogger("payment.webhooks")


async def handle_webhook(db: AsyncSession, toss: TossClient, email_sender: EmailSender,
                         *, transmission_id: str | None, payload: dict) -> WebhookEvent:
    event_type = str(payload.get("eventType", "UNKNOWN"))
    tid = transmission_id or f"gen-{uuid.uuid4().hex}"

    existing = await db.scalar(select(WebhookEvent).where(
        WebhookEvent.transmission_id == tid))
    if existing is not None:
        return existing  # 중복 수신 — 멱등 처리

    event = WebhookEvent(transmission_id=tid, event_type=event_type, payload=payload)
    db.add(event)
    await db.flush()
    try:
        if event_type == "BILLING_DELETED":
            await _handle_billing_deleted(db, email_sender, payload)
            event.status = WebhookStatus.PROCESSED
        elif event_type == "PAYMENT_STATUS_CHANGED":
            await _handle_payment_status_changed(db, toss, payload)
            event.status = WebhookStatus.PROCESSED
        else:
            event.status = WebhookStatus.IGNORED
    except Exception:
        logger.exception("webhook 처리 실패: %s", event_type)
        event.status = WebhookStatus.FAILED
    event.processed_at = utcnow()
    await db.commit()
    return event


async def _handle_billing_deleted(db: AsyncSession, email_sender: EmailSender,
                                  payload: dict) -> None:
    data = payload.get("data") or {}
    billing_key = str(data.get("billingKey", ""))
    if not billing_key:
        return
    sub = await db.scalar(select(Subscription).where(
        Subscription.billing_key_hash == sha256_hex(billing_key),
        Subscription.status.in_((SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE,
                                 SubscriptionStatus.CANCELED))))
    if sub is None:
        return
    service = await db.get(Service, sub.service_id)
    await email_sender.send(
        service.manager_email,
        f"[결제시스템] 빌링키 삭제 감지 — {service.name}",
        f"사용자 {sub.external_user_id}의 빌링키가 토스에서 삭제되었습니다 "
        f"(사유: {data.get('reason', '알 수 없음')}).\n"
        f"다음 갱신 결제가 실패할 수 있으니 카드 재등록을 안내해주세요.")


async def _handle_payment_status_changed(db: AsyncSession, toss: TossClient,
                                         payload: dict) -> None:
    """페이로드는 신뢰하지 않는다 — orderId만 취해 토스 API 재조회로 상태 확정."""
    data = payload.get("data") or {}
    order_id = str(data.get("orderId", ""))
    if not order_id:
        return
    payment = await db.scalar(select(Payment).where(Payment.order_id == order_id))
    if payment is None:
        return  # 우리 주문이 아님
    verified = await toss.get_payment_by_order_id(order_id)
    if verified is None:
        return  # 토스에서 확인 불가 — 위조 의심, 무시
    if verified.status == "CANCELED" and payment.status != PaymentStatus.CANCELED:
        payment.status = PaymentStatus.CANCELED
        payment.raw_response = verified.raw
```

`app/api/v1/webhooks.py`

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_client_ip,
    get_db,
    get_email_sender,
    get_settings,
    get_toss,
)
from app.core.config import Settings
from app.core.errors import PermissionDeniedError
from app.notifications.email import EmailSender
from app.services.webhooks import handle_webhook
from app.toss.client import TossClient

router = APIRouter()


@router.post("/webhooks/toss")
async def toss_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    toss: TossClient = Depends(get_toss),
    settings: Settings = Depends(get_settings),
    email_sender: EmailSender = Depends(get_email_sender),
):
    if settings.webhook_ip_check_enabled:
        ip = get_client_ip(request, settings)
        if ip not in settings.toss_webhook_allowed_ips:
            raise PermissionDeniedError("허용되지 않은 요청입니다")
    payload = await request.json()
    tid = request.headers.get("tosspayments-webhook-transmission-id")
    event = await handle_webhook(db, toss, email_sender,
                                 transmission_id=tid, payload=payload)
    return {"status": event.status}
```

`app/api/v1/__init__.py` 전체 교체:

```python
from fastapi import APIRouter

from app.api.v1 import payments, plans, subscriptions, webhooks

router = APIRouter()
router.include_router(plans.router, tags=["plans"])
router.include_router(subscriptions.router, tags=["subscriptions"])
router.include_router(payments.router, tags=["payments"])
router.include_router(webhooks.router, tags=["webhooks"])
```

- [ ] **Step 4: 통과 확인**

```bash
uv run pytest tests/integration/test_webhooks.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/webhooks.py app/api/v1 tests/integration/test_webhooks.py
git commit -m "feat: 토스 웹훅(IP검증/중복차단/재조회 확정)"
```

---

### Task 18: 스케줄러 연결 + CLI(create-admin)

**Files:**
- Create: `app/scheduler/__init__.py`, `app/scheduler/runner.py`, `app/cli.py`
- Modify: `app/main.py` (스케줄러 lifespan 연결), `app/services/auth.py` (create_system_admin 추가)
- Test: `tests/integration/test_scheduler.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/integration/test_scheduler.py`

```python
from datetime import timedelta

from app.core.clock import utcnow
from app.scheduler.runner import GLOBAL_LOCK_KEY, run_renewals, start_scheduler
from app.services.auth import create_system_admin
from tests.factories import create_plan, create_service, create_subscription


async def test_run_renewals_processes_due(app, db, redis_client, cipher, fake_toss):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="u-sch",
                              period_start=utcnow() - timedelta(days=31),
                              period_end=utcnow() - timedelta(minutes=1),
                              next_billing_at=utcnow() - timedelta(minutes=1))
    stats = await run_renewals(app)
    assert stats["renewed"] == 1
    assert len(fake_toss.charges) == 1


async def test_run_renewals_skips_when_global_lock_held(app, db, redis_client,
                                                        cipher, fake_toss):
    await redis_client.set(GLOBAL_LOCK_KEY, "1", ex=60)
    assert await run_renewals(app) is None
    assert fake_toss.charges == []


async def test_start_scheduler_registers_interval_job(app):
    scheduler = start_scheduler(app)
    try:
        assert len(scheduler.get_jobs()) == 1
    finally:
        scheduler.shutdown(wait=False)


async def test_create_system_admin(db):
    user = await create_system_admin(db, email="root@medisolveai.com",
                                     password="RootPassword1!")
    assert user.role == "SYSTEM_ADMIN"
    assert user.status == "ACTIVE"
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/integration/test_scheduler.py -v
```
Expected: FAIL

- [ ] **Step 3: 구현** — `app/scheduler/runner.py` (+ 빈 `app/scheduler/__init__.py`)

```python
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.services.renewals import process_due

logger = logging.getLogger("payment.scheduler")

GLOBAL_LOCK_KEY = "lock:scheduler:renewals"
GLOBAL_LOCK_TTL = 240


async def run_renewals(app: FastAPI) -> dict | None:
    """갱신 배치 1회. 전역 Redis 락으로 다중 인스턴스 중복 실행 방지."""
    redis = app.state.redis
    if not await redis.set(GLOBAL_LOCK_KEY, "1", nx=True, ex=GLOBAL_LOCK_TTL):
        logger.info("renewal batch skipped — 다른 인스턴스가 실행 중")
        return None
    try:
        stats = await process_due(app.state.session_factory, redis, app.state.toss,
                                  app.state.cipher, app.state.email_sender)
        logger.info("renewal batch done: %s", stats)
        return stats
    finally:
        await redis.delete(GLOBAL_LOCK_KEY)


def start_scheduler(app: FastAPI) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(run_renewals, "interval",
                      minutes=app.state.settings.scheduler_interval_minutes,
                      args=[app], max_instances=1, coalesce=True)
    scheduler.start()
    return scheduler
```

- [ ] **Step 4: main.py에 스케줄러 연결** — `app/main.py` 수정

import 블록에 추가:

```python
from app.scheduler.runner import start_scheduler
```

lifespan에서 `app.state.email_sender = ...` 줄 다음의 `yield`를 아래로 교체:

```python
        scheduler = start_scheduler(app) if app_settings.scheduler_enabled else None
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)
```

- [ ] **Step 5: auth 서비스에 create_system_admin 추가** — `app/services/auth.py`

import에 `ConflictError`(from app.core.errors), `UserRole`(from app.models) 추가 후 파일 끝에:

```python
async def create_system_admin(db: AsyncSession, *, email: str, password: str) -> User:
    """CLI에서 호출 — 최초 SYSTEM_ADMIN 계정 생성."""
    _validate_password(password)
    if await db.scalar(select(User).where(User.email == email)):
        raise ConflictError("이미 존재하는 이메일입니다")
    user = User(email=email, password_hash=hash_password(password),
                role=UserRole.SYSTEM_ADMIN, status=UserStatus.ACTIVE)
    db.add(user)
    await record_audit(db, actor_type="SYSTEM", action="user.create_admin",
                       detail={"email": email})
    await db.commit()
    return user
```

- [ ] **Step 6: CLI 작성** — `app/cli.py`

```python
import argparse
import asyncio

from app.core.config import Settings
from app.core.db import create_engine, create_session_factory
from app.services.auth import create_system_admin


async def _create_admin(email: str, password: str) -> None:
    settings = Settings()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as db:
            await create_system_admin(db, email=email, password=password)
        print(f"SYSTEM_ADMIN 생성 완료: {email}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="payment-system")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="시스템 관리자 생성")
    p.add_argument("--email", required=True)
    p.add_argument("--password", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        asyncio.run(_create_admin(args.email, args.password))


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: 통과 확인 + 서버 기동 스모크**

```bash
uv run pytest tests/integration/test_scheduler.py -v
```
Expected: 4 passed

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --port 8000 &
sleep 3 && curl -s http://localhost:8000/health
kill %1
```
Expected: `{"status":"ok"}`

- [ ] **Step 8: Commit**

```bash
git add app/scheduler app/cli.py app/main.py app/services/auth.py tests/integration/test_scheduler.py
git commit -m "feat: APScheduler 갱신 배치 연결 + create-admin CLI"
```

---

### Task 19: Admin 기반 — 세션/CSRF/로그인/레이아웃/대시보드

**Files:**
- Create: `app/admin/__init__.py`, `app/admin/deps.py`
- Create: `app/admin/routes/__init__.py`, `app/admin/routes/auth.py`, `app/admin/routes/dashboard.py`
- Create: `app/admin/templates/base.html`, `login.html`, `setup_password.html`, `dashboard.html`
- Create: `app/static/admin.css`
- Modify: `app/main.py` (admin 라우터/static/핸들러), `tests/helpers.py` (admin_login, get_csrf)
- Test: `tests/e2e/__init__.py`, `tests/e2e/conftest.py`, `tests/e2e/test_admin_flows.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/conftest.py`

```python
import pytest


@pytest.fixture(autouse=True)
def _auto_clean(clean_db, clean_redis):
    """E2E 테스트 후 DB/Redis 초기화."""
```

`tests/helpers.py` 끝에 추가:

```python
async def admin_login(client, email: str, password: str) -> str:
    """admin 로그인 후 세션 ID 반환. 쿠키는 client에 자동 저장됨."""
    resp = await client.post("/admin/login", data={"email": email, "password": password})
    assert resp.status_code == 303, f"login failed: {resp.status_code} {resp.text[:200]}"
    return resp.cookies["admin_session"]


async def get_csrf(redis_client, session_id: str) -> str:
    return await redis_client.hget(f"session:{session_id}", "csrf_token")
```

`tests/e2e/test_admin_flows.py`

```python
from datetime import timedelta

from app.core.clock import utcnow
from app.core.security import sha256_hex
from app.models import PasswordSetupToken
from tests.factories import create_user
from tests.helpers import admin_login, get_csrf


async def test_login_page_renders(client):
    resp = await client.get("/admin/login")
    assert resp.status_code == 200
    assert "로그인" in resp.text


async def test_login_success_and_dashboard(client, db):
    user, pw = await create_user(db)
    await admin_login(client, user.email, pw)
    resp = await client.get("/admin")
    assert resp.status_code == 200
    assert "대시보드" in resp.text


async def test_session_cookie_flags(client, db):
    user, pw = await create_user(db)
    resp = await client.post("/admin/login", data={"email": user.email, "password": pw})
    set_cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


async def test_wrong_password_shows_error(client, db):
    user, _ = await create_user(db)
    resp = await client.post("/admin/login",
                             data={"email": user.email, "password": "nope"})
    assert resp.status_code == 200
    assert "올바르지 않습니다" in resp.text


async def test_anonymous_redirected_to_login(client):
    resp = await client.get("/admin")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/login"


async def test_htmx_request_gets_hx_redirect(client):
    resp = await client.get("/admin", headers={"HX-Request": "true"})
    assert resp.status_code == 204
    assert resp.headers["hx-redirect"] == "/admin/login"


async def test_logout_destroys_session(client, db, redis_client):
    user, pw = await create_user(db)
    session_id = await admin_login(client, user.email, pw)
    csrf = await get_csrf(redis_client, session_id)
    resp = await client.post("/admin/logout", data={"csrf_token": csrf})
    assert resp.status_code == 303
    after = await client.get("/admin")
    assert after.status_code == 303  # 세션 무효 — 로그인으로


async def test_logout_without_csrf_rejected(client, db):
    user, pw = await create_user(db)
    await admin_login(client, user.email, pw)
    resp = await client.post("/admin/logout", data={})
    assert resp.status_code == 403


async def test_setup_password_full_flow(client, db):
    user, _ = await create_user(db, status="PENDING")
    token = "setup-" + "z" * 26
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()

    page = await client.get(f"/admin/setup-password?token={token}")
    assert page.status_code == 200

    resp = await client.post("/admin/setup-password",
                             data={"token": token, "password": "BrandNewPass12",
                                   "password_confirm": "BrandNewPass12"})
    assert resp.status_code == 303
    await admin_login(client, user.email, "BrandNewPass12")  # 새 비밀번호로 로그인 성공


async def test_setup_password_mismatch_shows_error(client, db):
    user, _ = await create_user(db, status="PENDING")
    token = "setup-" + "y" * 26
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + timedelta(hours=1)))
    await db.commit()
    resp = await client.post("/admin/setup-password",
                             data={"token": token, "password": "BrandNewPass12",
                                   "password_confirm": "Different12345"})
    assert resp.status_code == 200
    assert "일치하지 않습니다" in resp.text
```

빈 파일 생성:

```bash
mkdir -p tests/e2e app/admin/routes app/admin/templates app/static
touch tests/e2e/__init__.py
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/e2e/test_admin_flows.py -v
```
Expected: FAIL (404)

- [ ] **Step 3: 구현** — `app/admin/deps.py`

```python
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_redis, get_settings
from app.core.config import Settings
from app.core.errors import PermissionDeniedError
from app.core.security import constant_time_equals
from app.models import User, UserRole, UserStatus
from app.services import auth as auth_service

SESSION_COOKIE = "admin_session"


class AdminAuthRequired(Exception):
    """미인증 — /admin/login으로 리다이렉트."""


@dataclass
class AdminContext:
    user: User
    session_id: str
    csrf_token: str


async def require_user(request: Request,
                       db: AsyncSession = Depends(get_db),
                       redis: Redis = Depends(get_redis),
                       settings: Settings = Depends(get_settings)) -> AdminContext:
    session_id = request.cookies.get(SESSION_COOKIE, "")
    data = await auth_service.get_session(redis, settings, session_id)
    if data is None:
        raise AdminAuthRequired()
    user = await auth_service.get_user(db, data.get("user_id", ""))
    if user is None or user.status != UserStatus.ACTIVE:
        raise AdminAuthRequired()
    return AdminContext(user=user, session_id=session_id,
                        csrf_token=data.get("csrf_token", ""))


def require_role(*roles: str):
    async def checker(ctx: AdminContext = Depends(require_user)) -> AdminContext:
        if ctx.user.role not in roles:
            raise PermissionDeniedError("접근 권한이 없습니다")
        return ctx
    return checker


require_admin = require_role(UserRole.SYSTEM_ADMIN)
require_any = require_role(UserRole.SYSTEM_ADMIN, UserRole.SERVICE_MANAGER)


async def validate_csrf(request: Request, ctx: AdminContext) -> None:
    """모든 admin POST는 호출 필수. 폼 hidden 필드 또는 X-CSRF-Token 헤더."""
    form = await request.form()
    token = str(form.get("csrf_token", "")) or request.headers.get("x-csrf-token", "")
    if not token or not constant_time_equals(token, ctx.csrf_token):
        raise PermissionDeniedError("CSRF 토큰이 유효하지 않습니다")


def register_admin_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AdminAuthRequired)
    async def auth_required_handler(request: Request, exc: AdminAuthRequired):
        if request.headers.get("hx-request"):
            return Response(status_code=204, headers={"HX-Redirect": "/admin/login"})
        return RedirectResponse("/admin/login", status_code=303)
```

`app/admin/__init__.py`

```python
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.admin.deps import AdminContext

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def render(request: Request, name: str, ctx: AdminContext | None = None, **extra):
    context = {"ctx": ctx, **extra}
    return templates.TemplateResponse(request, name, context)


from app.admin.routes import auth, dashboard  # noqa: E402

router = APIRouter()
router.include_router(auth.router)
router.include_router(dashboard.router)
```

`app/admin/routes/__init__.py` — 빈 파일.

`app/admin/routes/auth.py`

```python
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import SESSION_COOKIE, AdminContext, require_any, validate_csrf
from app.api.deps import get_client_ip, get_db, get_redis, get_settings
from app.core.config import Settings
from app.core.errors import AuthenticationError, InputValidationError
from app.services import auth as auth_service

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    return render(request, "login.html", error=None)


@router.post("/login")
async def login_submit(request: Request,
                       db: AsyncSession = Depends(get_db),
                       redis: Redis = Depends(get_redis),
                       settings: Settings = Depends(get_settings)):
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
    ip = get_client_ip(request, settings)
    try:
        session_id, _user = await auth_service.login(
            db, redis, settings, email=email, password=password, ip=ip)
    except AuthenticationError as exc:
        return render(request, "login.html", error=exc.message)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, session_id, httponly=True, samesite="lax",
        secure=settings.environment == "prod",
        max_age=settings.session_ttl_seconds, path="/")
    return response


@router.post("/logout")
async def logout(request: Request,
                 ctx: AdminContext = Depends(require_any),
                 redis: Redis = Depends(get_redis)):
    await validate_csrf(request, ctx)
    await auth_service.logout(redis, ctx.session_id)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/setup-password")
async def setup_password_page(request: Request, token: str = ""):
    return render(request, "setup_password.html", token=token, error=None)


@router.post("/setup-password")
async def setup_password_submit(request: Request,
                                db: AsyncSession = Depends(get_db)):
    form = await request.form()
    token = str(form.get("token", ""))
    password = str(form.get("password", ""))
    confirm = str(form.get("password_confirm", ""))
    if password != confirm:
        return render(request, "setup_password.html", token=token,
                      error="비밀번호가 일치하지 않습니다")
    try:
        await auth_service.setup_password(db, token=token, password=password)
    except InputValidationError as exc:
        return render(request, "setup_password.html", token=token, error=exc.message)
    return RedirectResponse("/admin/login", status_code=303)
```

`app/admin/routes/dashboard.py`

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_any
from app.api.deps import get_db
from app.models import Payment, Subscription, UserRole

router = APIRouter()


@router.get("")
async def dashboard(request: Request,
                    ctx: AdminContext = Depends(require_any),
                    db: AsyncSession = Depends(get_db)):
    scoped = ctx.user.role != UserRole.SYSTEM_ADMIN

    async def count_status(status: str) -> int:
        q = select(func.count()).select_from(Subscription).where(
            Subscription.status == status)
        if scoped:
            q = q.where(Subscription.service_id == ctx.user.service_id)
        return await db.scalar(q) or 0

    stats = {
        "active": await count_status("ACTIVE"),
        "past_due": await count_status("PAST_DUE"),
        "canceled": await count_status("CANCELED"),
        "expired": await count_status("EXPIRED"),
    }
    recent_q = (select(Payment, Subscription)
                .join(Subscription, Payment.subscription_id == Subscription.id)
                .order_by(Payment.requested_at.desc()).limit(10))
    if scoped:
        recent_q = recent_q.where(Subscription.service_id == ctx.user.service_id)
    recent = (await db.execute(recent_q)).all()
    return render(request, "dashboard.html", ctx=ctx, stats=stats, recent=recent)
```

- [ ] **Step 4: 템플릿/CSS 작성** — `app/admin/templates/base.html`

```html
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}결제 관리{% endblock %} — Payment Admin</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
  <link rel="stylesheet" href="/static/admin.css">
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
</head>
<body>
{% if ctx %}
<aside class="sidebar">
  <div class="brand">PAY Admin</div>
  <nav>
    <a href="/admin">대시보드</a>
    {% if ctx.user.role == 'SYSTEM_ADMIN' %}
    <a href="/admin/services">서비스</a>
    <a href="/admin/users">계정</a>
    <a href="/admin/audit">감사 로그</a>
    {% endif %}
    <a href="/admin/plans">요금제</a>
    <a href="/admin/subscriptions">구독</a>
    <a href="/admin/payments">결제</a>
  </nav>
  <form method="post" action="/admin/logout">
    <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
    <button type="submit" class="btn btn-ghost">로그아웃 ({{ ctx.user.email }})</button>
  </form>
</aside>
{% endif %}
<main class="content">
  {% block content %}{% endblock %}
</main>
</body>
</html>
```

`app/admin/templates/login.html`

```html
{% extends "base.html" %}
{% block title %}로그인{% endblock %}
{% block content %}
<div class="login-wrap card">
  <h1>결제 관리 로그인</h1>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="post" action="/admin/login">
    <label for="email">이메일</label>
    <input type="email" id="email" name="email" required autofocus>
    <label for="password">비밀번호</label>
    <input type="password" id="password" name="password" required>
    <div class="actions"><button class="btn btn-primary" type="submit">로그인</button></div>
  </form>
</div>
{% endblock %}
```

`app/admin/templates/setup_password.html`

```html
{% extends "base.html" %}
{% block title %}비밀번호 설정{% endblock %}
{% block content %}
<div class="login-wrap card">
  <h1>비밀번호 설정</h1>
  {% if error %}<div class="error">{{ error }}</div>{% endif %}
  <form method="post" action="/admin/setup-password">
    <input type="hidden" name="token" value="{{ token }}">
    <label for="password">새 비밀번호 (10자 이상)</label>
    <input type="password" id="password" name="password" minlength="10" required>
    <label for="password_confirm">비밀번호 확인</label>
    <input type="password" id="password_confirm" name="password_confirm" required>
    <div class="actions"><button class="btn btn-primary" type="submit">설정</button></div>
  </form>
</div>
{% endblock %}
```

`app/admin/templates/dashboard.html`

```html
{% extends "base.html" %}
{% block title %}대시보드{% endblock %}
{% block content %}
<h1>대시보드</h1>
<div class="stats">
  <div class="card"><div class="stat-value">{{ stats.active }}</div><div class="stat-label">활성 구독</div></div>
  <div class="card"><div class="stat-value">{{ stats.past_due }}</div><div class="stat-label">미수금(PAST_DUE)</div></div>
  <div class="card"><div class="stat-value">{{ stats.canceled }}</div><div class="stat-label">취소 예정</div></div>
  <div class="card"><div class="stat-value">{{ stats.expired }}</div><div class="stat-label">만료</div></div>
</div>
<h2>최근 결제</h2>
<div class="card">
<table>
  <thead><tr><th>주문번호</th><th>사용자</th><th>금액</th><th>상태</th><th>요청 시각</th></tr></thead>
  <tbody>
  {% for payment, sub in recent %}
    <tr>
      <td>{{ payment.order_id[:20] }}…</td>
      <td>{{ sub.external_user_id }}</td>
      <td>{{ "{:,}".format(payment.amount) }}원</td>
      <td><span class="badge badge-{{ payment.status }}">{{ payment.status }}</span></td>
      <td>{{ payment.requested_at.strftime("%Y-%m-%d %H:%M") }}</td>
    </tr>
  {% else %}
    <tr><td colspan="5">결제 내역이 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`app/static/admin.css` — Centurion Suite 토큰(`docs/design/centurion-suite-handoff/`) 기반

```css
:root {
  --gray-100:#FBFBFB; --gray-200:#F3F3F3; --gray-300:#E3E3E3; --gray-400:#D6D6D6;
  --gray-500:#CFCFCF; --gray-600:#9F9F9F; --gray-700:#6E6E6E; --gray-800:#3E3E3E;
  --primary:#476CFF; --primary-100:#F0F4FF; --primary-300:#DDE6FF; --primary-500:#97B5FF;
  --red:#FF4E51; --red-100:#FFEFEF; --hover-dark:#222943;
}
* { box-sizing: border-box; margin: 0; }
body { font-family: Pretendard, -apple-system, sans-serif; background: var(--gray-100);
       color: #000; display: flex; min-height: 100vh; font-size: 14px; line-height: 1.6; }
.sidebar { width: 220px; background: #fff; border-right: 1px solid var(--gray-300);
           padding: 24px 16px; display: flex; flex-direction: column; gap: 8px; }
.brand { font-size: 18px; font-weight: 600; color: var(--primary); margin-bottom: 16px; }
.sidebar nav { display: flex; flex-direction: column; gap: 4px; flex: 1; }
.sidebar nav a { padding: 8px 12px; border-radius: 8px; color: var(--gray-800);
                 text-decoration: none; font-weight: 500; }
.sidebar nav a:hover { background: var(--primary-100); color: var(--primary); }
.content { flex: 1; padding: 32px; max-width: 1080px; }
h1 { font-size: 24px; font-weight: 600; line-height: 1.4; margin-bottom: 24px; }
h2 { font-size: 18px; font-weight: 600; margin: 24px 0 12px; }
.card { background: #fff; border: 1px solid var(--gray-300); border-radius: 12px;
        padding: 24px; margin-bottom: 16px; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
.stat-value { font-size: 32px; font-weight: 600; }
.stat-label { color: var(--gray-700); font-size: 12px; }
table { width: 100%; border-collapse: collapse; background: #fff; }
th { text-align: left; font-weight: 500; color: var(--gray-700); font-size: 12px;
     padding: 8px 12px; border-bottom: 1px solid var(--gray-300); }
td { padding: 10px 12px; border-bottom: 1px solid var(--gray-200); }
label { display: block; font-weight: 500; margin: 12px 0 4px; }
input, select, textarea { width: 100%; max-width: 420px; padding: 10px 12px;
                border: 1px solid var(--gray-400); border-radius: 8px; font: inherit; }
input:focus, select:focus, textarea:focus { outline: none; border-color: var(--primary); }
.btn { display: inline-block; padding: 10px 20px; border-radius: 8px; border: none;
       font: inherit; font-weight: 500; cursor: pointer; text-decoration: none; }
.btn-primary { background: var(--primary); color: #fff; }
.btn-primary:hover { background: var(--hover-dark); }
.btn-danger { background: var(--red); color: #fff; }
.btn-ghost { background: transparent; color: var(--gray-700); }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px;
         font-weight: 500; }
.badge-ACTIVE, .badge-DONE { background: var(--primary-100); color: var(--primary); }
.badge-PAST_DUE, .badge-PENDING { background: var(--gray-200); color: var(--gray-800); }
.badge-CANCELED { background: var(--gray-200); color: var(--gray-700); }
.badge-EXPIRED, .badge-FAILED { background: var(--red-100); color: var(--red); }
.error { background: var(--red-100); color: var(--red); padding: 12px 16px;
         border-radius: 8px; margin-bottom: 16px; }
.notice { background: var(--primary-100); color: var(--primary); padding: 12px 16px;
          border-radius: 8px; margin-bottom: 16px; }
.key-box { font-family: ui-monospace, monospace; background: var(--gray-200);
           padding: 12px; border-radius: 8px; word-break: break-all; margin: 8px 0; }
.login-wrap { margin: 10vh auto; width: 360px; }
.actions { display: flex; gap: 8px; margin-top: 16px; }
```

- [ ] **Step 5: main.py에 admin 연결** — `app/main.py` 수정

import 블록에 추가:

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app.admin import router as admin_router
from app.admin.deps import register_admin_exception_handlers
```

`app.include_router(api_v1_router, prefix="/api/v1")` 다음에 추가:

```python
    app.include_router(admin_router, prefix="/admin")
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")),
              name="static")
    register_admin_exception_handlers(app)
```

- [ ] **Step 6: 통과 확인**

```bash
uv run pytest tests/e2e/test_admin_flows.py -v
```
Expected: 10 passed

- [ ] **Step 7: Commit**

```bash
git add app/admin app/static app/main.py tests/e2e tests/helpers.py
git commit -m "feat: admin 기반(세션 쿠키/CSRF/로그인/대시보드, Centurion 토큰)"
```

---

### Task 20: Admin — 서비스 관리(키 1회 표시) + 요금제 관리 화면

**Files:**
- Create: `app/admin/routes/services.py`, `app/admin/routes/plans.py`
- Create: `app/admin/templates/services/list.html`, `services/new.html`, `services/keys.html`, `services/detail.html`, `plans/list.html`, `plans/form.html`
- Modify: `app/admin/__init__.py` (라우터 추가)
- Test: `tests/e2e/test_admin_services_plans.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_admin_services_plans.py`

```python
import re

from sqlalchemy import select

from app.models import Plan, Service, User
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login, get_csrf


async def _login_admin(client, db, redis_client):
    user, pw = await create_user(db, role="SYSTEM_ADMIN")
    session_id = await admin_login(client, user.email, pw)
    return await get_csrf(redis_client, session_id)


async def _login_manager(client, db, redis_client, service):
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=service.id)
    session_id = await admin_login(client, user.email, pw)
    return await get_csrf(redis_client, session_id)


async def test_register_service_shows_keys_once(client, db, redis_client, email_sender):
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "mediness", "manager_email": "mgr@medisolveai.com",
        "allowed_ips": "10.0.0.1, 10.0.0.2"})
    assert resp.status_code == 200
    api_key = re.search(r'data-key="(svc_[^"]+)"', resp.text).group(1)
    secret = re.search(r'data-secret="([^"]+)"', resp.text).group(1)
    assert api_key and secret

    svc = await db.scalar(select(Service).where(Service.name == "mediness"))
    assert svc.allowed_ips == ["10.0.0.1", "10.0.0.2"]
    # 담당자 계정 생성 + 안내 메일
    manager = await db.scalar(select(User).where(User.email == "mgr@medisolveai.com"))
    assert manager.status == "PENDING"
    assert len(email_sender.sent) == 1
    # 상세 페이지에는 키가 다시 노출되지 않음
    detail = await client.get(f"/admin/services/{svc.id}")
    assert api_key not in detail.text
    assert secret not in detail.text


async def test_register_service_invalid_ip_shows_error(client, db, redis_client):
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "bad", "manager_email": "b@x.com",
        "allowed_ips": "not-an-ip"})
    assert resp.status_code == 200
    assert "유효하지 않은 IP" in resp.text


async def test_rotate_keys_invalidates_old_hash(client, db, redis_client, cipher):
    svc, old_key, _ = await create_service(db, cipher)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/rotate-keys",
                             data={"csrf_token": csrf})
    assert resp.status_code == 200
    new_key = re.search(r'data-key="(svc_[^"]+)"', resp.text).group(1)
    assert new_key != old_key


async def test_update_ips(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/ips",
                             data={"csrf_token": csrf, "allowed_ips": "192.168.1.1"})
    assert resp.status_code == 303
    await db.refresh(svc)
    assert svc.allowed_ips == ["192.168.1.1"]


async def test_delete_service_with_subscription_blocked(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan)
    csrf = await _login_admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/delete",
                             data={"csrf_token": csrf}, follow_redirects=True)
    assert "삭제할 수 없습니다" in resp.text
    assert await db.get(Service, svc.id) is not None


async def test_manager_cannot_access_services_admin(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    await _login_manager(client, db, redis_client, svc)
    resp = await client.get("/admin/services")
    assert resp.status_code == 403


async def test_manager_creates_plan(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post("/admin/plans", data={
        "csrf_token": csrf, "name": "프로", "price": "29000",
        "billing_cycle": "MONTH", "cycle_days": "",
        "first_payment_type": "DISCOUNT_PERCENT", "first_payment_value": "30"},
        follow_redirects=True)
    assert resp.status_code == 200
    plan = await db.scalar(select(Plan).where(Plan.name == "프로"))
    assert plan.price == 29000
    assert plan.service_id == svc.id


async def test_manager_plan_validation_error_rendered(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post("/admin/plans", data={
        "csrf_token": csrf, "name": "x", "price": "0", "billing_cycle": "MONTH",
        "cycle_days": "", "first_payment_type": "NONE", "first_payment_value": ""})
    assert resp.status_code == 200
    assert "1원 이상" in resp.text


async def test_manager_edits_and_archives_plan(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    csrf = await _login_manager(client, db, redis_client, svc)
    resp = await client.post(f"/admin/plans/{plan.id}", data={
        "csrf_token": csrf, "name": "수정됨", "price": "15000",
        "first_payment_type": "NONE", "first_payment_value": ""},
        follow_redirects=True)
    assert resp.status_code == 200
    await db.refresh(plan)
    assert plan.name == "수정됨"

    await client.post(f"/admin/plans/{plan.id}/archive", data={"csrf_token": csrf})
    await db.refresh(plan)
    assert plan.status == "ARCHIVED"


async def test_manager_cannot_touch_other_service_plan(client, db, redis_client, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="own-svc")
    svc_b, _, _ = await create_service(db, cipher, name="other-svc")
    plan_b = await create_plan(db, svc_b)
    csrf = await _login_manager(client, db, redis_client, svc_a)
    resp = await client.post(f"/admin/plans/{plan_b.id}", data={
        "csrf_token": csrf, "name": "해킹", "price": "1",
        "first_payment_type": "NONE", "first_payment_value": ""})
    assert resp.status_code == 404
    await db.refresh(plan_b)
    assert plan_b.name != "해킹"
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/e2e/test_admin_services_plans.py -v
```
Expected: FAIL (404)

- [ ] **Step 3: 구현** — `app/admin/routes/services.py`

```python
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_admin, validate_csrf
from app.api.deps import get_cipher, get_db, get_email_sender, get_settings
from app.core.config import Settings
from app.core.crypto import AesGcmCipher
from app.core.errors import DomainError, NotFoundError
from app.models import Plan, Service, Subscription
from app.notifications.email import EmailSender
from app.services import registry

router = APIRouter()


def _parse_ips(raw: str) -> list[str]:
    return [ip.strip() for ip in raw.split(",") if ip.strip()]


@router.get("/services")
async def services_list(request: Request, ctx: AdminContext = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)):
    services = await registry.list_services(db)
    return render(request, "services/list.html", ctx=ctx, services=services)


@router.get("/services/new")
async def services_new(request: Request, ctx: AdminContext = Depends(require_admin)):
    return render(request, "services/new.html", ctx=ctx, error=None)


@router.post("/services")
async def services_create(request: Request,
                          ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db),
                          cipher: AesGcmCipher = Depends(get_cipher),
                          email_sender: EmailSender = Depends(get_email_sender),
                          settings: Settings = Depends(get_settings)):
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        creds = await registry.register_service(
            db, cipher, email_sender,
            name=str(form.get("name", "")),
            allowed_ips=_parse_ips(str(form.get("allowed_ips", ""))),
            manager_email=str(form.get("manager_email", "")).strip(),
            base_url=settings.base_url, actor_user_id=ctx.user.id)
    except DomainError as exc:
        return render(request, "services/new.html", ctx=ctx, error=exc.message)
    return render(request, "services/keys.html", ctx=ctx, service=creds.service,
                  api_key=creds.api_key, hmac_secret=creds.hmac_secret)


@router.get("/services/{service_id}")
async def services_detail(service_id: uuid.UUID, request: Request,
                          ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    plan_count = await db.scalar(select(func.count()).select_from(Plan)
                                 .where(Plan.service_id == service_id)) or 0
    sub_count = await db.scalar(select(func.count()).select_from(Subscription)
                                .where(Subscription.service_id == service_id)) or 0
    return render(request, "services/detail.html", ctx=ctx, service=service,
                  plan_count=plan_count, sub_count=sub_count,
                  error=request.query_params.get("error"))


@router.post("/services/{service_id}/rotate-keys")
async def services_rotate(service_id: uuid.UUID, request: Request,
                          ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db),
                          cipher: AesGcmCipher = Depends(get_cipher)):
    await validate_csrf(request, ctx)
    api_key, hmac_secret = await registry.rotate_keys(db, cipher, service_id,
                                                      actor_user_id=ctx.user.id)
    service = await db.get(Service, service_id)
    return render(request, "services/keys.html", ctx=ctx, service=service,
                  api_key=api_key, hmac_secret=hmac_secret)


@router.post("/services/{service_id}/ips")
async def services_update_ips(service_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_admin),
                              db: AsyncSession = Depends(get_db)):
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        await registry.update_allowed_ips(
            db, service_id, _parse_ips(str(form.get("allowed_ips", ""))),
            actor_user_id=ctx.user.id)
    except DomainError as exc:
        return RedirectResponse(f"/admin/services/{service_id}?error={exc.message}",
                                status_code=303)
    return RedirectResponse(f"/admin/services/{service_id}", status_code=303)


@router.post("/services/{service_id}/status")
async def services_set_status(service_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_admin),
                              db: AsyncSession = Depends(get_db)):
    await validate_csrf(request, ctx)
    form = await request.form()
    await registry.set_service_status(db, service_id, str(form.get("status", "")),
                                      actor_user_id=ctx.user.id)
    return RedirectResponse(f"/admin/services/{service_id}", status_code=303)


@router.post("/services/{service_id}/delete")
async def services_delete(service_id: uuid.UUID, request: Request,
                          ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    await validate_csrf(request, ctx)
    try:
        await registry.delete_service(db, service_id, actor_user_id=ctx.user.id)
    except DomainError as exc:
        return RedirectResponse(f"/admin/services/{service_id}?error={exc.message}",
                                status_code=303)
    return RedirectResponse("/admin/services", status_code=303)
```

`app/admin/routes/plans.py`

```python
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_any, require_role, validate_csrf
from app.api.deps import get_db
from app.core.errors import DomainError
from app.models import Plan, Service, UserRole
from app.services import plans as plan_service

router = APIRouter()
require_manager = require_role(UserRole.SERVICE_MANAGER)


def _form_plan_fields(form) -> dict:
    def opt_int(key: str) -> int | None:
        raw = str(form.get(key, "")).strip()
        return int(raw) if raw else None

    return {
        "name": str(form.get("name", "")),
        "price": opt_int("price") or 0,
        "first_payment_type": str(form.get("first_payment_type", "NONE")),
        "first_payment_value": opt_int("first_payment_value"),
    }


@router.get("/plans")
async def plans_list(request: Request, ctx: AdminContext = Depends(require_any),
                     db: AsyncSession = Depends(get_db)):
    if ctx.user.role == UserRole.SYSTEM_ADMIN:
        rows = (await db.execute(
            select(Plan, Service).join(Service, Plan.service_id == Service.id)
            .order_by(Plan.created_at))).all()
    else:
        plans = await plan_service.list_plans(db, service_id=ctx.user.service_id)
        service = await db.get(Service, ctx.user.service_id)
        rows = [(p, service) for p in plans]
    return render(request, "plans/list.html", ctx=ctx, rows=rows)


@router.get("/plans/new")
async def plans_new(request: Request, ctx: AdminContext = Depends(require_manager)):
    return render(request, "plans/form.html", ctx=ctx, plan=None, error=None)


@router.post("/plans")
async def plans_create(request: Request, ctx: AdminContext = Depends(require_manager),
                       db: AsyncSession = Depends(get_db)):
    await validate_csrf(request, ctx)
    form = await request.form()
    fields = _form_plan_fields(form)
    cycle_days_raw = str(form.get("cycle_days", "")).strip()
    try:
        await plan_service.create_plan(
            db, service_id=ctx.user.service_id,
            billing_cycle=str(form.get("billing_cycle", "")),
            cycle_days=int(cycle_days_raw) if cycle_days_raw else None,
            actor_user_id=ctx.user.id, **fields)
    except DomainError as exc:
        return render(request, "plans/form.html", ctx=ctx, plan=None, error=exc.message)
    return RedirectResponse("/admin/plans", status_code=303)


@router.get("/plans/{plan_id}/edit")
async def plans_edit(plan_id: uuid.UUID, request: Request,
                     ctx: AdminContext = Depends(require_manager),
                     db: AsyncSession = Depends(get_db)):
    plan = await plan_service.get_plan(db, plan_id=plan_id, service_id=ctx.user.service_id)
    return render(request, "plans/form.html", ctx=ctx, plan=plan, error=None)


@router.post("/plans/{plan_id}")
async def plans_update(plan_id: uuid.UUID, request: Request,
                       ctx: AdminContext = Depends(require_manager),
                       db: AsyncSession = Depends(get_db)):
    await validate_csrf(request, ctx)
    form = await request.form()
    fields = _form_plan_fields(form)
    try:
        await plan_service.update_plan(db, plan_id=plan_id,
                                       service_id=ctx.user.service_id,
                                       actor_user_id=ctx.user.id, **fields)
    except DomainError as exc:
        if exc.http_status == 404:
            raise
        plan = await plan_service.get_plan(db, plan_id=plan_id,
                                           service_id=ctx.user.service_id)
        return render(request, "plans/form.html", ctx=ctx, plan=plan, error=exc.message)
    return RedirectResponse("/admin/plans", status_code=303)


@router.post("/plans/{plan_id}/archive")
async def plans_archive(plan_id: uuid.UUID, request: Request,
                        ctx: AdminContext = Depends(require_manager),
                        db: AsyncSession = Depends(get_db)):
    await validate_csrf(request, ctx)
    await plan_service.archive_plan(db, plan_id=plan_id, service_id=ctx.user.service_id,
                                    actor_user_id=ctx.user.id)
    return RedirectResponse("/admin/plans", status_code=303)


@router.post("/plans/{plan_id}/delete")
async def plans_delete(plan_id: uuid.UUID, request: Request,
                       ctx: AdminContext = Depends(require_manager),
                       db: AsyncSession = Depends(get_db)):
    await validate_csrf(request, ctx)
    try:
        await plan_service.delete_plan(db, plan_id=plan_id,
                                       service_id=ctx.user.service_id,
                                       actor_user_id=ctx.user.id)
    except DomainError as exc:
        if exc.http_status == 404:
            raise
        return RedirectResponse(f"/admin/plans?error={exc.message}", status_code=303)
    return RedirectResponse("/admin/plans", status_code=303)
```

`app/admin/__init__.py`의 라우터 부분 교체:

```python
from app.admin.routes import auth, dashboard, plans, services  # noqa: E402

router = APIRouter()
router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(services.router)
router.include_router(plans.router)
```

- [ ] **Step 4: 템플릿 작성** — `app/admin/templates/services/list.html`

```html
{% extends "base.html" %}
{% block title %}서비스{% endblock %}
{% block content %}
<h1>서비스</h1>
<p style="margin-bottom:16px"><a class="btn btn-primary" href="/admin/services/new">서비스 등록</a></p>
<div class="card">
<table>
  <thead><tr><th>이름</th><th>담당자</th><th>허용 IP</th><th>상태</th><th></th></tr></thead>
  <tbody>
  {% for svc in services %}
    <tr>
      <td><a href="/admin/services/{{ svc.id }}">{{ svc.name }}</a></td>
      <td>{{ svc.manager_email }}</td>
      <td>{{ svc.allowed_ips | join(", ") }}</td>
      <td><span class="badge badge-{{ svc.status }}">{{ svc.status }}</span></td>
      <td><a href="/admin/services/{{ svc.id }}">상세</a></td>
    </tr>
  {% else %}
    <tr><td colspan="5">등록된 서비스가 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`app/admin/templates/services/new.html`

```html
{% extends "base.html" %}
{% block title %}서비스 등록{% endblock %}
{% block content %}
<h1>서비스 등록</h1>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<div class="card">
<form method="post" action="/admin/services">
  <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
  <label for="name">서비스명</label>
  <input id="name" name="name" required>
  <label for="manager_email">담당자 이메일</label>
  <input id="manager_email" name="manager_email" type="email" required>
  <label for="allowed_ips">허용 IP (쉼표 구분)</label>
  <input id="allowed_ips" name="allowed_ips" placeholder="10.0.0.1, 10.0.0.2" required>
  <div class="actions"><button class="btn btn-primary" type="submit">등록</button></div>
</form>
</div>
{% endblock %}
```

`app/admin/templates/services/keys.html`

```html
{% extends "base.html" %}
{% block title %}발급 키{% endblock %}
{% block content %}
<h1>{{ service.name }} — 발급된 키</h1>
<div class="notice">아래 키는 <strong>지금 한 번만</strong> 표시됩니다. 안전한 곳에 보관 후 서비스 담당자에게 전달하세요.</div>
<div class="card">
  <h2>서비스 API 키</h2>
  <div class="key-box" data-key="{{ api_key }}">{{ api_key }}</div>
  <h2>HMAC Secret</h2>
  <div class="key-box" data-secret="{{ hmac_secret }}">{{ hmac_secret }}</div>
  <div class="actions"><a class="btn btn-primary" href="/admin/services/{{ service.id }}">서비스 상세로</a></div>
</div>
{% endblock %}
```

`app/admin/templates/services/detail.html`

```html
{% extends "base.html" %}
{% block title %}{{ service.name }}{% endblock %}
{% block content %}
<h1>{{ service.name }}</h1>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<div class="card">
  <p>담당자: {{ service.manager_email }} · 상태:
     <span class="badge badge-{{ service.status }}">{{ service.status }}</span></p>
  <p>요금제 {{ plan_count }}개 · 구독 {{ sub_count }}건</p>
</div>
<div class="card">
  <h2>허용 IP</h2>
  <form method="post" action="/admin/services/{{ service.id }}/ips">
    <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
    <input name="allowed_ips" value="{{ service.allowed_ips | join(', ') }}">
    <div class="actions"><button class="btn btn-primary" type="submit">IP 갱신</button></div>
  </form>
</div>
<div class="card">
  <h2>키 관리</h2>
  <form method="post" action="/admin/services/{{ service.id }}/rotate-keys"
        onsubmit="return confirm('기존 키는 즉시 무효화됩니다. 재발급할까요?')">
    <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
    <button class="btn btn-danger" type="submit">키 재발급</button>
  </form>
</div>
<div class="card">
  <h2>상태/삭제</h2>
  <div class="actions">
    <form method="post" action="/admin/services/{{ service.id }}/status">
      <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
      <input type="hidden" name="status"
             value="{{ 'INACTIVE' if service.status == 'ACTIVE' else 'ACTIVE' }}">
      <button class="btn btn-primary" type="submit">
        {{ '비활성화' if service.status == 'ACTIVE' else '활성화' }}</button>
    </form>
    <form method="post" action="/admin/services/{{ service.id }}/delete"
          onsubmit="return confirm('정말 삭제할까요?')">
      <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
      <button class="btn btn-danger" type="submit">삭제</button>
    </form>
  </div>
</div>
{% endblock %}
```

`app/admin/templates/plans/list.html`

```html
{% extends "base.html" %}
{% block title %}요금제{% endblock %}
{% block content %}
<h1>요금제</h1>
{% if ctx.user.role == 'SERVICE_MANAGER' %}
<p style="margin-bottom:16px"><a class="btn btn-primary" href="/admin/plans/new">요금제 생성</a></p>
{% endif %}
<div class="card">
<table>
  <thead><tr><th>서비스</th><th>이름</th><th>가격</th><th>주기</th><th>첫구독 혜택</th><th>상태</th><th></th></tr></thead>
  <tbody>
  {% for plan, svc in rows %}
    <tr>
      <td>{{ svc.name }}</td>
      <td>{{ plan.name }}</td>
      <td>{{ "{:,}".format(plan.price) }}원</td>
      <td>{{ plan.billing_cycle }}{% if plan.cycle_days %}({{ plan.cycle_days }}일){% endif %}</td>
      <td>{{ plan.first_payment_type }}{% if plan.first_payment_value is not none %} {{ plan.first_payment_value }}{% endif %}</td>
      <td><span class="badge badge-{{ plan.status }}">{{ plan.status }}</span></td>
      <td>
        {% if ctx.user.role == 'SERVICE_MANAGER' %}
        <a href="/admin/plans/{{ plan.id }}/edit">수정</a>
        <form method="post" action="/admin/plans/{{ plan.id }}/archive" style="display:inline">
          <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
          <button class="btn btn-ghost" type="submit">보관</button>
        </form>
        <form method="post" action="/admin/plans/{{ plan.id }}/delete" style="display:inline"
              onsubmit="return confirm('삭제할까요? 구독이 있으면 불가합니다.')">
          <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
          <button class="btn btn-ghost" type="submit">삭제</button>
        </form>
        {% endif %}
      </td>
    </tr>
  {% else %}
    <tr><td colspan="7">요금제가 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`app/admin/templates/plans/form.html`

```html
{% extends "base.html" %}
{% block title %}요금제 {{ '수정' if plan else '생성' }}{% endblock %}
{% block content %}
<h1>요금제 {{ '수정' if plan else '생성' }}</h1>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<div class="card">
<form method="post" action="{{ '/admin/plans/' ~ plan.id if plan else '/admin/plans' }}">
  <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
  <label for="name">이름</label>
  <input id="name" name="name" value="{{ plan.name if plan else '' }}" required>
  <label for="price">가격 (원)</label>
  <input id="price" name="price" type="number" min="1"
         value="{{ plan.price if plan else '' }}" required>
  {% if not plan %}
  <label for="billing_cycle">결제 주기</label>
  <select id="billing_cycle" name="billing_cycle">
    <option value="MONTH">월</option><option value="YEAR">년</option>
    <option value="WEEK">주</option><option value="DAY">일(일수 지정)</option>
  </select>
  <label for="cycle_days">일수 (DAY 주기일 때만)</label>
  <input id="cycle_days" name="cycle_days" type="number" min="1">
  {% endif %}
  <label for="first_payment_type">첫구독 혜택</label>
  <select id="first_payment_type" name="first_payment_type">
    {% for t in ['NONE', 'FREE', 'DISCOUNT_AMOUNT', 'DISCOUNT_PERCENT'] %}
    <option value="{{ t }}" {{ 'selected' if plan and plan.first_payment_type == t }}>{{ t }}</option>
    {% endfor %}
  </select>
  <label for="first_payment_value">혜택 값 (할인 금액/율)</label>
  <input id="first_payment_value" name="first_payment_value" type="number" min="0"
         value="{{ plan.first_payment_value if plan and plan.first_payment_value is not none else '' }}">
  <div class="actions"><button class="btn btn-primary" type="submit">저장</button></div>
</form>
</div>
{% endblock %}
```

참고: 결제 주기는 생성 후 변경 불가(기존 구독의 기간 계산이 달라지므로) — 수정 폼에서 주기 필드를 제외했다.

- [ ] **Step 5: 통과 확인**

```bash
uv run pytest tests/e2e -v
```
Expected: 20 passed

- [ ] **Step 6: Commit**

```bash
git add app/admin tests/e2e/test_admin_services_plans.py
git commit -m "feat: admin 서비스 등록/키 1회 표시/IP 관리 + 요금제 CRUD 화면"
```

---

### Task 21: Admin — 구독/결제 조회, 강제취소, 계정/감사 로그

**Files:**
- Create: `app/admin/routes/subscriptions.py`, `app/admin/routes/users.py`, `app/admin/routes/audit.py`
- Create: `app/admin/templates/subscriptions/list.html`, `subscriptions/detail.html`, `payments/list.html`, `users/list.html`, `audit/list.html`
- Modify: `app/admin/__init__.py`, `app/services/subscriptions.py` (force_cancel), `app/services/auth.py` (reset 토큰)
- Test: `tests/e2e/test_admin_operations.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_admin_operations.py`

```python
from sqlalchemy import select

from app.models import AuditLog, PasswordSetupToken
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login, get_csrf


async def test_manager_sees_only_own_subscriptions(client, db, redis_client, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="sub-svc-a")
    svc_b, _, _ = await create_service(db, cipher, name="sub-svc-b")
    plan_a = await create_plan(db, svc_a)
    plan_b = await create_plan(db, svc_b)
    await create_subscription(db, cipher, svc_a, plan_a, external_user_id="user-of-a")
    await create_subscription(db, cipher, svc_b, plan_b, external_user_id="user-of-b")
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc_a.id)
    await admin_login(client, user.email, pw)

    resp = await client.get("/admin/subscriptions")
    assert "user-of-a" in resp.text
    assert "user-of-b" not in resp.text


async def test_admin_sees_all_subscriptions(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="user-all")
    user, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, user.email, pw)
    resp = await client.get("/admin/subscriptions")
    assert "user-all" in resp.text


async def test_manager_cannot_open_other_service_subscription_detail(
        client, db, redis_client, cipher):
    svc_a, _, _ = await create_service(db, cipher, name="det-svc-a")
    svc_b, _, _ = await create_service(db, cipher, name="det-svc-b")
    plan_b = await create_plan(db, svc_b)
    sub_b = await create_subscription(db, cipher, svc_b, plan_b)
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc_a.id)
    await admin_login(client, user.email, pw)
    resp = await client.get(f"/admin/subscriptions/{sub_b.id}")
    assert resp.status_code == 404


async def test_force_cancel_subscription(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-force")
    user, pw = await create_user(db, role="SYSTEM_ADMIN")
    session_id = await admin_login(client, user.email, pw)
    csrf = await get_csrf(redis_client, session_id)

    resp = await client.post(f"/admin/subscriptions/{sub.id}/force-cancel",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    await db.refresh(sub)
    assert sub.status == "CANCELED"
    log = await db.scalar(select(AuditLog).where(
        AuditLog.action == "subscription.force_cancel"))
    assert log is not None
    assert log.actor_user_id == user.id


async def test_payments_page_scoped(client, db, redis_client, cipher, fake_toss):
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    sub = await create_subscription(db, cipher, svc, plan, external_user_id="u-paylist")
    from app.models import Payment
    from app.core.clock import utcnow
    db.add(Payment(subscription_id=sub.id, order_id="adm-pay-1", amount=9900,
                   payment_type="RENEWAL", status="DONE", idempotency_key="ik",
                   requested_at=utcnow()))
    await db.commit()
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, user.email, pw)
    resp = await client.get("/admin/payments")
    assert resp.status_code == 200
    assert "adm-pay-1" in resp.text


async def test_users_page_admin_only_and_reset_password(client, db, redis_client,
                                                        cipher, email_sender):
    svc, _, _ = await create_service(db, cipher)
    manager, _ = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    session_id = await admin_login(client, admin.email, pw)
    csrf = await get_csrf(redis_client, session_id)

    page = await client.get("/admin/users")
    assert page.status_code == 200
    assert manager.email in page.text

    resp = await client.post(f"/admin/users/{manager.id}/reset-password",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    token = await db.scalar(select(PasswordSetupToken).where(
        PasswordSetupToken.user_id == manager.id))
    assert token is not None
    assert any("비밀번호" in m["subject"] for m in email_sender.sent)


async def test_audit_page_lists_actions(client, db, redis_client, cipher):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)  # auth.login 감사 로그 생성됨
    resp = await client.get("/admin/audit")
    assert resp.status_code == 200
    assert "auth.login" in resp.text


async def test_audit_page_forbidden_for_manager(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, user.email, pw)
    resp = await client.get("/admin/audit")
    assert resp.status_code == 403
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/e2e/test_admin_operations.py -v
```
Expected: FAIL

- [ ] **Step 3: 서비스 함수 추가** — `app/services/subscriptions.py` 끝에 추가

```python
async def force_cancel_subscription(db: AsyncSession, *, subscription_id: uuid.UUID,
                                    service_scope: uuid.UUID | None,
                                    actor_user_id: uuid.UUID) -> Subscription:
    """admin 화면에서 강제취소. service_scope가 있으면 해당 서비스 소속만 허용."""
    sub = await db.get(Subscription, subscription_id)
    if sub is None or (service_scope is not None and sub.service_id != service_scope):
        raise NotFoundError("구독을 찾을 수 없습니다")
    if sub.status not in (SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE):
        raise ConflictError("취소할 수 없는 상태입니다")
    sub.status = SubscriptionStatus.CANCELED
    sub.next_billing_at = None
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="subscription.force_cancel", target_type="subscription",
                       target_id=str(sub.id))
    await db.commit()
    return sub
```

`app/services/auth.py` 끝에 추가 (import에 `PasswordSetupToken`은 이미 있음,
`timedelta`/`generate_setup_token`/`sha256_hex`도 기존 import에 포함되어 있는지 확인):

```python
RESET_TOKEN_TTL = timedelta(hours=48)


async def issue_password_reset(db: AsyncSession, email_sender, *, user_id,
                               base_url: str, actor_user_id) -> None:
    """관리자가 담당자 비밀번호 재설정 토큰 발급 + 메일 발송."""
    from app.core.security import generate_setup_token, sha256_hex

    user = await db.get(User, user_id)
    if user is None:
        from app.core.errors import NotFoundError
        raise NotFoundError("사용자를 찾을 수 없습니다")
    token = generate_setup_token()
    db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(token),
                              expires_at=utcnow() + RESET_TOKEN_TTL))
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action="user.password_reset_issued", target_type="user",
                       target_id=str(user.id))
    await db.commit()
    await email_sender.send(
        user.email, "[결제시스템] 비밀번호 재설정 안내",
        f"아래 링크에서 비밀번호를 다시 설정해주세요 (48시간 유효):\n"
        f"{base_url}/admin/setup-password?token={token}")
```

- [ ] **Step 4: 라우트 구현** — `app/admin/routes/subscriptions.py`

```python
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_any, validate_csrf
from app.api.deps import get_db
from app.core.errors import NotFoundError
from app.models import Payment, Plan, Service, Subscription, UserRole
from app.services.subscriptions import force_cancel_subscription

router = APIRouter()


def _scope(ctx: AdminContext) -> uuid.UUID | None:
    return None if ctx.user.role == UserRole.SYSTEM_ADMIN else ctx.user.service_id


@router.get("/subscriptions")
async def subscriptions_list(request: Request,
                             ctx: AdminContext = Depends(require_any),
                             db: AsyncSession = Depends(get_db)):
    q = (select(Subscription, Plan, Service)
         .join(Plan, Subscription.plan_id == Plan.id)
         .join(Service, Subscription.service_id == Service.id)
         .order_by(Subscription.created_at.desc()).limit(100))
    scope = _scope(ctx)
    if scope is not None:
        q = q.where(Subscription.service_id == scope)
    status = request.query_params.get("status")
    if status:
        q = q.where(Subscription.status == status)
    rows = (await db.execute(q)).all()
    return render(request, "subscriptions/list.html", ctx=ctx, rows=rows,
                  status_filter=status or "")


@router.get("/subscriptions/{sub_id}")
async def subscription_detail(sub_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_any),
                              db: AsyncSession = Depends(get_db)):
    sub = await db.get(Subscription, sub_id)
    scope = _scope(ctx)
    if sub is None or (scope is not None and sub.service_id != scope):
        raise NotFoundError("구독을 찾을 수 없습니다")
    plan = await db.get(Plan, sub.plan_id)
    service = await db.get(Service, sub.service_id)
    payments = (await db.scalars(
        select(Payment).where(Payment.subscription_id == sub.id)
        .order_by(Payment.requested_at.desc()))).all()
    return render(request, "subscriptions/detail.html", ctx=ctx, sub=sub,
                  plan=plan, service=service, payments=payments,
                  error=request.query_params.get("error"))


@router.post("/subscriptions/{sub_id}/force-cancel")
async def subscription_force_cancel(sub_id: uuid.UUID, request: Request,
                                    ctx: AdminContext = Depends(require_any),
                                    db: AsyncSession = Depends(get_db)):
    await validate_csrf(request, ctx)
    await force_cancel_subscription(db, subscription_id=sub_id,
                                    service_scope=_scope(ctx),
                                    actor_user_id=ctx.user.id)
    return RedirectResponse(f"/admin/subscriptions/{sub_id}", status_code=303)


@router.get("/payments")
async def payments_list(request: Request,
                        ctx: AdminContext = Depends(require_any),
                        db: AsyncSession = Depends(get_db)):
    q = (select(Payment, Subscription)
         .join(Subscription, Payment.subscription_id == Subscription.id)
         .order_by(Payment.requested_at.desc()).limit(100))
    scope = _scope(ctx)
    if scope is not None:
        q = q.where(Subscription.service_id == scope)
    status = request.query_params.get("status")
    if status:
        q = q.where(Payment.status == status)
    rows = (await db.execute(q)).all()
    return render(request, "payments/list.html", ctx=ctx, rows=rows,
                  status_filter=status or "")
```

`app/admin/routes/users.py`

```python
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_admin, validate_csrf
from app.api.deps import get_db, get_email_sender, get_settings
from app.models import Service, User
from app.services.auth import issue_password_reset

router = APIRouter()


@router.get("/users")
async def users_list(request: Request, ctx: AdminContext = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(User, Service).outerjoin(Service, User.service_id == Service.id)
        .order_by(User.created_at))).all()
    return render(request, "users/list.html", ctx=ctx, rows=rows)


@router.post("/users/{user_id}/reset-password")
async def users_reset_password(user_id: uuid.UUID, request: Request,
                               ctx: AdminContext = Depends(require_admin),
                               db: AsyncSession = Depends(get_db),
                               email_sender=Depends(get_email_sender),
                               settings=Depends(get_settings)):
    await validate_csrf(request, ctx)
    await issue_password_reset(db, email_sender, user_id=user_id,
                               base_url=settings.base_url, actor_user_id=ctx.user.id)
    return RedirectResponse("/admin/users", status_code=303)
```

`app/admin/routes/audit.py`

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import AdminContext, require_admin
from app.api.deps import get_db
from app.models import AuditLog

router = APIRouter()


@router.get("/audit")
async def audit_list(request: Request, ctx: AdminContext = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    logs = (await db.scalars(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200))).all()
    return render(request, "audit/list.html", ctx=ctx, logs=logs)
```

`app/admin/__init__.py`의 라우터 부분 교체:

```python
from app.admin.routes import (  # noqa: E402
    audit,
    auth,
    dashboard,
    plans,
    services,
    subscriptions,
    users,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(dashboard.router)
router.include_router(services.router)
router.include_router(plans.router)
router.include_router(subscriptions.router)
router.include_router(users.router)
router.include_router(audit.router)
```

- [ ] **Step 5: 템플릿 작성** — `app/admin/templates/subscriptions/list.html`

```html
{% extends "base.html" %}
{% block title %}구독{% endblock %}
{% block content %}
<h1>구독</h1>
<form method="get" action="/admin/subscriptions" class="actions" style="margin-bottom:16px">
  <select name="status" onchange="this.form.submit()">
    <option value="">전체 상태</option>
    {% for s in ['ACTIVE', 'PAST_DUE', 'CANCELED', 'EXPIRED'] %}
    <option value="{{ s }}" {{ 'selected' if status_filter == s }}>{{ s }}</option>
    {% endfor %}
  </select>
</form>
<div class="card">
<table>
  <thead><tr><th>서비스</th><th>사용자</th><th>요금제</th><th>상태</th><th>기간 종료</th><th>다음 결제</th><th></th></tr></thead>
  <tbody>
  {% for sub, plan, svc in rows %}
    <tr>
      <td>{{ svc.name }}</td>
      <td>{{ sub.external_user_id }}</td>
      <td>{{ plan.name }}</td>
      <td><span class="badge badge-{{ sub.status }}">{{ sub.status }}</span></td>
      <td>{{ sub.current_period_end.strftime("%Y-%m-%d") }}</td>
      <td>{{ sub.next_billing_at.strftime("%Y-%m-%d %H:%M") if sub.next_billing_at else '-' }}</td>
      <td><a href="/admin/subscriptions/{{ sub.id }}">상세</a></td>
    </tr>
  {% else %}
    <tr><td colspan="7">구독이 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`app/admin/templates/subscriptions/detail.html`

```html
{% extends "base.html" %}
{% block title %}구독 상세{% endblock %}
{% block content %}
<h1>구독 상세 — {{ sub.external_user_id }}</h1>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<div class="card">
  <p>서비스: {{ service.name }} · 요금제: {{ plan.name }}
     ({{ "{:,}".format(plan.price) }}원/{{ plan.billing_cycle }})</p>
  <p>상태: <span class="badge badge-{{ sub.status }}">{{ sub.status }}</span>
     · 재시도: {{ sub.retry_count }}회</p>
  <p>기간: {{ sub.current_period_start.strftime("%Y-%m-%d") }} ~
     {{ sub.current_period_end.strftime("%Y-%m-%d") }}</p>
  <p>카드: {{ sub.card_info.number if sub.card_info else '-' }}</p>
  {% if sub.status in ('ACTIVE', 'PAST_DUE') %}
  <form method="post" action="/admin/subscriptions/{{ sub.id }}/force-cancel"
        onsubmit="return confirm('이 구독을 강제 취소할까요?')">
    <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
    <button class="btn btn-danger" type="submit">강제 취소</button>
  </form>
  {% endif %}
</div>
<h2>결제 이력</h2>
<div class="card">
<table>
  <thead><tr><th>주문번호</th><th>유형</th><th>금액</th><th>상태</th><th>실패 사유</th><th>요청 시각</th></tr></thead>
  <tbody>
  {% for p in payments %}
    <tr>
      <td>{{ p.order_id[:24] }}…</td>
      <td>{{ p.payment_type }}</td>
      <td>{{ "{:,}".format(p.amount) }}원</td>
      <td><span class="badge badge-{{ p.status }}">{{ p.status }}</span></td>
      <td>{{ p.failure_code or '-' }}</td>
      <td>{{ p.requested_at.strftime("%Y-%m-%d %H:%M") }}</td>
    </tr>
  {% else %}
    <tr><td colspan="6">결제 이력이 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`app/admin/templates/payments/list.html`

```html
{% extends "base.html" %}
{% block title %}결제{% endblock %}
{% block content %}
<h1>결제 이력</h1>
<form method="get" action="/admin/payments" class="actions" style="margin-bottom:16px">
  <select name="status" onchange="this.form.submit()">
    <option value="">전체 상태</option>
    {% for s in ['DONE', 'FAILED', 'PENDING', 'CANCELED'] %}
    <option value="{{ s }}" {{ 'selected' if status_filter == s }}>{{ s }}</option>
    {% endfor %}
  </select>
</form>
<div class="card">
<table>
  <thead><tr><th>주문번호</th><th>사용자</th><th>유형</th><th>금액</th><th>상태</th><th>실패 코드</th><th>요청 시각</th></tr></thead>
  <tbody>
  {% for p, sub in rows %}
    <tr>
      <td>{{ p.order_id }}</td>
      <td>{{ sub.external_user_id }}</td>
      <td>{{ p.payment_type }}</td>
      <td>{{ "{:,}".format(p.amount) }}원</td>
      <td><span class="badge badge-{{ p.status }}">{{ p.status }}</span></td>
      <td>{{ p.failure_code or '-' }}</td>
      <td>{{ p.requested_at.strftime("%Y-%m-%d %H:%M") }}</td>
    </tr>
  {% else %}
    <tr><td colspan="7">결제 이력이 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`app/admin/templates/users/list.html`

```html
{% extends "base.html" %}
{% block title %}계정{% endblock %}
{% block content %}
<h1>계정</h1>
<div class="card">
<table>
  <thead><tr><th>이메일</th><th>역할</th><th>서비스</th><th>상태</th><th></th></tr></thead>
  <tbody>
  {% for user, svc in rows %}
    <tr>
      <td>{{ user.email }}</td>
      <td>{{ user.role }}</td>
      <td>{{ svc.name if svc else '-' }}</td>
      <td><span class="badge badge-{{ 'ACTIVE' if user.status == 'ACTIVE' else 'PENDING' }}">{{ user.status }}</span></td>
      <td>
        <form method="post" action="/admin/users/{{ user.id }}/reset-password" style="display:inline">
          <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
          <button class="btn btn-ghost" type="submit">비밀번호 재설정 메일</button>
        </form>
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`app/admin/templates/audit/list.html`

```html
{% extends "base.html" %}
{% block title %}감사 로그{% endblock %}
{% block content %}
<h1>감사 로그</h1>
<div class="card">
<table>
  <thead><tr><th>시각</th><th>행위자</th><th>액션</th><th>대상</th><th>IP</th></tr></thead>
  <tbody>
  {% for log in logs %}
    <tr>
      <td>{{ log.created_at.strftime("%Y-%m-%d %H:%M:%S") }}</td>
      <td>{{ log.actor_type }}{{ ' ' ~ log.actor_user_id if log.actor_user_id else '' }}</td>
      <td>{{ log.action }}</td>
      <td>{{ log.target_type or '-' }} {{ log.target_id or '' }}</td>
      <td>{{ log.ip_address or '-' }}</td>
    </tr>
  {% else %}
    <tr><td colspan="5">로그가 없습니다</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

- [ ] **Step 6: 통과 확인**

```bash
uv run pytest tests/e2e -v
```
Expected: 28 passed

- [ ] **Step 7: Commit**

```bash
git add app/admin app/services/subscriptions.py app/services/auth.py tests/e2e/test_admin_operations.py
git commit -m "feat: admin 구독/결제 조회, 강제취소, 계정 관리, 감사 로그"
```

---

### Task 22: 보안 전용 테스트 스위트

**Files:**
- Create: `tests/security/__init__.py`, `tests/security/conftest.py`, `tests/security/test_hmac_auth.py`, `tests/security/test_admin_security.py`

**의도:** 공격 시나리오를 명시적으로 검증한다. 구현 수정 없이 전부 통과해야 정상
(통과하지 않으면 해당 구현 태스크로 돌아가 수정).

- [ ] **Step 1: 디렉토리/클린업** — `tests/security/conftest.py` (+ 빈 `__init__.py`)

```python
import pytest


@pytest.fixture(autouse=True)
def _auto_clean(clean_db, clean_redis):
    """보안 테스트 후 DB/Redis 초기화."""
```

- [ ] **Step 2: 외부 API 공격 시나리오** — `tests/security/test_hmac_auth.py`

```python
import json
import time
import uuid as uuid_mod

from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.services.registry import rotate_keys
from tests.factories import create_plan, create_service
from tests.helpers import api_request, signed_headers


async def test_body_tampering_rejected(client, db, cipher):
    """서명한 본문과 다른 본문을 보내면 401 (본문 무결성)."""
    svc, api_key, secret = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    base = {"external_user_id": "u-1", "plan_id": str(plan.id),
            "auth_key": "a", "customer_key": "ck-1"}
    good_body = json.dumps(base).encode()
    evil_body = json.dumps({**base, "customer_key": "ck-EVIL"}).encode()
    headers = signed_headers(api_key, secret, "POST", "/api/v1/subscriptions", good_body)
    resp = await client.post("/api/v1/subscriptions", content=evil_body, headers=headers)
    assert resp.status_code == 401


async def test_signature_for_other_path_rejected(client, db, cipher):
    """다른 경로용 서명 재사용 → 401."""
    svc, api_key, secret = await create_service(db, cipher)
    headers = signed_headers(api_key, secret, "GET", "/api/v1/plans")
    resp = await client.get("/api/v1/payments/u-1", headers=headers)
    assert resp.status_code == 401


async def test_future_timestamp_rejected(client, db, cipher):
    svc, api_key, secret = await create_service(db, cipher)
    future = str(int(time.time()) + 3600)
    resp = await api_request(client, "GET", "/api/v1/plans", api_key, secret,
                             timestamp=future)
    assert resp.status_code == 401


async def test_nonce_scope_is_per_service(client, db, cipher):
    """nonce는 서비스별 스코프 — 다른 서비스의 정상 요청을 막지 않는다."""
    svc_a, key_a, sec_a = await create_service(db, cipher, name="nonce-a")
    svc_b, key_b, sec_b = await create_service(db, cipher, name="nonce-b")
    shared_nonce = str(uuid_mod.uuid4())
    r1 = await api_request(client, "GET", "/api/v1/plans", key_a, sec_a,
                           nonce=shared_nonce)
    r2 = await api_request(client, "GET", "/api/v1/plans", key_b, sec_b,
                           nonce=shared_nonce)
    assert r1.status_code == 200 and r2.status_code == 200


async def test_rate_limit_returns_429(settings, engine, fake_toss, email_sender,
                                      db, cipher):
    limited = settings.model_copy(update={"rate_limit_per_minute": 3})
    svc, api_key, secret = await create_service(db, cipher)
    application = create_app(limited, toss_client=fake_toss,
                             email_sender=email_sender, engine=engine)
    statuses = []
    async with LifespanManager(application):
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as c:
            for _ in range(4):
                resp = await api_request(c, "GET", "/api/v1/plans", api_key, secret)
                statuses.append(resp.status_code)
    assert statuses == [200, 200, 200, 429]


async def test_payment_rate_limit_stricter(settings, engine, fake_toss, email_sender,
                                           db, cipher):
    limited = settings.model_copy(update={"rate_limit_payment_per_minute": 1})
    svc, api_key, secret = await create_service(db, cipher)
    plan = await create_plan(db, svc)
    application = create_app(limited, toss_client=fake_toss,
                             email_sender=email_sender, engine=engine)
    async with LifespanManager(application):
        async with AsyncClient(transport=ASGITransport(app=application),
                               base_url="http://test") as c:
            first = await api_request(
                c, "POST", "/api/v1/subscriptions", api_key, secret,
                json_body={"external_user_id": "u-rl1", "plan_id": str(plan.id),
                           "auth_key": "a", "customer_key": "ck-rl1"})
            second = await api_request(
                c, "POST", "/api/v1/subscriptions", api_key, secret,
                json_body={"external_user_id": "u-rl2", "plan_id": str(plan.id),
                           "auth_key": "a", "customer_key": "ck-rl2"})
    assert first.status_code == 201
    assert second.status_code == 429


async def test_rotated_key_invalidates_old(client, db, cipher):
    svc, old_key, old_secret = await create_service(db, cipher)
    new_key, new_secret = await rotate_keys(db, cipher, svc.id)
    old_resp = await api_request(client, "GET", "/api/v1/plans", old_key, old_secret)
    assert old_resp.status_code == 401
    new_resp = await api_request(client, "GET", "/api/v1/plans", new_key, new_secret)
    assert new_resp.status_code == 200


async def test_error_responses_do_not_leak_internals(client):
    resp = await client.get("/api/v1/plans")  # 인증 없음
    body = resp.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) == {"code", "message"}
```

- [ ] **Step 3: Admin 공격 시나리오** — `tests/security/test_admin_security.py`

```python
from sqlalchemy import select

from app.models import Plan
from tests.factories import create_plan, create_service, create_user
from tests.helpers import admin_login, get_csrf


async def test_bogus_session_cookie_redirects(client):
    client.cookies.set("admin_session", "forged-session-id")
    resp = await client.get("/admin")
    assert resp.status_code == 303


async def test_old_session_invalid_after_logout(client, db, redis_client):
    user, pw = await create_user(db)
    session_id = await admin_login(client, user.email, pw)
    csrf = await get_csrf(redis_client, session_id)
    await client.post("/admin/logout", data={"csrf_token": csrf})
    client.cookies.set("admin_session", session_id)  # 옛 세션 재사용 시도
    resp = await client.get("/admin")
    assert resp.status_code == 303


async def test_csrf_wrong_token_blocks_state_change(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher)
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, user.email, pw)
    resp = await client.post("/admin/plans", data={
        "csrf_token": "wrong-token", "name": "공격요금제", "price": "1000",
        "billing_cycle": "MONTH", "cycle_days": "",
        "first_payment_type": "NONE", "first_payment_value": ""})
    assert resp.status_code == 403
    assert await db.scalar(select(Plan).where(Plan.name == "공격요금제")) is None


async def test_manager_cannot_rotate_service_keys(client, db, redis_client, cipher):
    """권한 상승 시도 — SERVICE_MANAGER가 SYSTEM_ADMIN 기능 호출."""
    svc, _, _ = await create_service(db, cipher)
    user, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    session_id = await admin_login(client, user.email, pw)
    csrf = await get_csrf(redis_client, session_id)
    resp = await client.post(f"/admin/services/{svc.id}/rotate-keys",
                             data={"csrf_token": csrf})
    assert resp.status_code == 403


async def test_lockout_via_http(client, db):
    user, pw = await create_user(db)
    for _ in range(5):
        await client.post("/admin/login", data={"email": user.email, "password": "wrong"})
    resp = await client.post("/admin/login", data={"email": user.email, "password": pw})
    assert resp.status_code == 200
    assert "잠겼습니다" in resp.text


async def test_pending_user_cannot_login_http(client, db):
    user, pw = await create_user(db, status="PENDING")
    resp = await client.post("/admin/login", data={"email": user.email, "password": pw})
    assert "비밀번호 설정이 필요합니다" in resp.text


async def test_login_errors_do_not_reveal_account_existence(client, db):
    user, _ = await create_user(db)
    r1 = await client.post("/admin/login",
                           data={"email": user.email, "password": "wrong"})
    r2 = await client.post("/admin/login",
                           data={"email": "ghost@nowhere.com", "password": "wrong"})
    assert "이메일 또는 비밀번호가 올바르지 않습니다" in r1.text
    assert "이메일 또는 비밀번호가 올바르지 않습니다" in r2.text
```

- [ ] **Step 4: 실행 — 전부 통과해야 함 (실패 시 해당 구현 태스크로 복귀해 수정)**

```bash
uv run pytest tests/security -v
```
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add tests/security
git commit -m "test: 보안 공격 시나리오 스위트(변조/재전송/권한상승/CSRF/레이트리밋)"
```

---

### Task 23: E2E 전체 흐름 + README + 최종 검증

**Files:**
- Create: `tests/e2e/test_full_flow.py`, `README.md` (전체 교체)
- Test: 전체 스위트 + 커버리지

- [ ] **Step 1: E2E 라이프사이클 테스트 작성** — `tests/e2e/test_full_flow.py`

```python
import re
from datetime import timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.clock import utcnow
from app.models import Payment, Plan, Subscription
from app.scheduler.runner import run_renewals
from tests.factories import create_user
from tests.helpers import admin_login, api_request, get_csrf


async def test_full_subscription_lifecycle(client, app, db, redis_client, cipher,
                                           fake_toss, email_sender):
    # 1) 시스템 관리자: 서비스 등록 → 키 1회 발급
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    session_id = await admin_login(client, admin.email, pw)
    csrf = await get_csrf(redis_client, session_id)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "e2e-service",
        "manager_email": "mgr-e2e@medisolveai.com", "allowed_ips": "127.0.0.1"})
    assert resp.status_code == 200
    api_key = re.search(r'data-key="(svc_[^"]+)"', resp.text).group(1)
    hmac_secret = re.search(r'data-secret="([^"]+)"', resp.text).group(1)

    # 2) 담당자: 메일의 토큰으로 비밀번호 설정 → 로그인 → 요금제 생성
    setup_mail = email_sender.sent[0]
    token = re.search(r"token=([A-Za-z0-9_\-]+)", setup_mail["body"]).group(1)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as mgr:
        await mgr.post("/admin/setup-password", data={
            "token": token, "password": "ManagerPass12",
            "password_confirm": "ManagerPass12"})
        mgr_session = await admin_login(mgr, "mgr-e2e@medisolveai.com", "ManagerPass12")
        mgr_csrf = await get_csrf(redis_client, mgr_session)
        create_resp = await mgr.post("/admin/plans", data={
            "csrf_token": mgr_csrf, "name": "E2E 요금제", "price": "15000",
            "billing_cycle": "MONTH", "cycle_days": "",
            "first_payment_type": "DISCOUNT_PERCENT", "first_payment_value": "50"})
        assert create_resp.status_code == 303
    plan = await db.scalar(select(Plan).where(Plan.name == "E2E 요금제"))

    # 3) 외부 서비스: HMAC 서명 API로 구독 생성 (첫구독 50% 할인 → 7,500원)
    resp = await api_request(client, "POST", "/api/v1/subscriptions",
                             api_key, hmac_secret,
                             json_body={"external_user_id": "e2e-user",
                                        "plan_id": str(plan.id),
                                        "auth_key": "auth-from-widget",
                                        "customer_key": "ck-e2e-user"})
    assert resp.status_code == 201
    assert resp.json()["status"] == "ACTIVE"
    assert fake_toss.charges[0]["amount"] == 7500

    # 4) 만료일 도래 → 스케줄러 배치 → 정가로 자동연장
    sub = await db.scalar(select(Subscription))
    past = utcnow() - timedelta(minutes=5)
    sub.current_period_start = past - timedelta(days=31)
    sub.current_period_end = past
    sub.next_billing_at = past
    await db.commit()
    stats = await run_renewals(app)
    assert stats["renewed"] == 1
    assert fake_toss.charges[1]["amount"] == 15000  # 갱신은 정가

    # 5) 취소 → 재개 → 다시 취소 → 만료 처리(빌링키 삭제)
    cancel = await api_request(client, "POST",
                               "/api/v1/subscriptions/e2e-user/cancel",
                               api_key, hmac_secret)
    assert cancel.json()["status"] == "CANCELED"
    resume = await api_request(client, "POST",
                               "/api/v1/subscriptions/e2e-user/resume",
                               api_key, hmac_secret)
    assert resume.json()["status"] == "ACTIVE"
    await api_request(client, "POST", "/api/v1/subscriptions/e2e-user/cancel",
                      api_key, hmac_secret)
    await db.refresh(sub)
    sub.current_period_end = utcnow() - timedelta(minutes=1)
    await db.commit()
    stats = await run_renewals(app)
    assert stats["expired"] == 1
    status_resp = await api_request(client, "GET",
                                    "/api/v1/subscriptions/e2e-user",
                                    api_key, hmac_secret)
    assert status_resp.json()["status"] == "EXPIRED"
    assert fake_toss.deleted  # 빌링키 정리됨

    # 6) 결제 이력 API + 관리자 대시보드 반영
    pays = await api_request(client, "GET", "/api/v1/payments/e2e-user",
                             api_key, hmac_secret)
    assert len(pays.json()["payments"]) == 2
    dash = await client.get("/admin")
    assert dash.status_code == 200
    payments = (await db.scalars(select(Payment))).all()
    assert len(payments) == 2
```

- [ ] **Step 2: 실행 확인**

```bash
uv run pytest tests/e2e/test_full_flow.py -v
```
Expected: 1 passed

- [ ] **Step 3: README.md 작성** (전체 교체)

````markdown
# 구독/결제 API 서버

사내 서비스 공용 구독/결제 서버. 토스페이먼츠 빌링키 기반 자동결제.

- 스펙: `docs/superpowers/specs/2026-06-05-subscription-payment-server-design.md`
- 스택: FastAPI · PostgreSQL(SQLAlchemy 2 async) · Redis · htmx admin · APScheduler

## 빠른 시작

```bash
docker compose up -d                  # PostgreSQL(5433), Redis(6380)
cp .env.example .env                  # ENCRYPTION_KEY/TOSS_SECRET_KEY 채우기
uv sync
uv run alembic upgrade head
uv run python -m app.cli create-admin --email admin@medisolveai.com --password '<10자 이상>'
uv run uvicorn app.main:app --reload
```

- Admin: http://localhost:8000/admin
- Health: http://localhost:8000/health

## 테스트

```bash
docker compose up -d
uv run pytest                          # 전체 (unit/integration/security/e2e)
uv run pytest --cov=app --cov-report=term-missing
```

## 외부 서비스 연동 가이드

### 1. 인증 헤더 (모든 요청)

| 헤더 | 값 |
|---|---|
| `X-Service-Key` | 발급받은 서비스 키 (`svc_...`) |
| `X-Timestamp` | Unix epoch 초 (서버와 ±5분 이내) |
| `X-Nonce` | 요청마다 새로운 UUID |
| `X-Signature` | 아래 서명 |

서명 생성 (HMAC-SHA256, hex):

```python
import hashlib, hmac, json, time, uuid

def sign(secret: str, method: str, path: str, body: bytes) -> dict:
    ts = str(int(time.time()))
    nonce = str(uuid.uuid4())
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, ts, nonce, body_hash])
    sig = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return {"X-Service-Key": SERVICE_KEY, "X-Timestamp": ts,
            "X-Nonce": nonce, "X-Signature": sig}
```

주의: 등록된 서버 IP에서만 호출 가능. 키 유출 시 admin에서 즉시 재발급.

### 2. 구독 생성 플로우

1. 프론트에서 토스 SDK `payment.requestBillingAuth()` 호출
   (`customerKey`는 UUID로 생성)
2. successUrl 리다이렉트로 받은 `authKey` + `customerKey`를 백엔드로 전달
3. 백엔드에서 `POST /api/v1/subscriptions` 호출:

```json
{"external_user_id": "<서비스측 사용자 ID>", "plan_id": "<요금제 UUID>",
 "auth_key": "<authKey>", "customer_key": "<customerKey>"}
```

결제 금액은 서버가 요금제에서 계산한다(요청 본문에 금액 없음).

### 3. 주요 엔드포인트

| 메서드/경로 | 설명 |
|---|---|
| `POST /api/v1/subscriptions` | 구독 생성(빌링키 발급+첫 결제) |
| `GET /api/v1/subscriptions/{external_user_id}` | 구독 상태 |
| `POST /api/v1/subscriptions/{external_user_id}/cancel` | 취소(만료일까지 유지) |
| `POST /api/v1/subscriptions/{external_user_id}/resume` | 취소 철회 |
| `POST /api/v1/subscriptions/{external_user_id}/change-card` | 카드 교체 |
| `GET /api/v1/plans` | 요금제 목록 |
| `GET /api/v1/payments/{external_user_id}` | 결제 이력 |

에러 응답: `{"error": {"code": "...", "message": "..."}}`
(401 인증실패 · 403 IP/권한 · 402 결제실패 · 409 중복구독 · 429 한도초과)

## 운영 메모

- 자동연장: 5분 주기 배치(APScheduler), 실패 시 1일 간격 3회 재시도 후 만료
- 토스 웹훅 URL: `POST /api/v1/webhooks/toss` (토스 인바운드 IP만 허용)
- 빌링키/HMAC secret은 AES-256-GCM 암호화 저장 — `ENCRYPTION_KEY` 분실 시 복호화 불가
- 이메일: 기본 콘솔 출력(`ConsoleEmailSender`). SMTP 연동 시
  `app/notifications/email.py`에 구현체 추가 후 `create_app` 주입 교체
````

- [ ] **Step 4: 최종 전체 검증**

```bash
docker compose up -d
uv run pytest -q
```
Expected: 전체 통과 (약 120+ tests)

```bash
uv run pytest --cov=app --cov-report=term-missing -q | tail -30
```
Expected: 커버리지 리포트 출력 (서비스 레이어 90%+ 목표, 미달 영역 확인만)

```bash
uv run uvicorn app.main:app --port 8001 &
sleep 3
curl -s http://localhost:8001/health
curl -s http://localhost:8001/admin/login | grep -o "로그인" | head -1
kill %1
```
Expected: `{"status":"ok"}` 와 `로그인`

- [ ] **Step 5: 최종 Commit**

```bash
git add tests/e2e/test_full_flow.py README.md
git commit -m "test: E2E 라이프사이클 + README 연동 가이드"
```

---

## 완료 기준 (Definition of Done)

1. `uv run pytest` 전체 통과 (unit / integration / security / e2e)
2. 보안 스위트의 모든 공격 시나리오가 거부됨
3. `uvicorn` 기동 후 `/health`, `/admin/login` 정상 응답
4. 스펙(`docs/superpowers/specs/...`)의 §4~§10 요구사항이 각 태스크에 매핑되어 구현됨
5. `.env`(시크릿)가 git에 포함되지 않음 (`git status`로 확인)
