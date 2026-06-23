# 03. 인증과 보안 공통

> **쉽게 말하면**: 모든 요청은 비즈니스 로직에 닿기 전에 **검문소**를 통과해야 합니다. 외부 API는 신분증 3종(**API 키 + 허용 IP + HMAC 서명**)을 동시에 보여줘야 하고, 위조·재사용을 막기 위해 **타임스탬프와 1회용 nonce**까지 확인합니다. 은행 출입에 사원증·지문·일회용 OTP를 모두 요구하는 것과 비슷합니다.

> **모든 기능이 의존하는 공통 레이어.**  
> 외부 API 호출자와 어드민 콘솔 사용자 모두 이 파일에서 설명하는 인증을 통과해야
> 비즈니스 로직에 도달한다. 코드를 읽기 전에 이 문서를 먼저 보면 나머지 기능 문서가
> 훨씬 쉽게 이해된다.

---

## 1. 외부 API 인증 — HMAC 3중 인증

외부 서비스(사내 쇼핑몰, 진료 앱 등)가 `/api/v1/...` 경로로 요청을 보낼 때 거치는
인증이다. 단순 API 키 하나만 쓰는 것이 아니라 **API 키 + IP 화이트리스트 + HMAC 서명**
세 가지를 동시에 검사한다.

### 1-1. 어디서 실행되나

모든 외부 API 엔드포인트의 `Depends` 파라미터에 `authenticate_service`가 붙어 있다.

```
app/api/deps.py:77  — authenticate_service()
```

FastAPI가 요청을 받으면 라우트 함수보다 먼저 이 함수를 자동으로 실행한다.

### 1-2. 필수 요청 헤더 4개

클라이언트는 반드시 아래 헤더를 모두 보내야 한다. 하나라도 빠지면 즉시 401이 반환된다.
(`app/api/deps.py:87-92`)

| 헤더 이름 | 값 예시 | 설명 |
|---|---|---|
| `x-service-key` | `svc_abc123...` | 서비스 등록 시 발급받은 API 키 원문 |
| `x-timestamp` | `1717123456` | 요청 시각 (Unix 초, 정수) |
| `x-nonce` | `a1b2c3d4...` | 요청마다 다른 랜덤 문자열 (UUID hex 추천) |
| `x-signature` | `fa3c7d...` | HMAC-SHA256 서명 (아래에서 계산 방법 설명) |

### 1-3. 검증 6단계 상세

`app/api/deps.py:77-138` 의 `authenticate_service` 함수가 순서대로 아래를 검사한다.
하나라도 실패하면 뒤를 보지 않고 즉시 오류를 반환한다.

#### 0단계: 킬스위치 확인 (app/api/deps.py:86)

가장 먼저 `ensure_server_enabled(db)` 를 호출한다. 서버가 비활성화 상태면 API 키조차
읽지 않고 503을 반환한다 (상세 내용은 2절 참고).

#### 1단계: API 키 해시 대조 (app/api/deps.py:95-98)

```python
# app/api/deps.py:95-98
service = await db.scalar(select(Service).where(
    Service.api_key_hash == sha256_hex(api_key)))
if service is None or service.status != ServiceStatus.ACTIVE:
    raise AuthenticationError(AUTH_FAILED)
```

DB에는 API 키 원문이 없고 SHA-256 해시(`Service.api_key_hash`)만 저장되어 있다.
클라이언트가 보낸 키를 똑같이 해시해서 비교한다. 해시가 일치하는 서비스가 없거나
`INACTIVE` 상태이면 401.

해시 함수: `app/core/security.py:45-50`
```python
def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
```

#### 2단계: IP 화이트리스트 (app/api/deps.py:101-103)

```python
ip = get_client_ip(request, settings)
# allowed_ips가 비어 있으면 IP 제한 없음(모든 IP 허용 — HMAC로만 보호).
if service.allowed_ips and ip not in service.allowed_ips and not is_loopback_ip(ip):
    raise PermissionDeniedError("허용되지 않은 IP입니다")
```

`Service.allowed_ips`(JSONB 배열)가 **비어 있으면 IP 제한이 없고(모든 IP 허용)**, 목록이 있으면 현재 요청 IP가 없을 때 403.  
`get_client_ip`는 `settings.trust_proxy=True` 일 때 `X-Forwarded-For` 헤더에서
**오른쪽에서 `trust_proxy_hops`번째** 값을 사용한다(감사 Phase 1 — 보안 M-5 강화).
프록시가 없는 직접 연결이거나 XFF 항목 수가 hop 수보다 적으면(위조 의심)
헤더를 무시하고 TCP 연결 IP를 사용한다. (`app/api/deps.py:get_client_ip`)

> **왜 '오른쪽에서 n번째'인가:** 신뢰 프록시는 자신이 본 피어 IP를 XFF의 오른쪽에
> append한다. 따라서 오른쪽 n개까지가 프록시 체인이 기록한 신뢰 가능한 값이고,
> 왼쪽 항목들은 클라이언트가 임의로 위조할 수 있다. 과거처럼 맨 왼쪽(첫 번째)을
> 신뢰하면 공격자가 `X-Forwarded-For: <화이트리스트IP>` 헤더 하나로 IP 검사를
> 우회할 수 있었다. `TRUST_PROXY_HOPS`(기본 1)는 클라이언트와 앱 사이의 신뢰
> 프록시 단 수다 — 예) 클라이언트→nginx→앱이면 1, 클라이언트→LB→nginx→앱이면 2.
> 단위 테스트: `tests/unit/test_client_ip.py`

#### 3단계: 분당 요청 수 제한 Rate Limit (app/api/deps.py:105-112)

서명 검사 전에 카운트를 올려 잘못된 요청도 throttle 한다(DoS 방어).

```python
window = int(time.time() // 60)   # 현재 분(minute) 윈도우
rl_key = f"rl:{service.id}:{window}"
count = await redis.incr(rl_key)
if count == 1:
    await redis.expire(rl_key, 90)  # 만료 90초(1분+여유)
if count > settings.rate_limit_per_minute:   # 기본 120/분
    raise RateLimitedError("요청 한도를 초과했습니다")
```

`settings.rate_limit_per_minute` (기본 120) 초과 시 429.
(`app/core/config.py:47`)

#### 4단계: 타임스탬프 오차 검사 (app/api/deps.py:115-120)

```python
if abs(time.time() - ts) > settings.hmac_timestamp_tolerance_seconds:
    raise AuthenticationError(AUTH_FAILED)
```

현재 서버 시각과 헤더의 `x-timestamp` 차이가 `±300초`(기본) 이상이면 거부.
과거에 캡처한 요청을 그대로 재전송하는 **재전송(replay) 공격** 1차 방어선이다.
(`app/core/config.py:45`)

#### 5단계: HMAC 서명 검증 (app/api/deps.py:123-131)

```python
body = await request.body()
secret = cipher.decrypt(service.hmac_secret_encrypted)  # AES 복호화
expected = sign_request(secret, request.method, request.url.path,
                        timestamp, nonce, body)
if not constant_time_equals(expected, signature):
    raise AuthenticationError(AUTH_FAILED)
```

서버 측에서 서명을 직접 계산해 클라이언트가 보낸 `x-signature`와 비교한다.  
`constant_time_equals`(`app/core/security.py:53-59`)는 `hmac.compare_digest`를 써서
**타이밍 공격**(응답 시간 차로 서명 값을 추측)을 방지한다.

> **nonce 소비 순서:** 서명 검증 후에 nonce를 Redis에 기록한다. 순서를 바꾸면(nonce 먼저)
> 서명이 잘못된 요청이 Redis nonce 키를 무한정 쌓아 메모리 DoS를 유발할 수 있다.
> (`app/api/deps.py:123-125` 주석 참고)

#### 6단계: nonce 1회용 검사 (app/api/deps.py:133-136)

```python
nonce_key = f"nonce:{service.id}:{nonce}"
if not await redis.set(nonce_key, "1", nx=True, ex=600):
    raise AuthenticationError(AUTH_FAILED)
```

`nx=True` → 키가 이미 있으면 실패(set 반환값 None).  
TTL 600초(10분) — 이 시간 안에 같은 nonce를 재사용하면 거부.  
타임스탬프 오차(±300초)와 함께 재전송 공격 **2중 방어**를 구성한다.

### 1-4. 서명(signature) 계산 방법 — 클라이언트 구현 가이드

`app/core/security.py:62-75` 의 `sign_request` 를 클라이언트 언어로 그대로 구현하면 된다.
샘플 서비스(`sample_service/shop/payment_client.py:19-23`)가 Python 구현의 완전한 예시다.

**정준 문자열(canonical string) 구성:**

```
{METHOD 대문자}\n
{path}\n
{timestamp}\n
{nonce}\n
{sha256_hex(body)}
```

예시 — `POST /api/v1/subscriptions` 에 JSON 본문을 보낼 때:

```
POST
/api/v1/subscriptions
1717123456
a1b2c3d4e5f6
e3b0c44298fc...  ← sha256(본문 bytes)
```

5줄을 개행(`\n`)으로 이어붙인 뒤, `HMAC-SHA256(hmac_secret, 위 문자열)` 을 hex 문자열로
인코딩한 것이 `x-signature` 값이 된다.

> **주의:** `method`, `path`, `timestamp`, `nonce` 중 어느 것에도 개행(`\n`, `\r`)이
> 들어오면 `sign_request`가 `ValueError`를 발생시킨다. 필드 간 바이트 이동 공격을
> 막기 위한 검증이다. (`app/core/security.py:69-72`)

**Python 구현 전체 (sample_service/shop/payment_client.py:19-56):**

```python
import hashlib, hmac, json, time, uuid, requests
from django.conf import settings as django_settings

def sign_request(secret, method, path, timestamp, nonce, body):
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

def _request(method, path, json_body=None, creds=None):
    api_key, hmac_secret = creds if creds else (
        django_settings.SERVICE_API_KEY, django_settings.SERVICE_HMAC_SECRET)
    body = json.dumps(json_body).encode() if json_body else b""
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    headers = {
        "x-service-key": api_key,
        "x-timestamp": timestamp,
        "x-nonce": nonce,
        "x-signature": sign_request(hmac_secret, method, path, timestamp, nonce, body),
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    resp = requests.request(method, django_settings.PAYMENT_API_BASE + path,
                            headers=headers, data=body or None, timeout=30)
    if resp.status_code >= 400:
        err = resp.json()["error"]
        raise Exception(f"{err['code']}: {err['message']}")
    return resp.json()
```

**서비스 키와 시크릿 어디서 얻나?**  
어드민 콘솔 `/admin/services/{id}` 화면에서 발급된다.  
- `x-service-key`: 화면에서 복사 가능한 평문 API 키 (1회 표시, 이후 AES 암호화 저장)  
- HMAC secret: 서비스 등록 시 서버가 생성한 48바이트 랜덤 값, AES 암호화 후 DB 저장
  (`app/core/security.py:28-34`), 복호화된 값을 어드민 화면에서 받는다.

### 1-5. payment_rate_limit — 결제 경로 추가 제한

결제 관련 엔드포인트(`POST /subscriptions`, `POST .../pay`, `POST .../change-card` 등)는
`authenticate_service` 위에 `payment_rate_limit`이 추가로 붙는다.
(`app/api/deps.py:141-155`)

```python
# app/api/v1/subscriptions.py:44
service: Service = Depends(payment_rate_limit)  # 결제 경로
service: Service = Depends(authenticate_service) # 읽기 경로
```

```python
# app/api/deps.py:141-155
async def payment_rate_limit(...) -> Service:
    window = int(time.time() // 60)
    key = f"rlp:{service.id}:{window}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 90)
    if count > settings.rate_limit_payment_per_minute:  # 기본 20/분
        raise RateLimitedError("결제 요청 한도를 초과했습니다")
    return service
```

일반 API 한도(120/분)와 별개로 결제 API는 20/분으로 훨씬 낮다.  
무차별 카드 시도나 빌링키 남용을 방지하기 위한 설계다. (`app/core/config.py:49`)

---

## 2. 킬스위치 — ensure_server_enabled

### 2-1. 어떤 기능인가

어드민 콘솔의 **전체 설정 > 결제서버 킬스위치**에서 서버를 즉시 비활성화할 수 있다.
활성화되어 있으면 모든 외부 API 요청이 503으로 차단된다.
어드민 콘솔(`/admin/...`)은 영향받지 않는다.

### 2-2. 코드 흐름

1. `authenticate_service` 가장 첫 줄(`app/api/deps.py:86`)에서 호출된다.

   ```python
   await ensure_server_enabled(db)
   ```

2. `ensure_server_enabled` 구현(`app/services/app_settings.py:93-102`):

   ```python
   async def ensure_server_enabled(db: AsyncSession) -> None:
       gs = await get_global_settings(db)
       if gs.server_disabled:
           raise ServerDisabledError(gs.disabled_reason or "서비스 점검 중입니다")
   ```

3. `GlobalSettings.server_disabled` (`app/models/global_settings.py:26`):

   ```python
   server_disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
   disabled_reason: Mapped[str | None] = ...  # 비활성화 사유, 클라이언트에게 반환
   ```

4. `ServerDisabledError`(`app/core/errors.py:100-109`):
   - `code = "SERVER_DISABLED"`, `http_status = 503`

5. 전역 예외 핸들러(`app/api/errors.py:33-36`)가 받아 JSON으로 변환:
   ```json
   {"error": {"code": "SERVER_DISABLED", "message": "서비스 점검 중입니다"}}
   ```

### 2-3. 킬스위치 전환 방법 (어드민 콘솔)

`app/admin/routes/settings.py:113-146`  
어드민 전체설정 페이지 `POST /admin/settings/server-toggle` 에서 처리된다.

- 비활성화 시: **사유(reason) 입력 필수 + 본인 비밀번호 재확인** 필요
  (`app/services/app_settings.py:105-134`)
- SYSTEM_ADMIN 역할만 접근 가능 (`Depends(require_admin)`)
- 전환 시 감사 로그(`server.disabled` / `server.enabled`) 자동 기록

---

## 3. 어드민 인증 — 세션/CSRF

어드민 콘솔(`/admin/...`)은 외부 API와 완전히 별개의 인증 체계를 사용한다.
세션 쿠키 기반이며, 상태 변경 요청에는 CSRF 토큰 검증이 추가된다.

### 3-1. 세션 쿠키

쿠키 이름: `admin_session` (`app/admin/deps.py:31`)

속성:
- `httponly=True` — JavaScript에서 접근 불가 (XSS 방어)
- `samesite="lax"` — 외부 사이트에서 오는 요청에 쿠키 미전송
- `secure=True` — HTTPS에서만 전송 (운영 환경)

쿠키 값은 랜덤 32바이트 `session_id`이며, 실제 세션 데이터는 모두 Redis에 저장된다.
쿠키만으로는 사용자 정보를 알 수 없다.

### 3-2. Redis 세션 구조

세션 생성: `app/services/auth.py:55-82`

Redis에서 키 `session:{session_id}` 에 Hash로 저장된다:

```
session:{session_id}
  user_id     →  UUID 문자열
  role        →  SYSTEM_ADMIN 또는 SERVICE_MANAGER
  service_id  →  주 서비스 UUID (없으면 빈 문자열)
  csrf_token  →  랜덤 32바이트 CSRF 토큰
  created_at  →  생성 시각(unix epoch 초) — 절대 수명 판정용
```

TTL: `settings.session_ttl_seconds` (기본 1800초 = 30분)  
매 요청마다 TTL을 갱신해 활성 사용자의 세션이 중간에 끊기지 않도록 한다.

**절대 수명(감사 Phase 2 — 보안 L-5)**: 유휴 TTL은 활동 시마다 연장되므로 탈취된
세션이 계속 쓰이면 영구 유효해진다. `get_session`이 생성 시각(created_at) 기준
`settings.session_absolute_ttl_seconds`(기본 43200초 = 12시간)를 초과한 세션을
활동과 무관하게 파기한다. created_at이 없는 구버전 세션도 안전 측으로 파기된다.

추가로 `user_sessions:{user_id}` Set에 session_id를 모아두어 비밀번호 변경·계정 비활성화 시
해당 사용자의 모든 세션을 한 번에 파기할 수 있다. (`app/services/auth.py:170-176`)

### 3-3. 로그인 흐름과 잠금 정책

`app/services/auth.py:85-143`

```
클라이언트 → POST /admin/login
          → [IP rate limit] 분당 10회 초과 시 인증 로직 진입 전 차단
            (감사 Phase 2 — 보안 M-2; app/admin/routes/auth.py의 _login_rate_limited.
             존재하지 않는 이메일 무차별 시도와 감사 로그 팽창 DoS를 함께 막는다)
          → auth_service.login(db, redis, settings, email=, password=, ip=)
            1. DB에서 User 조회 (email)
            2. 없으면 verify_password(_DUMMY_HASH) 실행 후 401  ← 타이밍 균등화
            3. LOCKED 상태:
               - locked_until < now → 자동 ACTIVE 복구 후 정상 진행
               - locked_until >= now → 즉시 거부
            4. DELETED → 없는 것처럼 처리 (계정 열거 방지)
            5. DISABLED → "비활성화된 계정" 메시지
            6. PENDING → "비밀번호 설정 필요" 메시지
            7. verify_password(password, user.password_hash)
               - 실패: failed_login_count += 1
               - 5회 이상 실패: status=LOCKED, locked_until=now+15분
            8. 성공: 감사 로그 → DB commit → Redis 세션 생성
```

주요 상수 (`app/services/auth.py:43-44`):
- `MAX_FAILED_LOGINS = 5` — 이 횟수까지 실패하면 잠금
- `LOCK_DURATION = timedelta(minutes=15)` — 잠금 지속 시간

**_DUMMY_HASH:** 이메일이 존재하지 않는 경우에도 `verify_password`를 한 번 실행해 응답 시간을
똑같이 만든다. 응답 시간 차이로 이메일이 등록되어 있는지 알아내는 타이밍 공격을 방지한다.
(`app/services/auth.py:52`)

### 3-4. require_user — 세션 검증 Depends

`app/admin/deps.py:60-83`

어드민 라우트에서 `Depends(require_user)` 를 선언하면 모든 요청에서 자동으로 아래를 검사한다:

1. `admin_session` 쿠키 읽기
2. `auth_service.get_session(redis, ...)` 호출 — Redis에서 세션 데이터 조회
3. 세션이 없거나 만료: `AdminAuthRequired` 예외 → 로그인 페이지로 리다이렉트
4. DB에서 User 조회 — `user.status != ACTIVE`이면 리다이렉트
5. `effective_service_ids(db, user)` 호출 — 담당 서비스 ID 목록 계산
6. **어드민 접속 IP 제한 검사** (`app/admin/deps.py:79-81`):
   ```python
   gs = await get_global_settings(db)
   if gs.admin_allowed_ips and get_client_ip(request, settings) not in gs.admin_allowed_ips:
       raise PermissionDeniedError("허용되지 않은 IP입니다")
   ```
   `GlobalSettings.admin_allowed_ips` 가 비어 있으면 제한 없음.
   목록이 있으면 현재 접속 IP가 포함되어야 한다.

7. `AdminContext(user, session_id, csrf_token, service_ids)` 반환

### 3-5. 역할 검사 — require_role / require_admin / require_any

`app/admin/deps.py:86-102`

```python
def require_role(*roles: str):
    async def checker(ctx: AdminContext = Depends(require_user)) -> AdminContext:
        if ctx.user.role not in roles:
            raise PermissionDeniedError("접근 권한이 없습니다")
        return ctx
    return checker

require_admin = require_role(UserRole.SYSTEM_ADMIN)
require_any   = require_role(UserRole.SYSTEM_ADMIN, UserRole.SERVICE_MANAGER)
```

라우트 함수의 Depends에서 이렇게 사용한다:
```python
# SYSTEM_ADMIN만 접근 가능
ctx: AdminContext = Depends(require_admin)

# 두 역할 모두 접근 가능
ctx: AdminContext = Depends(require_any)
```

### 3-6. CSRF 검증

`app/admin/deps.py:105-110`

모든 어드민 POST 핸들러는 `await validate_csrf(request, ctx)` 를 반드시 호출해야 한다.

```python
async def validate_csrf(request: Request, ctx: AdminContext) -> None:
    form = await request.form()
    token = str(form.get("csrf_token", "")) or request.headers.get("x-csrf-token", "")
    if not token or not constant_time_equals(token, ctx.csrf_token):
        raise PermissionDeniedError("CSRF 토큰이 유효하지 않습니다")
```

- 폼(HTML form): `<input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">`
- htmx AJAX: `X-CSRF-Token: {토큰}` 헤더

CSRF 토큰은 세션 생성 시 랜덤으로 만들어져 Redis에 저장된다.
(`app/services/auth.py:76` — `"csrf_token": secrets.token_urlsafe(32)`)

`constant_time_equals` 로 비교해 타이밍 공격을 막는다.

### 3-7. htmx 요청의 인증 실패 처리

`app/admin/deps.py:118-130`

`AdminAuthRequired` 예외 발생 시:
- 일반 요청 → `303 See Other` `/admin/login` 으로 리다이렉트
- htmx 요청(`HX-Request` 헤더 존재) → `204 No Content` + `HX-Redirect: /admin/login` 헤더  
  htmx가 이 헤더를 받아 클라이언트 측에서 전체 페이지 이동을 처리한다.

---

## 4. 권한 스코프 — SYSTEM_ADMIN vs SERVICE_MANAGER

두 역할의 핵심 차이는 **어떤 서비스 데이터에 접근할 수 있는가**다.

### 4-1. 역할 정의

`app/models/enums.py:18-21`

| 역할 | 의미 | service_ids |
|---|---|---|
| `SYSTEM_ADMIN` | 전체 관리자 — 모든 서비스 접근 | `None` (전체) |
| `SERVICE_MANAGER` | 서비스 담당자 — 담당 서비스만 접근 | `[uuid, ...]` (목록) |

### 4-2. effective_service_ids

`app/services/accounts.py:33-49`

```python
async def effective_service_ids(db: AsyncSession, user: User) -> list[uuid.UUID] | None:
    if user.role == UserRole.SYSTEM_ADMIN:
        return None  # None = 전체 접근
    ids: set[uuid.UUID] = set()
    if user.service_id is not None:
        ids.add(user.service_id)      # 주 서비스 (User.service_id)
    extra = (await db.scalars(select(UserService.service_id).where(
        UserService.user_id == user.id))).all()
    ids.update(extra)                 # + UserService 다대다 추가 서비스
    return list(ids)
```

SERVICE_MANAGER의 유효 스코프 = `User.service_id` (주 서비스) + `user_services` 테이블의
추가 서비스를 합집합으로 계산한다.

**왜 세션에 캐시하지 않나?**  
권한 변경이 즉시 반영되어야 하기 때문에 매 요청마다 DB를 조회한다.
(`app/services/auth.py:63-65` 주석 참고)

### 4-3. service_scope 헬퍼

`app/admin/deps.py:113-115`

```python
def service_scope(ctx: AdminContext) -> list[uuid.UUID] | None:
    """담당 서비스 ID 목록. SYSTEM_ADMIN이면 None(전체)."""
    return ctx.service_ids
```

라우트 함수에서 조회 쿼리에 `.where(Service.id.in_(service_ids))` 를 붙일 때 사용한다.
`None`이면 조건을 붙이지 않아 전체 조회가 된다.

### 4-4. 타 서비스 접근 시 404 반환 이유

SERVICE_MANAGER가 담당하지 않는 서비스의 데이터를 요청했을 때 403(권한 없음) 대신
**404(없음)**을 반환하는 라우트가 있다.

이유: 403을 반환하면 "해당 리소스가 존재한다"는 사실이 노출된다 (존재 열거 공격).
404를 반환하면 존재 여부 자체를 숨길 수 있다.

예시 패턴:
```python
# 서비스를 조회했는데 스코프 밖이면 404 처리
service = await db.get(Service, service_id)
if service is None or (scope is not None and service.id not in scope):
    raise NotFoundError("서비스를 찾을 수 없습니다")
```

---

## 5. 암호화와 시크릿 관리

### 5-1. AES-256-GCM 암호화

`app/core/crypto.py`

빌링키(토스페이먼츠 자동결제용)와 서비스별 HMAC 시크릿은 DB에 평문으로 저장하지 않는다.
`AesGcmCipher`가 AES-256-GCM으로 암호화한다.

```python
# app/core/crypto.py:32-50
def encrypt(self, plaintext: str) -> str:
    nonce = os.urandom(12)  # 매번 새 12바이트 nonce
    ct = self._aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()
    # 반환: base64(nonce[12] + ciphertext + GCM_tag[16])

def decrypt(self, token: str) -> str:
    raw = base64.b64decode(token)
    return self._aesgcm.decrypt(raw[:12], raw[12:], None).decode()
    # 앞 12바이트=nonce, 나머지=암호문+인증태그
```

GCM 모드는 암호화와 함께 **무결성 인증 태그**를 생성한다. 복호화 시 태그가 맞지 않으면
자동으로 예외가 발생해 변조된 데이터를 탐지한다 (AEAD).

암호화 키: `settings.encryption_key` — base64 인코딩된 32바이트, `.env`에만 보관.
앱 시작 시 `AesGcmCipher(settings.encryption_key)` 로 초기화되며 키 길이가 32바이트가
아니면 즉시 `ValueError`로 실패한다. (`app/core/crypto.py:26-30`, `app/main.py:48`)

어디에 저장되나:
- `Service.hmac_secret_encrypted` (`app/models/service.py:26`) — HMAC 시크릿
- `Service.api_key_encrypted` (`app/models/service.py:28`) — 표시용 평문 키(AES 암호화)
- `Service.toss_secret_key_encrypted` (`app/models/service.py:39`) — 서비스별 토스 시크릿(AES 암호화, nullable)
- 구독 빌링키 — `Subscription` 모델에 암호화 저장

> **서비스별 토스 시크릿**: 2026-06-23부터 전역 `TOSS_SECRET_KEY` 환경변수가 제거되고,
> 각 서비스마다 독립 토스 시크릿 키를 `toss_secret_key_encrypted`에 암호화 저장한다.
> 어드민 콘솔 → 서비스 상세 → Toss 시크릿 키 카드에서 등록. 평문은 화면·로그에 절대 표시하지 않는다.
> 키가 미등록된 서비스에서 결제를 시도하면 `TossKeyNotConfiguredError` (HTTP 422, 코드 `TOSS_KEY_NOT_CONFIGURED`)가 발생한다.
> → `app/toss/provider.py`, `app/core/errors.py`

### 5-2. API 키 — SHA-256 해시 비교

`app/models/service.py:25`:
```python
api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
# SHA-256 해시(인증 검증용, 평문 저장 안 함)
```

DB에는 SHA-256 해시만 저장한다. API 키 원문이 DB에서 유출되어도 해시에서 원문을 역산할 수 없다.  
키를 비교할 때 매번 해시해서 대조한다 (`app/api/deps.py:96`).

### 5-3. API 키 1회 표시 정책

서비스 등록 또는 키 재발급 시 평문 키를 **딱 1회** 화면에 표시하고, 이후에는 AES 암호화
상태로만 저장한다. 관리자가 나중에 어드민 화면에서 다시 볼 수 있도록 AES로 보관하지만,
인증 자체는 항상 해시 비교로만 한다.

### 5-4. 비밀번호 — Argon2id

`app/core/security.py:78-96`

```python
_ph = PasswordHasher()  # 기본: time_cost=3, memory_cost=65536

def hash_password(password: str) -> str:
    return _ph.hash(password)      # 솔트 자동 생성

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False  # 손상된 해시도 False (500 방지)
```

Argon2id: 사이드채널 공격 + GPU 병렬 공격에 강한 최신 KDF (OWASP 권장).  
솔트는 라이브러리가 자동 생성한다.

최소 길이: 10자 (`app/services/auth.py:48` MIN_PASSWORD_LENGTH, `app/services/auth.py:179-182`)

### 5-5. Swagger 문서 보호 — HTTP Basic

API 문서(`/docs`, `/openapi.json`)는 `SWAGGER_ID`/`SWAGGER_PW`(환경별 `.env`) 기반 HTTP Basic 인증으로 보호한다. `app/main.py`의 `_register_protected_docs()`가 두 값이 모두 설정된 경우에만 라우트를 등록하고, `secrets.compare_digest`로 타이밍 안전 비교한다. 하나라도 비면 라우트가 없어 404 — 운영에서 자격증명을 비워 두면 문서가 자연히 비공개된다.

```python
def verify(credentials=Depends(_swagger_security)):
    id_ok = credentials and secrets.compare_digest(credentials.username, swagger_id)
    pw_ok = credentials and secrets.compare_digest(credentials.password, swagger_pw)
    if not (id_ok and pw_ok):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
```

---

## 6. 초보자 디버깅 가이드

### 6-1. 외부 API에서 401 났을 때

| 증상 | 원인 | 확인 포인트 |
|---|---|---|
| `UNAUTHORIZED` 401 | API 키 불일치 | `x-service-key` 값과 DB의 `sha256_hex(api_key)` == `Service.api_key_hash` 확인 |
| `UNAUTHORIZED` 401 | 타임스탬프 오차 | 서버와 클라이언트 시각 동기화 확인 (NTP). `abs(server_time - x-timestamp) <= 300` |
| `UNAUTHORIZED` 401 | nonce 재사용 | 매 요청마다 새 UUID를 생성했는지 확인. 요청 재시도 시에도 새 nonce 사용 필수 |
| `UNAUTHORIZED` 401 | 서명 불일치 | 정준 문자열 5줄 순서 확인 (METHOD\npath\ntimestamp\nnonce\nsha256(body)). 본문 bytes가 동일한지 확인 |
| `FORBIDDEN` 403 | IP 화이트리스트 | `Service.allowed_ips` 에 요청 IP가 등록되어 있는지 확인. 리버스 프록시 환경이면 `trust_proxy` 설정 확인 |
| `SERVER_DISABLED` 503 | 킬스위치 | 어드민 전체설정에서 서버 활성화 여부 확인 |
| `RATE_LIMITED` 429 | 한도 초과 | 분당 요청 수 줄이기. 결제 API는 20/분, 일반은 120/분 |

**서명 디버깅 순서:**
1. method 대문자인지 확인 (`GET`, `POST` 등)
2. path 앞에 `/` 있는지, 쿼리 파라미터 없는지 확인 (`/api/v1/subscriptions`)
3. 본문 bytes를 그대로 sha256 했는지 확인 (Content-Type: application/json 이면 `json.dumps(...).encode()`)
4. timestamp가 Unix 초(정수)인지 확인
5. 계산한 서명을 서버 쪽 `sign_request`와 동일 입력으로 대조

### 6-2. 어드민 로그인에서 401/잠금 났을 때

| 증상 | 원인 |
|---|---|
| "이메일 또는 비밀번호가 올바르지 않습니다" | 이메일/비밀번호 불일치 |
| "계정이 잠겼습니다" | 5회 실패로 잠김 → 15분 후 자동 해제. 또는 어드민에서 복구 |
| "비활성화된 계정입니다" | SYSTEM_ADMIN이 계정을 비활성화함 |
| "비밀번호 설정이 필요합니다" | 최초 가입 후 비밀번호를 아직 설정 안 함 → 이메일 링크 확인 |

### 6-3. CSRF 오류 났을 때

`PermissionDeniedError: CSRF 토큰이 유효하지 않습니다` 가 나오면:

1. 폼에 `<input type="hidden" name="csrf_token" value="...">` 가 있는지 확인
2. 세션이 만료되어 새 CSRF 토큰이 발급되었는지 확인 → 페이지 새로고침 후 재시도
3. htmx AJAX 요청이면 `X-CSRF-Token` 헤더를 보내는지 확인

### 6-4. 서비스 매니저가 다른 서비스에 접근하려 할 때

`NotFoundError` 404 가 반환된다 (403이 아님).  
`effective_service_ids`가 반환한 목록에 해당 서비스 ID가 없으면 없는 리소스로 처리된다.  
어드민 화면에서 해당 계정에 서비스를 할당하면 해결된다.

### 6-5. 인증 우회 금지 규칙

> 절대 하지 말아야 할 것들

- **`authenticate_service` 를 Depends에서 제거하는 것** — 인증 없이 외부에 API가 노출된다.
- **`validate_csrf` 호출을 생략하는 것** — 어드민 POST가 CSRF 공격에 노출된다.
- **`require_admin` 대신 `require_any` 를 쓰는 것** (SYSTEM_ADMIN 전용 기능에) — SERVICE_MANAGER가 전체 설정에 접근하게 된다.
- **nonce 없이 타임스탬프만 사용하는 것** — 5분 이내 재전송 공격이 가능해진다.
- **API 키 원문을 DB에 저장하는 것** — 해시만 저장해야 유출 시 원문 노출을 막는다.
- **`constant_time_equals` 대신 `==` 로 서명 비교하는 것** — 타이밍 공격에 취약해진다.

---

## 참고 — 관련 파일 빠른 찾기

| 역할 | 파일 |
|---|---|
| 외부 API 인증 (3중) | `app/api/deps.py` |
| HMAC 서명 계산 | `app/core/security.py` |
| 클라이언트 구현 예시 | `sample_service/shop/payment_client.py` |
| 어드민 세션/역할/CSRF | `app/admin/deps.py` |
| 로그인·세션 생성·잠금 | `app/services/auth.py` |
| 스코프 계산 (담당 서비스) | `app/services/accounts.py` |
| AES-256-GCM 암호화 | `app/core/crypto.py` |
| 킬스위치·어드민 IP | `app/services/app_settings.py` |
| GlobalSettings 모델 | `app/models/global_settings.py` |
| 도메인 예외 (에러 코드) | `app/core/errors.py` |
| 환경변수 설정 | `app/core/config.py` |

상호 참조:
- 외부 API 엔드포인트 목록과 각 라우트의 Depends 선택 이유 → **15. 외부 API 레퍼런스**
- `GlobalSettings` 변경 방법과 킬스위치 UI → **14. 전체 설정**
- 계정 생성·서비스 할당·비밀번호 재설정 상세 → **13. 어드민 계정**
