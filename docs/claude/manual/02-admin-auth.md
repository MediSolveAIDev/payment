# 02. 관리자 계정 · 로그인 · 세션 · 권한

> Admin 콘솔(`/admin`)을 쓰는 **사람**의 계정·인증·권한 전 과정.
> 이 흐름(세션·CSRF·스코프)은 01번을 포함한 **모든 Admin 기능의 토대**다.
>
> 선행: [00-overview.md](00-overview.md), [01-service-registry.md](01-service-registry.md)의 "공통 관문" 절.

---

## 0. 한눈에 보기

계정에는 두 역할이 있다:
- **`SYSTEM_ADMIN`** — 전체 관리(서비스 등록·계정 관리 등). 스코프 없음.
- **`SERVICE_MANAGER`** — 담당 서비스만(요금제·구독·정산 등을 자기 서비스 범위에서).

| 하는 일 | HTTP | URL | 라우트 | 서비스 계층 |
|---|---|---|---|---|
| 최초 관리자 생성 | (CLI) | `python -m app.cli create-admin` | `cli.main` | `auth.create_system_admin` |
| 로그인 폼 | GET | `/admin/login` | `login_page` | — |
| **로그인** | POST | `/admin/login` | `login_submit` | `auth.login` |
| 로그아웃 | POST | `/admin/logout` | `logout` | `auth.logout` |
| 비번 설정 폼 | GET | `/admin/setup-password` | `setup_password_page` | — |
| **비번 설정/재설정** | POST | `/admin/setup-password` | `setup_password_submit` | `auth.setup_password` |
| 계정 목록 | GET | `/admin/users` | `users_list` | (직접 쿼리) |
| 계정 생성 | POST | `/admin/users` | `users_create` | `accounts.create_account` |
| 계정 상세 | GET | `/admin/users/{id}` | `users_detail` | `accounts.list_managed_services` |
| 계정 수정 | POST | `/admin/users/{id}/edit` | `users_update` | `accounts.update_account` |
| 비활성/복구 | POST | `/admin/users/{id}/disable` | `users_disable` | `accounts.set_account_disabled` |
| 삭제(소프트) | POST | `/admin/users/{id}/delete` | `users_delete` | `accounts.delete_account` |
| 비번 재설정 메일 | POST | `/admin/users/{id}/reset-password` | `users_reset_password` | `auth.issue_password_reset` |
| 담당 서비스 추가/해제 | POST | `/admin/users/{id}/services[...]` | `users_assign/unassign_service` | `accounts.assign/unassign_service` |

관련 파일:
- 라우트: `app/admin/routes/auth.py`(로그인/세션), `app/admin/routes/users.py`(계정 관리)
- 서비스 계층: `app/services/auth.py`(로그인/세션/비번/토큰), `app/services/accounts.py`(계정 CRUD/배정)
- 인증 의존성: `app/admin/deps.py`(`require_user`/`require_admin`/`require_any`/`validate_csrf`)
- 모델: `app/models/user.py`(User, PasswordSetupToken)
- 보안: `app/core/security.py`(argon2 해시), 세션 저장소: **Redis**
- CLI: `app/cli.py`

핵심 설계 한 줄 요약: **비밀번호는 argon2 해시로 DB에**, **세션은 Redis에**(서버측, TTL 슬라이딩),
**권한은 역할(RBAC) + 스코프(`service_ids`)** 로 통제한다.

---

## 1. 데이터 모델

### `User` (`app/models/user.py`)
| 컬럼 | 의미 |
|---|---|
| `email` (unique) | 로그인 ID |
| `password_hash` | **argon2 해시**(평문 저장 안 함). PENDING이면 빈 문자열 |
| `role` | `SYSTEM_ADMIN` / `SERVICE_MANAGER` |
| `service_id` (nullable, FK) | **주 서비스**(SERVICE_MANAGER의 1차 담당). SYSTEM_ADMIN은 None |
| `status` | `PENDING`/`ACTIVE`/`LOCKED`/`DISABLED`/`DELETED` |
| `failed_login_count` | 연속 로그인 실패 횟수(잠금 판단) |
| `locked_until` | 잠금 해제 시각(자동 해제용) |

추가 담당 서비스는 `UserService`(다대다, 문서 01 참고)로 표현. "유효 담당" = `service_id` ∪ `UserService`.

### 계정 상태(`UserStatus`) 의미
- `PENDING` — 생성됨, **비밀번호 미설정**(로그인 불가, 설정 메일 대기)
- `ACTIVE` — 정상
- `LOCKED` — 로그인 연속 실패로 잠김(`locked_until` 지나면 자동 해제)
- `DISABLED` — 관리자가 비활성(복구 가능)
- `DELETED` — 소프트 삭제(목록에서 숨김, 로그인 시 "존재하지 않는 것처럼" 처리)

### `PasswordSetupToken`
비밀번호 설정/재설정 링크의 1회용 토큰. **원문은 메일 링크에만, DB엔 해시(`token_hash`)**.
`expires_at`(만료), `used_at`(사용 시각 — 1회용 보장).

---

## 2. 계정 생명주기 (생성 → 비번 설정 → 활성)

### 2-1. 부트스트랩: 최초 관리자 — CLI

서버에 계정이 하나도 없을 때 첫 `SYSTEM_ADMIN`을 만든다(웹으로는 못 만듦 — 닭과 달걀 문제).

```bash
uv run python -m app.cli create-admin --email admin@... --password '<10자 이상>'
```

`cli.py` → `auth.create_system_admin`: 비번 길이 검증(10자+) → 이메일 중복 검사 →
`User(role=SYSTEM_ADMIN, status=ACTIVE, password_hash=argon2)` 생성 → 감사 `user.create_admin` → commit.
이 계정은 PENDING을 거치지 않고 **바로 ACTIVE**(비번을 CLI에서 직접 받으므로).

### 2-2. 관리자가 계정 생성 — `POST /admin/users`

```python
# routes/users.py:71
@router.post("/users")
async def users_create(request, ctx=Depends(require_admin), db=..., email_sender=..., settings=...):
    await validate_csrf(request, ctx)
    form = await request.form()
    try:
        _, sent = await account_service.create_account(
            db, email_sender, email=..., role=..., service_ids=_parse_service_ids(form),
            phone=..., base_url=settings.base_url, actor_user_id=ctx.user.id)
    except DomainError as exc:
        return render(... error=exc.message)            # 폼 에러
    qs = email_flash_qs(sent, "계정 설정 메일을 발송했습니다")  # 메일 성공/실패 → flash
    return RedirectResponse(f"/admin/users?{qs}", status_code=303)
```

`accounts.create_account`(`app/services/accounts.py:65`)가 하는 일:
1. 이메일 정규화(소문자) + 필수 검증, 전화번호 형식 검증.
2. 역할 검증. **SYSTEM_ADMIN이면 서비스 배정 무시**(전체 권한이므로 서비스 불필요).
3. `service_ids` 중복 제거 → 모든 서비스가 실제 존재하는지 확인.
4. 이메일 중복이면 `ConflictError`.
5. `User(status=PENDING, password_hash="")` 생성. **첫 서비스 = 주(service_id), 나머지 = UserService**.
   - SERVICE_MANAGER는 서비스 0개로도 생성 가능(나중에 서비스 등록 시 배정 — 문서 01).
6. `PasswordSetupToken` 발급(48시간) + 감사 `account.create` → commit.
7. **설정 메일 발송**(`base_url/admin/setup-password?token=...`). 반환 `(user, 메일성공여부)`.

> 포인트: 계정은 **PENDING으로 태어나** 비밀번호가 없다. 메일 링크로 본인이 설정해야 ACTIVE가 된다.
> 메일 발송 성공/실패는 `email_flash_qs`로 토스트 메시지를 만들어 리다이렉트에 실어 보낸다.

### 2-3. 비밀번호 설정 — `GET/POST /admin/setup-password`

메일 링크(`?token=...`)로 들어오는 화면. 폼에서 비번 2번 입력.

```python
# routes/auth.py:60
@router.post("/setup-password")
async def setup_password_submit(request, db=..., redis=...):
    form = await request.form()
    token, password, confirm = ...
    if password != confirm:
        return render("setup_password.html", error="비밀번호가 일치하지 않습니다")
    try:
        await auth_service.setup_password(db, token=token, password=password, redis=redis)
    except InputValidationError as exc:
        return render("setup_password.html", error=exc.message)
    return RedirectResponse("/admin/login", status_code=303)
```

`auth.setup_password`(`auth.py:124`):
1. 비번 길이 검증(10자+).
2. 토큰 검증 — `token_hash`로 조회 + **`used_at IS NULL`(미사용) + 만료 전**. 아니면 검증 에러.
3. 대상 User의 `password_hash` 설정, **`status=ACTIVE`**, 실패카운트/잠금 초기화.
4. 이 토큰 `used_at` 기록(1회용) + **같은 사용자의 다른 미사용 토큰 일괄 무효화**.
5. 감사 `auth.password_set` → commit.
6. `redis`가 있으면 **그 사용자의 기존 세션 전부 파기**(비번 변경 = 보안 이벤트).

CSRF가 없는 이유: 이 폼은 **로그인 전** 화면이라 세션/CSRF 토큰이 없다. 대신 **1회용 토큰**
자체가 인증 역할을 한다(소유 증명).

---

## 3. 로그인 (가장 중요) — `POST /admin/login`

### 3-1. 라우트

```python
# routes/auth.py:25
@router.post("/login")
async def login_submit(request, db=..., redis=..., settings=...):
    form = await request.form()
    email = form.get("email").strip(); password = form.get("password")
    ip = get_client_ip(request, settings)
    try:
        session_id, _user = await auth_service.login(db, redis, settings,
                                                     email=email, password=password, ip=ip)
    except AuthenticationError as exc:
        return render(request, "login.html", error=exc.message)   # 실패 → 폼에 에러
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax",
                        secure=(prod), max_age=session_ttl, path="/")    # 세션 쿠키 발급
    return response
```

성공하면 `session_id`를 **HttpOnly 쿠키**로 심고 대시보드로 리다이렉트. 쿠키 속성 의미:
- `httponly=True` — JS에서 쿠키 접근 불가(XSS로 세션 탈취 방지).
- `samesite="lax"` — 타 사이트發 요청에 쿠키 미전송(CSRF 완화).
- `secure=prod` — 운영에선 HTTPS에서만 전송.
- `max_age=session_ttl_seconds` — 쿠키 수명.

### 3-2. 핵심 로직 — `auth.login` (`auth.py:50`)

순서대로(각 분기가 보안 의미를 가짐):

```python
user = db.scalar(select(User).where(email == email))
if user is None:
    verify_password(password, _DUMMY_HASH)   # ① 타이밍 균등화
    record_audit("auth.login_failed", reason="unknown_email"); commit
    raise AuthenticationError(LOGIN_FAILED_MESSAGE)

now = utcnow()
if user.status == LOCKED:                      # ② 잠금 처리
    if locked_until and locked_until > now:
        raise AuthenticationError("계정이 잠겼습니다...")
    else:                                      #    잠금 시간 지났으면 자동 해제
        user.status = ACTIVE; failed_login_count = 0; locked_until = None

if user.status == DELETED:  raise AuthenticationError(LOGIN_FAILED_MESSAGE)  # ③ 존재 안 하는 척
if user.status == DISABLED: raise AuthenticationError("비활성화된 계정입니다...")
if user.status == PENDING:  raise AuthenticationError("비밀번호 설정이 필요합니다...")

if not verify_password(password, user.password_hash):   # ④ 비번 검증
    user.failed_login_count += 1
    if failed_login_count >= 5:                # 5회 실패 → 15분 잠금
        user.status = LOCKED; locked_until = now + 15분
    record_audit("auth.login_failed"); commit
    raise AuthenticationError(LOGIN_FAILED_MESSAGE)

user.failed_login_count = 0; locked_until = None        # ⑤ 성공
record_audit("auth.login"); commit                      #    감사 먼저 커밋
session_id = await _create_session(redis, settings, user)  # 그 다음 세션 생성
return session_id, user
```

초급자용 핵심 포인트:
- **① 타이밍 균등화**: 존재하지 않는 이메일이어도 더미 해시로 argon2 검증을 한 번 돌린다.
  안 그러면 "응답이 빠르면 없는 계정"이라는 시간차로 **계정 열거(enumeration)** 가 가능해진다.
- **③ 같은 실패 메시지**: 없는 이메일/틀린 비번/삭제 계정 모두 동일한
  "이메일 또는 비밀번호가 올바르지 않습니다"로 응답 → 어느 쪽이 틀렸는지 노출 안 함.
- **② 자동 잠금/해제**: 5회 연속 실패 시 15분 `LOCKED`. 시간이 지나면 다음 로그인 시도 때
  자동으로 `ACTIVE`로 풀고 카운트 초기화.
- **⑤ 순서**: 감사로그 commit이 **세션 생성보다 먼저**. "감사 기록 없는 유효 세션"을 막는다.
- 모든 로그인 시도(성공/실패)는 IP와 함께 감사로그에 남는다.

---

## 4. 세션 메커니즘 (Redis)

### 4-1. 세션 생성 — `_create_session` (`auth.py:30`)

```python
session_id = secrets.token_urlsafe(32)        # 추측 불가한 랜덤 ID
key = "session:" + session_id
async with redis.pipeline(transaction=True) as pipe:
    pipe.hset(key, mapping={                   # 세션 본문(Redis Hash)
        "user_id": ..., "role": ..., "service_id": ..., "csrf_token": token_urlsafe(32),
    })
    pipe.expire(key, session_ttl_seconds)      # TTL — 만료 시 자동 삭제
    pipe.sadd("user_sessions:{user_id}", session_id)  # 역색인(이 유저의 세션 집합)
    pipe.expire("user_sessions:{user_id}", session_ttl_seconds)
    await pipe.execute()
```

- 세션 ID만 쿠키에 들어가고, **실제 데이터는 서버측(Redis)** 에 있다(무상태 JWT가 아님 →
  서버가 언제든 즉시 무효화 가능).
- `csrf_token`을 세션 안에 함께 보관 → 폼 검증에 사용.
- `user_sessions:{id}` 집합은 "이 사용자의 모든 세션"을 추적 → 비번 변경/비활성 시 일괄 파기용.
- 파이프라인(transaction)으로 `hset`+`expire`를 묶어 **TTL 없는 불멸 세션**이 생기는 틈을 없앤다.

### 4-2. 세션 검증 + 슬라이딩 TTL — `get_session` (`auth.py:97`)

```python
data = await redis.hgetall("session:" + session_id)
if not data: return None
await redis.expire(key, session_ttl_seconds)   # 요청마다 만료 시각 갱신 = 유휴 타임아웃
return data
```

요청이 올 때마다 TTL을 다시 설정한다 → **활동 중이면 안 끊기고, 일정 시간 무활동이면 만료**(유휴 타임아웃).

### 4-3. 요청마다의 인증 — `require_user` (`deps.py:33`)

01번에서 본 그 관문. 매 Admin 요청에서:
```
쿠키 admin_session → get_session(redis) → 없으면 AdminAuthRequired(→ /admin/login)
세션의 user_id → DB에서 User 조회 → status != ACTIVE 면 AdminAuthRequired
effective_service_ids(user) → 담당 서비스 범위 계산
→ AdminContext(user, session_id, csrf_token, service_ids) 반환
```
- 세션이 살아있어도 **DB의 계정 상태를 매번 재확인**한다 → 세션 유효 중에 계정이 DISABLED 되면
  다음 요청부터 즉시 차단.

### 4-4. 로그아웃 / 강제 세션 파기

- **로그아웃** `POST /admin/logout` → `auth.logout`: 해당 세션 키 삭제 + 역색인에서 제거 + 쿠키 삭제.
- **`destroy_user_sessions(redis, user_id)`** — `user_sessions:{id}` 집합의 모든 세션을 한 번에 삭제.
  비밀번호 설정/재설정, 계정 비활성화, 삭제 시 호출되어 **기존 로그인 창을 전부 닫는다**(탈취 대응).

---

## 5. CSRF 보호

- 세션 생성 시 만든 `csrf_token`이 세션에 저장되고, `ctx.csrf_token`으로 라우트/템플릿에 전달.
- 모든 폼에 `<input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">`.
- 모든 POST 라우트가 `await validate_csrf(request, ctx)` 호출 → 폼 토큰 ≠ 세션 토큰이면 403.
- 상수시간 비교(`constant_time_equals`)로 타이밍 공격 방지.
- 예외: `/admin/login`·`/admin/setup-password` POST는 **세션 전(前)** 이라 CSRF 없음
  (로그인은 자격증명 자체가, 비번설정은 1회용 토큰이 보호).

---

## 6. 권한 모델 (RBAC + 스코프)

### 6-1. 역할 게이트 — `require_role` (`deps.py:49`)

```python
def require_role(*roles):
    async def checker(ctx = Depends(require_user)):
        if ctx.user.role not in roles:
            raise PermissionDeniedError("접근 권한이 없습니다")   # 403
        return ctx
    return checker

require_admin = require_role(SYSTEM_ADMIN)                 # 시스템관리자 전용
require_any   = require_role(SYSTEM_ADMIN, SERVICE_MANAGER) # 둘 다 허용(스코프 적용)
```

라우트는 시그니처에서 둘 중 하나를 고른다:
- **`require_admin`**: 서비스 등록, 계정 관리, 감사로그 등 운영 전반(문서 01·02·10).
- **`require_any`**: 대시보드·구독·요금제·결제·정산 — SERVICE_MANAGER도 보되 **자기 서비스만**.

### 6-2. 스코프 — `effective_service_ids` (`accounts.py:33`)

```python
async def effective_service_ids(db, user):
    if user.role == SYSTEM_ADMIN:
        return None                      # None = 전체 접근(제한 없음)
    ids = set()
    if user.service_id: ids.add(user.service_id)        # 주 서비스
    ids.update(UserService where user_id == user.id)    # 추가 담당
    return list(ids)                     # 이 목록의 서비스만 접근 가능
```

`require_user`가 이 값을 계산해 `ctx.service_ids`에 넣는다. **`require_any` 라우트들은 이 값으로
쿼리를 제한**한다(예: `WHERE service_id IN ctx.service_ids`). 패턴:
```python
if ctx.service_ids is not None:           # None이면 전체(시스템관리자), 제한 안 함
    query = query.where(X.service_id.in_(ctx.service_ids))
```
이 한 줄이 SERVICE_MANAGER가 남의 서비스 데이터를 못 보게 막는 **모든 목록/집계의 공통 장치**다.

---

## 7. 계정 관리 기능 (`/admin/users`, 전부 SYSTEM_ADMIN 전용)

| URL | 동작 | 핵심 규칙 |
|---|---|---|
| `GET /users` | 목록 | DELETED 제외, q(이메일)·역할·상태 필터, htmx 부분 렌더 |
| `GET /users/{id}` | 상세 | 담당 서비스 + 추가 가능한 서비스 표시 |
| `POST /users/{id}/edit` | 이메일/전화 수정 | `update_account` — 이메일 변경 시 **대표인 서비스의 manager_email 동기화**(문서 01) |
| `POST /users/{id}/disable` | 비활성/복구 | `set_account_disabled` — 비활성 시 **세션 전부 파기**. 본인은 비활성 불가 |
| `POST /users/{id}/delete` | 소프트 삭제 | `delete_account` — status=DELETED, 담당 해제, 세션 파기. **대표 담당자면 차단**(문서 01) |
| `POST /users/{id}/reset-password` | 재설정 메일 | `issue_password_reset` — 토큰 발급+메일, **즉시 기존 세션 파기**(탈취 대응) |
| `POST /users/{id}/services[...]` | 담당 추가/해제 | `assign/unassign_service` (문서 01과 동일 함수) |

공통: 생성/재설정처럼 **메일이 결과를 좌우하는 동작**은 `email_flash_qs(sent, ...)`로
성공/실패 토스트를 만들어 리다이렉트에 싣는다(`app/admin/flash.py`).

---

## 8. 예외 · 보안 포인트 정리

| 상황 | 처리 | 위치 |
|---|---|---|
| 없는 이메일 로그인 | 더미 해시 검증(타이밍 균등) + 동일 실패 메시지 | `login` ① |
| 비번 5회 실패 | 15분 `LOCKED`, 시간 지나면 자동 해제 | `login` ② ④ |
| 삭제/비활성/미설정 계정 로그인 | 각각 적절한(또는 동일한) 메시지로 거부 | `login` ③ |
| 세션 만료 | Redis TTL로 자동 삭제 → `AdminAuthRequired` → 로그인 | `get_session`/`require_user` |
| 유휴 타임아웃 | 요청마다 TTL 갱신, 무활동 시 만료 | `get_session` |
| 비번 변경/계정 비활성·삭제·재설정 | 해당 유저 **전 세션 즉시 파기** | `destroy_user_sessions` |
| CSRF 토큰 불일치 | 403 | `validate_csrf` |
| 토큰 재사용/만료 | `used_at`/`expires_at` 검증으로 거부, 1회용 | `setup_password` |
| 세션 유효 중 계정 비활성화 | 다음 요청에서 DB 상태 재확인 → 차단 | `require_user` |
| 손상된 password_hash | `verify_password`가 False 반환(500 방지) | `security.verify_password` |

**비밀번호 저장**: argon2(`_ph.hash`)로만. 평문/가역 암호화 안 함. 검증은 `verify_password`.

---

## 9. 관련 테스트

- `tests/e2e/test_admin_flows.py` — 로그인 성공/실패, 세션 쿠키 플래그, 미인증 리다이렉트,
  htmx 리다이렉트, 로그아웃 세션 파기, CSRF 없는 로그아웃 거부, 비번설정 플로우/불일치,
  재설정 시 기존 세션 파기.
- `tests/e2e/test_accounts_admin.py` — 계정 생성/수정/비활성/삭제 화면 흐름.
- `tests/integration/test_accounts.py` — `create_account`(역할/서비스/중복/0개 허용),
  `update_account`(대표 이메일 동기화), 비활성·삭제 규칙, 대표 담당자 삭제 차단.
- `tests/integration/test_auth_service.py` — `login`/`setup_password`/잠금/세션 로직.
- `tests/e2e/test_email_flash.py` — 메일 성공/실패 flash 토스트.

---

## 10. 유지보수 체크리스트

1. **새 역할 추가** (예: 읽기전용 뷰어):
   - `UserRole`에 값 추가 → `require_role` 조합으로 새 게이트 정의 →
     `effective_service_ids`에 스코프 규칙 추가 → 영향 라우트 검토.
2. **세션 TTL/잠금 정책 변경**: `auth.py`의 `MAX_FAILED_LOGINS`/`LOCK_DURATION`,
   `settings.session_ttl_seconds`. 정책은 서비스 계층에만 두고 라우트는 건드리지 말 것.
3. **세션에 필드 추가**: `_create_session`의 `hset` mapping + `AdminContext` + `require_user`에서
   읽어 채우기. (세션은 Redis Hash라 자유롭게 키 추가 가능.)
4. **보안 이벤트로 세션 파기가 필요한 새 동작**: `destroy_user_sessions(redis, user_id)` 호출.
   (단, **DB commit 성공 후** 호출 — 순서 주의.)
5. **CSRF 빠뜨리지 말 것**: 새 POST 라우트는 첫 줄에 `await validate_csrf(request, ctx)`.
   로그인/비번설정처럼 세션 전 동작만 예외(다른 인증 수단 보유 시).
6. **스코프 적용 잊지 말 것**: `require_any` 라우트에서 목록/집계 쿼리에
   `if ctx.service_ids is not None: ... .in_(ctx.service_ids)` 패턴 필수.
