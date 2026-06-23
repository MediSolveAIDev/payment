# 서비스별 toss_secret_key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 토스 시크릿 키를 서비스별로 암호화 저장하고, 서버의 모든 토스 호출을 해당 서비스 키로 수행하도록 전환한다(전역 키 제거).

**Architecture:** `services.toss_secret_key_encrypted`(AES) 컬럼을 추가하고, `TossClientProvider.for_service(service)`가 서비스 키를 복호화해 서비스별 `HttpTossClient`를 캐시·반환한다. 전역 `app.state.toss`를 provider로 대체하되, 테스트 호환을 위해 provider는 주입된 override 클라이언트(Fake)를 모든 서비스에 반환하는 모드를 지원한다. 콜사이트(API·어드민·스케줄러)를 점진 전환한 뒤 전역 키를 제거한다.

**Tech Stack:** FastAPI, SQLAlchemy(async), Alembic, httpx, AES-GCM(AesGcmCipher), pytest/pytest-asyncio, htmx(Jinja2).

## Global Constraints

- `toss_secret_key`는 **AES-GCM 암호화**해 `services.toss_secret_key_encrypted`에 저장. 평문은 DB·API응답·감사로그·로그 어디에도 남기지 않는다.
- 서비스에 키가 없으면 토스 호출이 `TossKeyNotConfiguredError`(코드 `TOSS_KEY_NOT_CONFIGURED`)로 실패한다. 전역 폴백 없음.
- 등록 시 키는 **선택 입력**(미설정 허용). 수정에서 설정/교체 가능.
- 키 설정/교체 액션은 감사로그에 기록하되 **시크릿 값은 절대 기록하지 않는다**.
- 전역 `TOSS_SECRET_KEY`(config·.env)는 전환 완료 후 제거. `toss_api_base_url`/타임아웃은 전역 유지.
- 테스트 호환: `create_app(toss_client=FakeTossClient(...))`로 주입하면 provider가 모든 서비스에 그 Fake를 반환(키 없이도 기존 테스트 통과).
- 모든 변경 코드에 한국어 주석. 기존 패턴(`hmac_secret_encrypted` 암호화·`record_audit`)을 따른다.
- 테스트 인프라: DB localhost:5432, Redis localhost:6380(테스트 시 가동 필요).

---

### Task 1: Service 모델 컬럼 + 마이그레이션

**Files:**
- Modify: `app/models/service.py:36` (컬럼 추가)
- Create: `alembic/versions/<rev>_service_toss_secret_key.py`
- Test: `tests/integration/test_registry.py` (모델 속성 확인은 기존 등록 테스트로 충분 — 신규 컬럼이 None 기본값)

**Interfaces:**
- Produces: `Service.toss_secret_key_encrypted: str | None`

- [ ] **Step 1: 모델에 컬럼 추가** — `app/models/service.py`의 `notification_url` 아래

```python
    notification_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 알림 수신 URL(없으면 미발송)
    # 서비스별 토스 시크릿 키(AES-GCM 암호화 보관). 미설정(NULL)이면 결제·승인·갱신이 TOSS_KEY_NOT_CONFIGURED로 거부된다.
    # 평문은 저장·응답·감사로그 어디에도 남기지 않는다(api_key/hmac과 동일 정책).
    toss_secret_key_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

- [ ] **Step 2: 마이그레이션 생성**

Run: `uv run alembic revision -m "service toss_secret_key"`

- [ ] **Step 3: 마이그레이션 본문 작성** — 생성된 파일 (down_revision은 `uv run alembic heads`로 확인한 현재 head로; 기존 버전 파일의 헤더 형식을 따른다)

```python
def upgrade() -> None:
    # 서비스별 토스 시크릿 보관 컬럼(AES 암호문, nullable). 기존 행은 NULL → 키 등록 전까지 결제 거부.
    op.add_column("services", sa.Column("toss_secret_key_encrypted", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("services", "toss_secret_key_encrypted")
```

- [ ] **Step 4: 마이그레이션 적용 확인**

Run: `uv run alembic upgrade head && uv run alembic current`
Expected: 오류 없이 적용, current가 새 head.

- [ ] **Step 5: 회귀 확인 + 커밋**

Run: `uv run pytest tests/integration/test_registry.py -q`
Expected: PASS (신규 nullable 컬럼이 기존 등록 흐름을 깨지 않음)

```bash
git add app/models/service.py alembic/versions/
git commit -m "feat: services.toss_secret_key_encrypted 컬럼 + 마이그레이션"
```

---

### Task 2: TossKeyNotConfiguredError + TossClientProvider

**Files:**
- Modify: `app/core/errors.py` (신규 예외)
- Create: `app/toss/provider.py`
- Test: `tests/unit/test_toss_provider.py`

**Interfaces:**
- Consumes: `Service.toss_secret_key_encrypted`(Task 1), `AesGcmCipher` (`app/core/crypto.py`), `HttpTossClient`/`TossClient` (`app/toss/client.py`).
- Produces:
  - `TossKeyNotConfiguredError(DomainError)` — `.code == "TOSS_KEY_NOT_CONFIGURED"`
  - `TossClientProvider(cipher, base_url, *, override_client=None, factory=HttpTossClient)`
    - `.for_service(service) -> TossClient`
    - `async .aclose() -> None`

- [ ] **Step 1: 실패 테스트 작성** — `tests/unit/test_toss_provider.py`

```python
import pytest
from app.core.crypto import AesGcmCipher
from app.core.errors import TossKeyNotConfiguredError
from app.toss.provider import TossClientProvider


class _Svc:
    def __init__(self, enc): self.toss_secret_key_encrypted = enc


def _cipher():
    # 32바이트 base64 키(테스트용)
    import base64, os
    return AesGcmCipher(base64.b64encode(b"0" * 32).decode())


def test_override_client_returned_for_any_service():
    sentinel = object()
    p = TossClientProvider(_cipher(), "https://api.tosspayments.com", override_client=sentinel)
    assert p.for_service(_Svc(None)) is sentinel          # 키 없어도 override 반환(테스트 모드)


def test_missing_key_raises():
    p = TossClientProvider(_cipher(), "https://api.tosspayments.com")
    with pytest.raises(TossKeyNotConfiguredError):
        p.for_service(_Svc(None))


def test_builds_and_caches_per_secret():
    cipher = _cipher()
    built = []
    def factory(secret, base_url):
        built.append(secret)
        return object()
    p = TossClientProvider(cipher, "https://api.tosspayments.com", factory=factory)
    svc = _Svc(cipher.encrypt("sk_test_abc"))
    c1 = p.for_service(svc)
    c2 = p.for_service(_Svc(cipher.encrypt("sk_test_abc")))   # 동일 시크릿 → 캐시 재사용
    assert c1 is c2
    assert built == ["sk_test_abc"]                            # 팩토리는 1회만 호출
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/unit/test_toss_provider.py -v`
Expected: FAIL (ImportError: provider/error 없음)

- [ ] **Step 3: 예외 추가** — `app/core/errors.py` (기존 DomainError 서브클래스들 옆)

```python
class TossKeyNotConfiguredError(DomainError):
    """서비스에 toss_secret_key가 설정되지 않아 토스 호출을 할 수 없음.

    서비스별 키 체계에서 키 미등록 서비스의 결제·승인·갱신을 명확히 거부한다.
    """
    code = "TOSS_KEY_NOT_CONFIGURED"

    def __init__(self, message: str = "서비스에 토스 시크릿 키가 설정되지 않았습니다") -> None:
        super().__init__(message)
```

> 참고: 기존 `DomainError`가 `code`/`message`를 어떻게 노출하는지 확인하고(다른 서브클래스 패턴) 동일하게 맞춘다. 다른 에러가 `code` 클래스 속성을 쓰지 않으면 그 파일의 실제 패턴(예: 생성자에서 code 전달)을 따른다.

- [ ] **Step 4: Provider 구현** — `app/toss/provider.py` (신규)

```python
"""서비스별 토스 클라이언트 해석기.

서비스의 암호화된 toss_secret_key를 복호화해 HttpTossClient를 생성·캐시한다.
캐시 키는 복호화된 시크릿 값 → 키 교체 시 새 엔트리가 생기고 옛 엔트리는 유휴화된다.
테스트는 override_client를 주입해 모든 서비스에 동일 Fake를 반환받는다(키 불필요).
"""
from app.core.crypto import AesGcmCipher
from app.core.errors import TossKeyNotConfiguredError
from app.toss.client import HttpTossClient, TossClient


class TossClientProvider:
    def __init__(self, cipher: AesGcmCipher, base_url: str, *,
                 override_client: TossClient | None = None,
                 factory=HttpTossClient) -> None:
        self._cipher = cipher
        self._base_url = base_url
        self._override = override_client          # 테스트 주입용(있으면 항상 이 클라이언트 반환)
        self._factory = factory                   # (secret, base_url) -> TossClient
        self._cache: dict[str, TossClient] = {}   # 시크릿별 클라이언트 캐시(연결 재사용)

    def for_service(self, service) -> TossClient:
        """서비스의 토스 클라이언트 반환. 키 미설정 시 TossKeyNotConfiguredError."""
        if self._override is not None:
            return self._override
        enc = getattr(service, "toss_secret_key_encrypted", None)
        if not enc:
            raise TossKeyNotConfiguredError()
        secret = self._cipher.decrypt(enc)
        client = self._cache.get(secret)
        if client is None:
            client = self._factory(secret, self._base_url)
            self._cache[secret] = client
        return client

    async def aclose(self) -> None:
        """캐시된 모든 HttpTossClient 정리(앱 종료 시). override는 소유자가 정리."""
        for client in self._cache.values():
            aclose = getattr(client, "aclose", None)
            if aclose is not None:
                await aclose()
        self._cache.clear()
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest tests/unit/test_toss_provider.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: 커밋**

```bash
git add app/core/errors.py app/toss/provider.py tests/unit/test_toss_provider.py
git commit -m "feat: TossClientProvider(서비스별 클라이언트 해석) + TossKeyNotConfiguredError"
```

---

### Task 3: registry — 등록 시 키 + set_toss_secret_key + 감사로그

**Files:**
- Modify: `app/services/registry.py` (register_service 파라미터/저장; 신규 set_toss_secret_key)
- Test: `tests/integration/test_registry.py`

**Interfaces:**
- Consumes: `cipher.encrypt`, `record_audit`, `Service.toss_secret_key_encrypted`.
- Produces:
  - `register_service(..., toss_secret_key: str | None = None)` (키워드)
  - `async set_toss_secret_key(db, cipher, *, service_id, toss_secret_key: str, actor_user_id=None) -> None`

- [ ] **Step 1: 실패 테스트 작성** — `tests/integration/test_registry.py` (기존 픽스처/스타일 사용; `audit` 조회 방식은 같은 파일의 기존 audit 검증 패턴을 따른다)

```python
@pytest.mark.asyncio
async def test_register_with_toss_key_encrypts_and_audits(db, cipher, ...):
    creds = await register_service(db, cipher, name="svc-toss", allowed_ips=[],
                                   manager_user_ids=[...], primary_user_id=...,
                                   toss_secret_key="sk_test_LIVE")
    svc = creds.service
    assert svc.toss_secret_key_encrypted                      # 저장됨
    assert cipher.decrypt(svc.toss_secret_key_encrypted) == "sk_test_LIVE"
    # 감사로그에 set 액션이 있고, 어디에도 평문 시크릿이 없어야 함
    rows = (await db.execute(select(AuditLog).where(AuditLog.target_id == str(svc.id)))).scalars().all()
    assert any(r.action == "service.toss_secret_key.set" for r in rows)
    assert all("sk_test_LIVE" not in (str(r.detail) or "") for r in rows)


@pytest.mark.asyncio
async def test_set_toss_secret_key_set_then_change(db, cipher, ...):
    creds = await register_service(db, cipher, name="svc2", allowed_ips=[],
                                   manager_user_ids=[...], primary_user_id=...)
    sid = creds.service.id
    await set_toss_secret_key(db, cipher, service_id=sid, toss_secret_key="sk_1")
    await set_toss_secret_key(db, cipher, service_id=sid, toss_secret_key="sk_2")
    svc = await db.get(Service, sid)
    assert cipher.decrypt(svc.toss_secret_key_encrypted) == "sk_2"
    rows = (await db.execute(select(AuditLog).where(AuditLog.target_id == str(sid)))).scalars().all()
    actions = [r.action for r in rows]
    assert "service.toss_secret_key.set" in actions           # 최초 설정
    assert "service.toss_secret_key.changed" in actions       # 교체
    assert all("sk_1" not in str(r.detail) and "sk_2" not in str(r.detail) for r in rows)


@pytest.mark.asyncio
async def test_set_toss_secret_key_rejects_empty(db, cipher, ...):
    creds = await register_service(db, cipher, name="svc3", allowed_ips=[],
                                   manager_user_ids=[...], primary_user_id=...)
    with pytest.raises(InputValidationError):
        await set_toss_secret_key(db, cipher, service_id=creds.service.id, toss_secret_key="  ")
```

> `...`(매니저 픽스처 등)는 `tests/integration/test_registry.py`의 기존 등록 테스트가 쓰는 값/헬퍼를 그대로 사용한다. `AuditLog` 모델·import는 같은 파일의 기존 audit 검증에서 확인.

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/integration/test_registry.py -k toss -v`
Expected: FAIL (param/함수 없음)

- [ ] **Step 3: register_service에 키 파라미터 + 저장 + audit** — `app/services/registry.py`

시그니처에 추가:
```python
                           cancellation_fee_percent: int = 0,
                           toss_secret_key: str | None = None,   # 서비스별 토스 시크릿(선택; AES 암호화 저장)
                           actor_user_id: uuid.UUID | None = None) -> IssuedCredentials:
```
`Service(...)` 생성에 추가:
```python
                      cancellation_fee_percent=cancellation_fee_percent,
                      toss_secret_key_encrypted=(cipher.encrypt(toss_secret_key.strip())
                                                 if toss_secret_key and toss_secret_key.strip() else None))
```
등록 audit(기존 `service.register`/유사 액션 기록 직후, 키가 설정된 경우에만 추가 기록 — 값 제외):
```python
    if toss_secret_key and toss_secret_key.strip():
        await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                           action="service.toss_secret_key.set", target_type="service",
                           target_id=str(service.id),
                           detail={"service_name": service.name})   # 시크릿 값은 기록하지 않음
```
> 기존 register_service의 audit/commit 위치를 확인해 같은 트랜잭션 안에서 기록한다.

- [ ] **Step 4: set_toss_secret_key 추가** — `app/services/registry.py` (rotate_keys 등 다른 update 함수 옆)

```python
async def set_toss_secret_key(db: AsyncSession, cipher: AesGcmCipher, *,
                              service_id: uuid.UUID, toss_secret_key: str,
                              actor_user_id: uuid.UUID | None = None) -> None:
    """서비스의 토스 시크릿 키를 설정/교체한다(AES 암호화 저장).

    빈 값은 거부한다. 기존에 키가 있었으면 'changed', 없었으면 'set'으로 감사 기록한다.
    감사로그에는 시크릿 값을 절대 남기지 않는다.
    """
    secret = (toss_secret_key or "").strip()
    if not secret:
        raise InputValidationError("토스 시크릿 키는 비어 있을 수 없습니다")
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    had_key = bool(service.toss_secret_key_encrypted)
    service.toss_secret_key_encrypted = cipher.encrypt(secret)
    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,
                       action=("service.toss_secret_key.changed" if had_key
                               else "service.toss_secret_key.set"),
                       target_type="service", target_id=str(service_id),
                       detail={"service_name": service.name})   # 시크릿 값 미기록
    await db.commit()
```
> import 확인: `InputValidationError`, `NotFoundError`(이미 registry에서 사용 중), `record_audit`.

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest tests/integration/test_registry.py -v`
Expected: PASS (신규 3 + 기존 회귀)

- [ ] **Step 6: 커밋**

```bash
git add app/services/registry.py tests/integration/test_registry.py
git commit -m "feat: 서비스 등록/수정에 toss_secret_key(암호화·감사로그) 추가"
```

---

### Task 4: 앱 배선 — provider 추가(전역 toss는 유지) + DI

**Files:**
- Modify: `app/main.py:233-247` (provider 생성/aclose; create_app override 연결)
- Modify: `app/core/deps.py` (`get_toss_provider` 추가)
- Test: `tests/integration/test_api_endpoints.py` 또는 기존 앱-기동 테스트로 회귀

**Interfaces:**
- Consumes: `TossClientProvider`(Task 2).
- Produces: `app.state.toss_provider`; `get_toss_provider(request) -> TossClientProvider`.

> 이 태스크는 provider를 **추가**만 한다. 기존 `app.state.toss`/`get_toss`는 그대로 두어 콜사이트가 깨지지 않게 한다(전환은 Task 5~6, 제거는 Task 7).

- [ ] **Step 1: main.py에 provider 생성** — `app.state.toss` 생성 직후

```python
        app.state.toss = toss_client or HttpTossClient(
            app_settings.toss_secret_key, app_settings.toss_api_base_url)
        # 서비스별 토스 클라이언트 해석기. 테스트가 toss_client(Fake)를 주입하면
        # override로 사용해 모든 서비스에 동일 Fake를 반환한다(키 불필요).
        app.state.toss_provider = TossClientProvider(
            app.state.cipher, app_settings.toss_api_base_url,
            override_client=toss_client)
```
import 추가: `from app.toss.provider import TossClientProvider`.
lifespan 종료부에 provider 정리(전역 toss aclose 부근):
```python
        await app.state.toss_provider.aclose()
        if own_toss and isinstance(app.state.toss, HttpTossClient):
            await app.state.toss.aclose()
```

- [ ] **Step 2: get_toss_provider 추가** — `app/core/deps.py` (get_toss 옆)

```python
def get_toss_provider(request: Request) -> "TossClientProvider":
    """서비스별 토스 클라이언트 해석기를 반환한다."""
    return request.app.state.toss_provider
```
import: `from app.toss.provider import TossClientProvider`.

- [ ] **Step 3: 회귀 확인**

Run: `uv run pytest tests/integration/test_api_endpoints.py -q`
Expected: PASS (앱 기동 + provider 추가가 기존 동작을 깨지 않음)

- [ ] **Step 4: 커밋**

```bash
git add app/main.py app/core/deps.py
git commit -m "feat: app.state.toss_provider + get_toss_provider 배선(전역 toss 유지)"
```

---

### Task 5: 요청 경로 콜사이트 전환(API v1 + 어드민)

**Files:**
- Modify: `app/api/v1/payments.py` (create_payment, cancel_payment)
- Modify: `app/admin/routes/payments.py`, `app/admin/routes/subscriptions.py` (수동결제·취소 등 toss 사용 라우트)
- Test: `tests/integration/test_api_endpoints.py`, `tests/integration/test_one_off_payment.py`, `tests/integration/test_subscription_manage.py`

**Interfaces:**
- Consumes: `get_toss_provider`(Task 4), 라우트에 이미 주입된 `service`(또는 대상 구독→서비스).
- Produces: (없음 — 서비스 계층 시그니처 불변)

> 패턴: 라우트에서 `toss: TossClient = Depends(get_toss)` 주입을 제거하고, 대신 `toss_provider = Depends(get_toss_provider)`를 주입해 본문에서 `toss = toss_provider.for_service(service)`로 해석한다. 키 미설정 서비스는 `TossKeyNotConfiguredError`가 발생해 기존 DomainError 핸들러가 처리한다(매핑은 Task 7에서 확인).

- [ ] **Step 1: 실패/회귀 테스트 추가** — 키 미설정 서비스가 단건결제 시 거부됨 (`tests/integration/test_one_off_payment.py`)

```python
@pytest.mark.asyncio
async def test_one_off_payment_rejected_when_no_toss_key(...):
    # 기존 단건결제 통합테스트와 동일하게 셋업하되, override 없이 provider가
    # 실제 키 해석을 하도록 한 서비스를 키 미설정으로 두고 결제 호출 → TOSS_KEY_NOT_CONFIGURED
    ...
```
> 주의: 대부분의 통합테스트는 `create_app(toss_client=FakeTossClient())`로 override가 걸려 있어 키 검사를 우회한다(의도된 호환). 이 거부 테스트는 **provider override 없이** 실제 키 해석 경로를 타도록 별도 구성하거나, provider를 override 없이 만들어 직접 `for_service`를 검증하는 식으로 작성한다. 작성이 과하게 복잡하면 이 거부 시나리오는 Task 7의 통합 지점에서 검증하고, 여기서는 라우트가 provider로 해석하도록 바꾼 뒤 기존 결제 테스트(override 경로)가 통과하는지로 회귀만 본다.

- [ ] **Step 2: api/v1/payments.py 전환**

`create_payment`/`cancel_payment`에서:
```python
    # 변경 전: toss: TossClient = Depends(get_toss),
    toss_provider: TossClientProvider = Depends(get_toss_provider),
```
본문 상단(서비스 확보 후):
```python
    toss = toss_provider.for_service(service)   # 서비스별 토스 클라이언트
```
import 정리: `get_toss` 제거, `get_toss_provider` 추가, `TossClientProvider` 타입 import.

- [ ] **Step 3: 어드민 라우트 전환** — `app/admin/routes/payments.py`, `app/admin/routes/subscriptions.py`

각 toss 사용 핸들러에서 대상 서비스(취소 대상 payment.service_id / 구독 sub.service_id)를 로드한 뒤 `toss = toss_provider.for_service(service)`로 해석해 서비스 함수에 전달. `get_toss` 주입 → `get_toss_provider`로 교체.
> 핸들러별로 service 객체를 어떻게 얻는지 실제 코드 확인 후 최소 변경. 이미 sub/payment를 로드하므로 `await db.get(Service, <obj>.service_id)`로 서비스 확보.

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/integration/test_one_off_payment.py tests/integration/test_subscription_manage.py tests/integration/test_api_endpoints.py -q`
Expected: PASS (override 경로로 기존 결제 동작 유지 + 신규 거부 테스트)

- [ ] **Step 5: 커밋**

```bash
git add app/api/v1/payments.py app/admin/routes/payments.py app/admin/routes/subscriptions.py tests/
git commit -m "feat: API·어드민 결제 경로를 서비스별 토스 클라이언트로 전환"
```

---

### Task 6: 스케줄러/갱신 경로 전환

**Files:**
- Modify: `app/services/renewals.py` (process_due 및 하위가 toss 대신 toss_provider 사용; _renew_one 등에서 service로 해석)
- Modify: `app/scheduler/runner.py:90` (process_due 호출 인자)
- Test: `tests/integration/test_renewals.py`, `tests/integration/test_scheduler.py`

**Interfaces:**
- Consumes: `app.state.toss_provider`, `Service`(이미 로드됨).
- Produces: `process_due(session_factory, redis, toss_provider, cipher, ...)` (toss → toss_provider로 교체)

- [ ] **Step 1: 실패/회귀 테스트** — MINUTE/일반 갱신이 provider 경로로 동작 + 키 미설정 서비스 구독 갱신은 결제 실패 처리(전체 스윕 중단 없음). `tests/integration/test_renewals.py`

```python
@pytest.mark.asyncio
async def test_renewal_uses_service_toss_client(...):
    # override Fake로 갱신이 정상 진행되는지(기존 갱신 테스트 회귀)
    ...
```
> 키 미설정 거부의 per-sub 격리는 override 없는 구성이 필요해 복잡하면 단위 수준(`_renew_one`이 TossKeyNotConfiguredError를 결제 실패로 처리)으로 좁혀 검증하거나 회귀 위주로 둔다.

- [ ] **Step 2: process_due 시그니처/호출 전환** — `app/services/renewals.py`

`toss: TossClient` 파라미터를 `toss_provider: TossClientProvider`로 바꾸고, 하위 헬퍼(`_renew_one`, `_expire_*`, `reconcile_pending`)로 `toss_provider`를 전달. 실제 토스 호출 직전(서비스 로드 지점, 예: line 101 `service = await db.get(Service, sub.service_id)` 이후)에서:
```python
    toss = toss_provider.for_service(service)   # 서비스별 토스 클라이언트
```
키 미설정으로 `TossKeyNotConfiguredError`가 나면 해당 구독을 결제 실패로 처리(기존 TossError 처리와 동일 흐름)하고 다음 구독으로 진행 — 전체 스윕을 멈추지 않는다.
> `_expire_*`는 실제 토스 호출이 없을 수 있다(상태 전이만). 그런 경우 toss 해석을 호출 직전까지 미뤄 불필요한 키 요구를 피한다(만료에 토스 호출이 없으면 provider 호출도 하지 않음).

- [ ] **Step 3: scheduler runner 호출 전환** — `app/scheduler/runner.py:90`

```python
        stats = await process_due(app.state.session_factory, redis, app.state.toss_provider,
                                  cipher, ...)
```
(`app.state.toss` → `app.state.toss_provider`)

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/integration/test_renewals.py tests/integration/test_scheduler.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/renewals.py app/scheduler/runner.py tests/integration/test_renewals.py
git commit -m "feat: 갱신 스케줄러를 서비스별 토스 클라이언트로 전환"
```

---

### Task 7: 전역 키 제거(컷오버) + 에러 응답 매핑 확인

**Files:**
- Modify: `app/main.py` (app.state.toss/own_toss/aclose 제거; create_app toss_client → provider override 전용)
- Modify: `app/core/deps.py` (get_toss 제거)
- Modify: `app/core/config.py` (toss_secret_key 제거)
- Modify: `.env`, `.env.dev`, `.env.prod`, `.env.example` (TOSS_SECRET_KEY 제거)
- Modify: (필요 시) API 에러 핸들러에 `TOSS_KEY_NOT_CONFIGURED` 매핑 확인
- Test: 전체 스위트

**Interfaces:**
- Produces: 전역 toss 부재. 모든 토스 호출은 provider 경유.

- [ ] **Step 1: 잔존 get_toss/app.state.toss 사용처 확인**

Run: `grep -rnE "app\.state\.toss\b|get_toss\b|toss_secret_key" app --include="*.py" | grep -v provider | grep -v test`
Expected: Task 5·6에서 모두 전환되어 남은 사용처가 없어야 함(있으면 먼저 전환).

- [ ] **Step 2: main.py 컷오버** — 전역 toss 제거, provider는 override만 사용

```python
    own_engine = engine is None
    # (own_toss 제거)
    ...
        # (app.state.toss 생성 제거)
        app.state.toss_provider = TossClientProvider(
            app.state.cipher, app_settings.toss_api_base_url,
            override_client=toss_client)   # 테스트 주입 시 Fake override, 운영은 None → 서비스별 해석
    ...
        await app.state.toss_provider.aclose()
        # (app.state.toss aclose 블록 제거)
```
`HttpTossClient` import가 main에서 더 불필요하면 정리(provider가 사용).

- [ ] **Step 3: get_toss 제거** — `app/core/deps.py`에서 `get_toss` 함수 삭제(이미 미사용).

- [ ] **Step 4: config.toss_secret_key 제거** — `app/core/config.py`의 `toss_secret_key: str = ""` 삭제. `toss_api_base_url`/타임아웃 유지. docstring의 "toss_secret_key는 .env" 문구 갱신.

- [ ] **Step 5: .env 키 제거** — `.env`, `.env.dev`, `.env.prod`, `.env.example`에서 `TOSS_SECRET_KEY=...` 라인 제거. (`.env*`는 gitignore — `.env.example`만 커밋 대상)

- [ ] **Step 6: 에러 응답 매핑 확인** — API/어드민에서 `TossKeyNotConfiguredError`(DomainError)가 깔끔한 응답으로 매핑되는지 확인. 기존 DomainError 핸들러가 code/message를 그대로 노출하면 추가 작업 불필요; 아니면 핸들러에 케이스 추가.

- [ ] **Step 7: 전체 테스트**

Run: `uv run pytest -q`
Expected: PASS (전체). override 경로로 기존 결제·갱신 테스트 통과, 전역 키 부재.

- [ ] **Step 8: 커밋**

```bash
git add app/main.py app/core/deps.py app/core/config.py .env.example
git commit -m "feat: 전역 TOSS_SECRET_KEY 제거 — 서비스별 키로 완전 전환"
```

---

### Task 8: 어드민 UI — 서비스 등록/수정 폼 + 감사 라벨

**Files:**
- Modify: `app/admin/routes/services.py` (등록 폼에 toss_secret_key 전달; 수정/설정 핸들러에서 set_toss_secret_key)
- Modify: `app/admin/templates/services/*` (등록 폼 입력칸; 상세/수정에 "설정됨/미설정" + 설정 입력)
- Modify: `app/admin/audit_labels.py` (신규 액션 한글 라벨)
- Test: `tests/integration/` 어드민 서비스 테스트(있으면) + `tests/security/test_admin_security.py` 회귀

**Interfaces:**
- Consumes: `register_service(..., toss_secret_key=)`, `set_toss_secret_key`(Task 3).

- [ ] **Step 1: 등록 라우트에 키 전달** — `app/admin/routes/services.py` 등록 핸들러

```python
    toss_secret_key = str(form.get("toss_secret_key", "")).strip() or None
    creds = await register_service(db, cipher, name=..., allowed_ips=...,
                                   manager_user_ids=..., primary_user_id=...,
                                   cancellation_enabled=..., cancellation_fee_percent=...,
                                   toss_secret_key=toss_secret_key,
                                   actor_user_id=ctx.user.id)
```

- [ ] **Step 2: 수정/설정 핸들러** — 서비스 상세/수정에서 키 설정·교체 폼 제출 시

```python
    new_key = str(form.get("toss_secret_key", "")).strip()
    if new_key:
        await set_toss_secret_key(db, cipher, service_id=service_id,
                                  toss_secret_key=new_key, actor_user_id=ctx.user.id)
```
(빈 값이면 변경 없음 — 기존 키 유지)

- [ ] **Step 3: 템플릿** — 등록 폼에 입력칸 추가, 상세/수정에 상태 표시

```html
<!-- 등록/수정 폼 -->
<label for="toss_secret_key">토스 시크릿 키</label>
<input id="toss_secret_key" name="toss_secret_key" type="password" autocomplete="off"
       placeholder="{% if service and service.toss_secret_key_encrypted %}설정됨 — 변경 시에만 입력{% else %}미설정 — 입력 시 저장{% endif %}">
<small>AES 암호화 저장. 저장 후에는 다시 표시되지 않습니다(변경 시 재입력).</small>
```
> 상세 화면에는 "토스 시크릿 키: {{ '설정됨' if service.toss_secret_key_encrypted else '미설정' }}"만 표시. 평문 절대 표시 금지.

- [ ] **Step 4: 감사 라벨 추가** — `app/admin/audit_labels.py`

```python
    "service.toss_secret_key.set": "토스 시크릿 키 설정",
    "service.toss_secret_key.changed": "토스 시크릿 키 변경",
```

- [ ] **Step 5: 검증(렌더/회귀)**

Run: `uv run pytest tests/security/test_admin_security.py -q` (+ 어드민 서비스 통합테스트 있으면 함께). 어드민을 띄워 서비스 등록/수정에서 키 입력·"설정됨" 표시·감사로그 기록(값 미노출) 수동 확인.
Expected: PASS / 수동 확인 OK.

- [ ] **Step 6: 커밋**

```bash
git add app/admin/routes/services.py app/admin/templates/services/ app/admin/audit_labels.py tests/
git commit -m "feat: 어드민 서비스 등록/수정에 toss_secret_key 입력 + 감사 라벨"
```

---

### Task 9: 문서 + 워크로그

**Files:**
- Modify: 결제/보안/서비스 등록 설명 매뉴얼 .md (grep로 식별) + 빌드 재실행
- Create: `docs/audit/2026-06-23-per-service-toss-secret-key-worklog.md`

- [ ] **Step 1: 매뉴얼 갱신**

대상 문서 식별:
```bash
grep -rln "toss_secret_key\|TOSS_SECRET_KEY\|서비스 등록\|시크릿" docs/manual/dev_manual docs/user_manual
```
서비스 등록/보안 문서에 추가: "서비스별 toss_secret_key를 등록(AES 암호화 저장), 미설정 시 결제 거부(TOSS_KEY_NOT_CONFIGURED), 전역 TOSS_SECRET_KEY는 제거됨, client_key는 서비스 프론트 자체 사용."

- [ ] **Step 2: 매뉴얼 재빌드**

Run: `uv run --with markdown python docs/user_manual/build.py` (+ dev_manual 빌드 스크립트가 있으면 실행)
Expected: 재생성 완료.

- [ ] **Step 3: 워크로그 작성** — `docs/audit/2026-06-23-per-service-toss-secret-key-worklog.md`

내용: 목적(서비스별 토스 키), 결정(암호화 저장·전역 제거·키 없으면 거부·등록 선택·감사로그 값 제외), 변경 요약(모델+마이그레이션 / provider+error / registry / 앱배선 / API·어드민 전환 / 스케줄러 전환 / 전역 제거 / 어드민 UI / 문서), 배포 순서 주의(마이그레이션 → 각 서비스 키 등록 → .env 키 제거), 검증(전체 테스트 통과), 설계·계획 문서 링크.

- [ ] **Step 4: 커밋**

```bash
git add docs/
git commit -m "docs: 서비스별 toss_secret_key 매뉴얼 갱신 + 워크로그"
```

---

## Self-Review (작성자 점검)

- **Spec coverage:** 모델+마이그레이션(T1)·provider+error(T2)·registry 등록/set+감사(T3)·앱배선(T4)·API/어드민 전환(T5)·스케줄러 전환(T6)·전역 제거+에러매핑(T7)·어드민 UI+감사라벨(T8)·문서/워크로그(T9). 스펙 전 항목 커버. 암호화·키없으면거부·값미기록·전역제거·등록선택 모두 태스크에 반영.
- **Placeholder scan:** 핵심 코드(provider/error/모델/registry/배선)는 완전 코드 제공. 일부 라우트/테스트는 "기존 콜사이트·픽스처 확인 후 최소 변경" 지시 + 패턴 명시(파일별 구조가 실제와 달라 라인 고정이 위험한 구간). 콜사이트 전환은 기계적 치환(get_toss→provider.for_service(service))이라 패턴으로 충분.
- **Type consistency:** `TossClientProvider(cipher, base_url, *, override_client, factory)` / `.for_service(service)->TossClient` / `.aclose()` 가 T2 정의 → T4·T5·T6·T7에서 동일하게 사용. `set_toss_secret_key(db, cipher, *, service_id, toss_secret_key, actor_user_id)` T3 정의 → T8 호출 일치. `register_service(..., toss_secret_key=)` T3 → T8 일치. `TossKeyNotConfiguredError`(code TOSS_KEY_NOT_CONFIGURED) T2 → T5/T6/T7 일치.
- **주의(실행자):** 테스트 대부분 `create_app(toss_client=Fake)`로 provider override가 걸려 키 검사를 우회한다(의도된 호환). "키 없으면 거부" 시나리오는 override 없는 구성/단위 검증으로 별도 작성. 픽스처 이름은 각 테스트 파일의 기존 것을 사용. 마이그레이션 down_revision은 실제 head 확인 후 작성.
