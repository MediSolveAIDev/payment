# 이메일 발송 결과 토스트 알림 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리자 화면의 메일 발송 버튼 3곳(서비스 등록·계정 생성·비밀번호 재설정)에서 발송 성공/실패를 토스트로 표시한다.

**Architecture:** `EmailSender.send()`가 `bool`을 반환하도록 바꾸고, 서비스 계층이 그 결과를 라우트로 전달한다. 라우트는 기존 `?error=` 패턴처럼 `?flash=...&flash_type=...` 쿼리파람으로 리다이렉트(또는 직접 렌더 시 kwargs)하고, `render()`가 쿼리파람을 템플릿 컨텍스트에 주입한다. `base.html`의 `body[data-flash]` → `admin.js` 토스트는 이미 구현되어 있어 변경 없음.

**Tech Stack:** FastAPI, Jinja2, pytest (기존 conftest의 `RecordingEmailSender` 픽스처 재사용)

**스펙:** `docs/superpowers/specs/2026-06-06-email-send-toast-design.md`

---

### Task 1: `EmailSender.send() -> bool`

**Files:**
- Modify: `app/notifications/email.py`
- Test: `tests/unit/test_email_sender.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_email_sender.py`의 기존 `test_gmail_send_builds_message_and_uses_starttls`에서 `await sender.send(...)` 줄을 반환값 검증으로 변경:

```python
        result = await sender.send("user@x.com", "제목", "본문 내용")

    assert result is True
```

기존 `test_gmail_send_swallows_errors`를 반환값 검증으로 변경:

```python
async def test_gmail_send_swallows_errors():
    """발송 실패가 호출자(결제/계정 흐름)를 깨뜨리지 않는다 — False만 반환."""
    sender = GmailEmailSender(host="h", port=587, username="u", password="p")
    with patch("app.notifications.email.smtplib.SMTP", side_effect=OSError("conn refused")):
        result = await sender.send("user@x.com", "제목", "본문")  # 예외 전파 없음
    assert result is False
```

파일 끝에 새 테스트 추가 (import에 `RecordingEmailSender` 추가):

```python
async def test_console_sender_returns_true():
    assert await ConsoleEmailSender().send("u@x.com", "s", "b") is True


async def test_recording_sender_returns_true_and_records():
    sender = RecordingEmailSender()
    assert await sender.send("u@x.com", "s", "b") is True
    assert len(sender.sent) == 1


async def test_recording_sender_fail_flag_returns_false_without_recording():
    sender = RecordingEmailSender()
    sender.fail = True
    assert await sender.send("u@x.com", "s", "b") is False
    assert sender.sent == []
```

import 줄 변경:

```python
from app.notifications.email import ConsoleEmailSender, GmailEmailSender, RecordingEmailSender
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/unit/test_email_sender.py -v`
Expected: FAIL — `assert result is True`에서 `None is True` 실패, `fail` 속성 없음(AttributeError) 등

- [ ] **Step 3: 구현**

`app/notifications/email.py` 변경:

Protocol:

```python
class EmailSender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> bool: ...
```

ConsoleEmailSender:

```python
    async def send(self, to: str, subject: str, body: str) -> bool:
        logger.info("EMAIL to=%s subject=%s\n%s", to, subject, body)
        return True
```

GmailEmailSender.send:

```python
    async def send(self, to: str, subject: str, body: str) -> bool:
        try:
            await asyncio.to_thread(self._send_sync, to, subject, body)
            logger.info("EMAIL sent to=%s subject=%s", to, subject)
            return True
        except Exception:  # noqa: BLE001 — 발송 실패가 결제/계정 흐름을 깨면 안 됨
            logger.exception("EMAIL 발송 실패 to=%s subject=%s", to, subject)
            return False
```

RecordingEmailSender:

```python
class RecordingEmailSender:
    """테스트용 — 발송 내역 기록. fail=True면 발송 실패 시뮬레이션."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail = False

    async def send(self, to: str, subject: str, body: str) -> bool:
        if self.fail:
            return False
        self.sent.append({"to": to, "subject": subject, "body": body})
        return True
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/unit/test_email_sender.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/notifications/email.py tests/unit/test_email_sender.py
git commit -m "feat(email): EmailSender.send가 발송 성공 여부(bool) 반환"
```

---

### Task 2: `render()` flash 쿼리파람 주입 + flash 헬퍼

**Files:**
- Modify: `app/admin/__init__.py:12-14` (render 함수)
- Create: `app/admin/flash.py`
- Test: `tests/e2e/test_email_flash.py` (새 파일)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/e2e/test_email_flash.py` 생성:

```python
"""메일 발송 결과 flash → 토스트 표시 (base.html data-flash)."""
from urllib.parse import quote

from tests.factories import create_user
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


async def test_render_injects_flash_from_query_params(client, db, redis_client):
    await _admin(client, db, redis_client)
    resp = await client.get("/admin/users?flash=hello&flash_type=error")
    assert 'data-flash="hello"' in resp.text
    assert 'data-flash-type="error"' in resp.text


async def test_no_flash_param_no_data_flash_attr(client, db, redis_client):
    await _admin(client, db, redis_client)
    resp = await client.get("/admin/users")
    assert "data-flash" not in resp.text
```

그리고 flash 헬퍼 단위 테스트를 같은 파일에 추가:

```python
def test_email_flash_qs_success():
    from app.admin.flash import email_flash_qs
    assert email_flash_qs(True, "메일을 발송했습니다") == f"flash={quote('메일을 발송했습니다')}"


def test_email_flash_qs_failure():
    from app.admin.flash import EMAIL_FAIL_MSG, email_flash_qs
    qs = email_flash_qs(False, "메일을 발송했습니다")
    assert qs == f"flash={quote(EMAIL_FAIL_MSG)}&flash_type=error"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/e2e/test_email_flash.py -v`
Expected: FAIL — `data-flash="hello"` 미존재, `app.admin.flash` 모듈 없음(ImportError)

- [ ] **Step 3: 구현**

`app/admin/flash.py` 생성:

```python
from urllib.parse import quote

EMAIL_FAIL_MSG = "메일 발송에 실패했습니다. SMTP 설정을 확인하세요"


def email_flash_qs(sent: bool, success_msg: str) -> str:
    """메일 발송 결과를 리다이렉트 URL에 붙일 flash 쿼리스트링으로 변환."""
    if sent:
        return f"flash={quote(success_msg)}"
    return f"flash={quote(EMAIL_FAIL_MSG)}&flash_type=error"
```

`app/admin/__init__.py`의 `render()` 변경:

```python
def render(request: Request, name: str, ctx: AdminContext | None = None, **extra):
    context = {"ctx": ctx, **extra}
    # 리다이렉트로 전달된 flash 메시지(?flash=...&flash_type=...) → 토스트
    context.setdefault("flash", request.query_params.get("flash"))
    context.setdefault("flash_type", request.query_params.get("flash_type"))
    return templates.TemplateResponse(request, name, context)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/e2e/test_email_flash.py -v`
Expected: 4개 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/admin/__init__.py app/admin/flash.py tests/e2e/test_email_flash.py
git commit -m "feat(admin): flash 쿼리파람 → 토스트 표시 기반 마련"
```

---

### Task 3: 비밀번호 재설정 메일 발송 결과 토스트

**Files:**
- Modify: `app/services/auth.py:187-213` (issue_password_reset)
- Modify: `app/admin/routes/users.py:187-198` (users_reset_password)
- Test: `tests/e2e/test_email_flash.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/e2e/test_email_flash.py`에 추가:

```python
async def test_reset_password_success_flash(client, db, redis_client):
    csrf = await _admin(client, db, redis_client)
    target, _ = await create_user(db, role="SYSTEM_ADMIN")
    resp = await client.post(f"/admin/users/{target.id}/reset-password",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    assert quote("비밀번호 재설정 메일을 발송했습니다") in resp.headers["location"]


async def test_reset_password_failure_flash(client, db, redis_client, email_sender):
    email_sender.fail = True
    csrf = await _admin(client, db, redis_client)
    target, _ = await create_user(db, role="SYSTEM_ADMIN")
    resp = await client.post(f"/admin/users/{target.id}/reset-password",
                             data={"csrf_token": csrf})
    assert resp.status_code == 303
    assert "flash_type=error" in resp.headers["location"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/e2e/test_email_flash.py -v -k reset_password`
Expected: FAIL — location에 flash 없음

- [ ] **Step 3: 구현**

`app/services/auth.py` — `issue_password_reset` 반환 타입 `None → bool`, 마지막 `await email_sender.send(...)`를 `return`으로:

```python
async def issue_password_reset(db: AsyncSession, email_sender, *, user_id,
                               base_url: str, actor_user_id,
                               redis: Redis | None = None) -> bool:
    """관리자가 담당자 비밀번호 재설정 토큰 발급 + 메일 발송. 반환: 메일 발송 성공 여부.

    redis를 넘기면 발급 즉시 해당 사용자의 기존 세션을 모두 파기한다 —
    관리자 주도 재설정은 계정 탈취 의심 상황일 수 있으므로 활성 세션 창을 닫는다.
    """
```

(본문 마지막 부분만 변경)

```python
    return await email_sender.send(
        user.email, "[결제시스템] 비밀번호 재설정 안내",
        f"아래 링크에서 비밀번호를 다시 설정해주세요 (48시간 유효):\n"
        f"{base_url}/admin/setup-password?token={token}")
```

`app/admin/routes/users.py` — import 추가:

```python
from app.admin.flash import email_flash_qs
```

`users_reset_password` 마지막 두 줄 변경:

```python
    sent = await issue_password_reset(db, email_sender, user_id=user_id,
                                      base_url=settings.base_url,
                                      actor_user_id=ctx.user.id, redis=redis)
    qs = email_flash_qs(sent, "비밀번호 재설정 메일을 발송했습니다")
    return RedirectResponse(f"/admin/users/{user_id}?{qs}", status_code=303)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/e2e/test_email_flash.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 기존 테스트 회귀 확인**

Run: `pytest tests/e2e tests/integration -q`
Expected: 전부 PASS (issue_password_reset 기존 호출부는 반환값 미사용)

- [ ] **Step 6: 커밋**

```bash
git add app/services/auth.py app/admin/routes/users.py tests/e2e/test_email_flash.py
git commit -m "feat(admin): 비밀번호 재설정 메일 발송 결과 토스트"
```

---

### Task 4: 계정 생성 메일 발송 결과 토스트

**Files:**
- Modify: `app/services/accounts.py:65-120` (create_account)
- Modify: `app/admin/routes/users.py:66-83` (users_create)
- Modify: `tests/integration/test_accounts.py` (반환값 언패킹)
- Test: `tests/e2e/test_email_flash.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/e2e/test_email_flash.py`에 추가 (파일 상단 import에 `create_service` 추가: `from tests.factories import create_service, create_user`):

```python
async def test_create_account_success_flash(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="flash-acc-svc")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post("/admin/users", data={
        "csrf_token": csrf, "email": "flash-mgr@x.com", "role": "SERVICE_MANAGER",
        "service_ids": [str(svc.id)]})
    assert resp.status_code == 303
    assert quote("계정 설정 메일을 발송했습니다") in resp.headers["location"]


async def test_create_account_failure_flash(client, db, redis_client, cipher,
                                            email_sender):
    email_sender.fail = True
    svc, _, _ = await create_service(db, cipher, name="flash-acc-fail")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post("/admin/users", data={
        "csrf_token": csrf, "email": "flash-mgr2@x.com", "role": "SERVICE_MANAGER",
        "service_ids": [str(svc.id)]})
    assert resp.status_code == 303
    assert "flash_type=error" in resp.headers["location"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/e2e/test_email_flash.py -v -k create_account`
Expected: FAIL — location에 flash 없음

- [ ] **Step 3: 구현**

`app/services/accounts.py` — `create_account` 반환 타입 `User → tuple[User, bool]` (시그니처와 docstring):

```python
async def create_account(db: AsyncSession, email_sender: EmailSender, *,
                         email: str, role: str, service_ids: list[uuid.UUID],
                         base_url: str, phone: str | None = None,
                         actor_user_id: uuid.UUID | None = None) -> tuple[User, bool]:
    """관리자 계정 생성(PENDING) + 비밀번호 설정 메일. 반환: (User, 메일 발송 성공 여부).

    SERVICE_MANAGER는 1개 이상 서비스 필요(첫 서비스=주, 나머지=추가부여).
    SYSTEM_ADMIN은 서비스 없음.
    """
```

본문 마지막 변경:

```python
    sent = await email_sender.send(
        email, "[결제시스템] 관리자 계정 설정 안내",
        f"안녕하세요. 결제 관리 콘솔 계정({role})이 생성되었습니다.\n"
        f"아래 링크에서 비밀번호를 설정해주세요 (48시간 유효):\n"
        f"{base_url}/admin/setup-password?token={token}")
    return user, sent
```

`app/admin/routes/users.py` — `users_create`에서 호출/리다이렉트 변경:

```python
    try:
        _, sent = await account_service.create_account(
            db, email_sender, email=str(form.get("email", "")),
            role=str(form.get("role", "")), service_ids=_parse_service_ids(form),
            phone=str(form.get("phone", "")),
            base_url=settings.base_url, actor_user_id=ctx.user.id)
    except DomainError as exc:
        services = await registry.list_services(db)
        return render(request, "users/new.html", ctx=ctx, services=services,
                      error=exc.message)
    qs = email_flash_qs(sent, "계정 설정 메일을 발송했습니다")
    return RedirectResponse(f"/admin/users?{qs}", status_code=303)
```

`tests/integration/test_accounts.py` — 반환값을 사용하는 호출부 언패킹으로 변경 (반환값을 안 쓰는 호출부는 그대로). 대상 줄과 변경:

- L19: `user = await accounts.create_account(` → `user, _ = await accounts.create_account(`
- L38: `user = await accounts.create_account(` → `user, _ = await accounts.create_account(`
- L99: `user = await accounts.create_account(` → `user, _ = await accounts.create_account(`
- L112: `a = await accounts.create_account(db, email, ...)` → `a, _ = await accounts.create_account(db, email, ...)`
- L126: `u = await accounts.create_account(db, email, ...)` → `u, _ = await accounts.create_account(db, email, ...)`
- L137: `u = await accounts.create_account(db, email, ...)` → `u, _ = await accounts.create_account(db, email, ...)`
- L146: `u = await accounts.create_account(db, email, ...)` → `u, _ = await accounts.create_account(db, email, ...)`

(L49, L54, L57, L107, L114는 반환값 미사용 — 변경 없음)

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/e2e/test_email_flash.py tests/integration/test_accounts.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/services/accounts.py app/admin/routes/users.py tests/integration/test_accounts.py tests/e2e/test_email_flash.py
git commit -m "feat(admin): 계정 생성 메일 발송 결과 토스트"
```

---

### Task 5: 서비스 등록 메일 발송 결과 토스트

**Files:**
- Modify: `app/services/registry.py:35-101` (IssuedCredentials, register_service)
- Modify: `app/admin/routes/services.py:54-74` (services_create)
- Test: `tests/e2e/test_email_flash.py`

서비스 등록은 리다이렉트가 아니라 키 표시 페이지(`services/keys.html`)를 직접 렌더하므로 flash를 render kwargs로 전달한다. 또한 담당자 계정이 이미 존재하면 메일을 보내지 않으므로 그때는 토스트도 없다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/e2e/test_email_flash.py`에 추가:

```python
async def test_service_create_success_flash_on_keys_page(client, db, redis_client):
    csrf = await _admin(client, db, redis_client)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "flash-svc-new",
        "manager_email": "flash-newmgr@x.com", "allowed_ips": "10.0.0.1"})
    assert resp.status_code == 200
    assert 'data-flash="담당자에게 계정 설정 메일을 발송했습니다"' in resp.text


async def test_service_create_failure_flash_on_keys_page(client, db, redis_client,
                                                         email_sender):
    email_sender.fail = True
    csrf = await _admin(client, db, redis_client)
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "flash-svc-fail",
        "manager_email": "flash-failmgr@x.com", "allowed_ips": "10.0.0.1"})
    assert resp.status_code == 200
    assert 'data-flash-type="error"' in resp.text


async def test_service_create_existing_manager_no_flash(client, db, redis_client):
    """담당자 계정이 이미 있으면 메일을 보내지 않음 — 토스트도 없음."""
    csrf = await _admin(client, db, redis_client)
    existing, _ = await create_user(db, role="SERVICE_MANAGER",
                                    email="flash-exist@x.com")
    resp = await client.post("/admin/services", data={
        "csrf_token": csrf, "name": "flash-svc-exist",
        "manager_email": "flash-exist@x.com", "allowed_ips": "10.0.0.1"})
    assert resp.status_code == 200
    assert "data-flash" not in resp.text
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/e2e/test_email_flash.py -v -k service_create`
Expected: FAIL — data-flash 미존재

- [ ] **Step 3: 구현**

`app/services/registry.py` — `IssuedCredentials`에 필드 추가:

```python
@dataclass
class IssuedCredentials:
    service: Service
    api_key: str
    hmac_secret: str
    setup_token: str | None  # 신규 담당자 계정이 만들어졌을 때만
    email_sent: bool | None = None  # None=메일 발송 없음(기존 담당자)
```

`register_service` 본문 — `setup_token` 초기화 옆에 `email_sent` 추가, send 결과 캡처, 반환에 포함:

```python
    setup_token: str | None = None
    email_sent: bool | None = None
    user = await db.scalar(select(User).where(User.email == manager_email))
    if user is None:
        user = User(email=manager_email, role=UserRole.SERVICE_MANAGER,
                    service_id=service.id, status=UserStatus.PENDING)
        db.add(user)
        await db.flush()
        setup_token = generate_setup_token()
        db.add(PasswordSetupToken(user_id=user.id, token_hash=sha256_hex(setup_token),
                                  expires_at=utcnow() + SETUP_TOKEN_TTL))
        # TODO(운영 SMTP 도입 시): 커밋 실패 시 죽은 링크가 발송되지 않도록
        # 발송을 커밋 이후로 이동하거나 outbox 패턴 적용
        email_sent = await email_sender.send(
            manager_email, "[결제시스템] 관리자 계정 설정 안내",
            f"안녕하세요. {name} 서비스의 구독/결제 관리자 계정이 생성되었습니다.\n"
            f"아래 링크에서 비밀번호를 설정해주세요 (48시간 유효):\n"
            f"{base_url}/admin/setup-password?token={setup_token}")
```

```python
    return IssuedCredentials(service=service, api_key=api_key,
                             hmac_secret=hmac_secret, setup_token=setup_token,
                             email_sent=email_sent)
```

`app/admin/routes/services.py` — import 추가:

```python
from app.admin.flash import EMAIL_FAIL_MSG
```

`services_create`의 마지막 `return render(...)` 변경:

```python
    flash = flash_type = None
    if creds.email_sent is True:
        flash = "담당자에게 계정 설정 메일을 발송했습니다"
    elif creds.email_sent is False:
        flash, flash_type = EMAIL_FAIL_MSG, "error"
    return render(request, "services/keys.html", ctx=ctx, service=creds.service,
                  api_key=creds.api_key, hmac_secret=creds.hmac_secret,
                  flash=flash, flash_type=flash_type)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pytest tests/e2e/test_email_flash.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 테스트 회귀 확인**

Run: `pytest -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add app/services/registry.py app/admin/routes/services.py tests/e2e/test_email_flash.py
git commit -m "feat(admin): 서비스 등록 시 담당자 메일 발송 결과 토스트"
```
