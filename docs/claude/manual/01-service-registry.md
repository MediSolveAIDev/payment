# 01. 서비스 등록 · 키 발급 · 담당자 배정

> 외부 서비스가 이 결제 서버를 쓰려면 먼저 **서비스로 등록**되어야 한다.
> 이 문서는 등록부터 키 발급, 담당자 배정, IP/상태 관리, 삭제까지의 전 과정을
> "어떤 URL을 호출하면 → 어떤 코드를 거쳐서 → 무엇을 반환하는가" 기준으로 설명한다.
>
> 선행 지식: [00-overview.md](00-overview.md)의 "두 진입 평면과 인증", "공통 처리 흐름".

---

## 0. 한눈에 보기

이 기능은 전부 **Admin 콘솔(`/admin/services...`)** 에서 일어난다. 외부 API(`/api/v1`)가
아니다. 그리고 **전부 `SYSTEM_ADMIN`(시스템 관리자) 전용**이다(`require_admin`).

| 하는 일 | HTTP | URL | 라우트 함수 | 서비스 계층 함수 |
|---|---|---|---|---|
| 서비스 목록 | GET | `/admin/services` | `services_list` | (직접 쿼리) |
| 등록 폼 열기 | GET | `/admin/services/new` | `services_new` | `_manager_options` |
| **서비스 등록** | POST | `/admin/services` | `services_create` | `registry.register_service` |
| 상세 보기 | GET | `/admin/services/{id}` | `services_detail` | `_service_managers` |
| 키 복사(평문) | GET | `/admin/services/{id}/keys-modal` | `services_keys_modal` | (cipher 직접) |
| 담당자 추가 | POST | `/admin/services/{id}/assign-manager` | `services_assign_manager` | `accounts.assign_service` |
| 대표 담당자 지정 | POST | `/admin/services/{id}/primary-manager` | `services_set_primary_manager` | `registry.set_primary_manager` |
| 담당자 해제 | POST | `/admin/services/{id}/managers/{uid}/remove` | `services_remove_manager` | `accounts.unassign_service` |
| 키 재발급 | POST | `/admin/services/{id}/rotate-keys` | `services_rotate` | `registry.rotate_keys` |
| 허용 IP 변경 | POST | `/admin/services/{id}/ips` | `services_update_ips` | `registry.update_allowed_ips` |
| 활성/비활성 | POST | `/admin/services/{id}/status` | `services_set_status` | `registry.set_service_status` |
| **취소 정책 변경** | POST | `/admin/services/{id}/cancel-policy` | `services_cancel_policy` | `registry.update_cancel_policy` |
| 삭제 | POST | `/admin/services/{id}/delete` | `services_delete` | `registry.delete_service` |

관련 파일:
- 라우트: `app/admin/routes/services.py`
- 서비스 계층: `app/services/registry.py`, `app/services/accounts.py`(담당자 추가/해제)
- 모델: `app/models/service.py`(Service), `app/models/user_service.py`(담당 다대다)
- 보조: `app/core/security.py`(키 생성/해시), `app/core/crypto.py`(암호화), `app/services/audit.py`
- 화면: `app/admin/templates/services/*.html`

---

## 1. 모든 Admin 요청이 공통으로 거치는 길

각 엔드포인트를 보기 전에, **모든 요청이 똑같이 거치는 3개의 관문**을 먼저 이해하면
이후 설명이 쉬워진다. (코드: `app/admin/deps.py`)

### (1) `require_admin` — "로그인했고, 시스템 관리자인가?"

라우트 시그니처의 `ctx: AdminContext = Depends(require_admin)`가 그것이다. FastAPI가
라우트 함수 본문을 실행하기 **전에** 이 의존성을 먼저 실행한다. 내부 동작:

```
require_admin = require_role(UserRole.SYSTEM_ADMIN)   # deps.py:57
  → require_user(...)            # 쿠키 → 세션 → 유저 복원
      1. 쿠키 admin_session 읽음
      2. auth_service.get_session(redis, ...) 로 Redis 세션 조회 → 없으면 AdminAuthRequired
      3. 세션의 user_id로 User 조회 → 상태가 ACTIVE가 아니면 AdminAuthRequired
      4. effective_service_ids(user) 로 담당 서비스 범위 계산
      5. AdminContext(user, session_id, csrf_token, service_ids) 반환
  → checker: ctx.user.role이 SYSTEM_ADMIN이 아니면 PermissionDeniedError(403)
```

- 미인증이면 `AdminAuthRequired` → 핸들러가 `/admin/login`으로 리다이렉트(htmx면 `HX-Redirect`).
- 결과물 **`AdminContext`** 가 라우트에 주입된다. 핵심 필드:
  - `ctx.user` — 현재 로그인한 관리자(User 객체). `ctx.user.id`가 감사로그의 행위자.
  - `ctx.csrf_token` — 이 세션의 CSRF 토큰(폼 검증/렌더에 사용).
  - `ctx.service_ids` — `None`이면 전체 접근(시스템관리자). 서비스 등록 기능은 어차피
    `require_admin`이라 항상 시스템관리자이므로 여기선 `None`이다.

### (2) `validate_csrf` — "이 POST가 우리 폼에서 온 게 맞나?" (POST에만)

모든 POST 라우트는 본문 첫 줄에서 `await validate_csrf(request, ctx)`를 호출한다.

```python
# deps.py:60
async def validate_csrf(request, ctx):
    form = await request.form()
    token = form.get("csrf_token") or request.headers.get("x-csrf-token", "")
    if not token or not constant_time_equals(token, ctx.csrf_token):
        raise PermissionDeniedError("CSRF 토큰이 유효하지 않습니다")
```

- 폼에는 항상 `<input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">`가 들어있다.
- 토큰이 없거나 세션 토큰과 다르면 403. **상수시간 비교**로 타이밍 공격을 막는다.
- 주의(초급자용): `request.form()`은 Starlette가 한 번 파싱하면 캐시하므로, `validate_csrf`가
  먼저 읽어도 라우트에서 다시 `await request.form()`을 호출하면 같은 값을 받는다. 중복 파싱 아님.

### (3) `render` / `render_list` — "화면(HTML)으로 응답"

- `render(request, "템플릿.html", ctx=ctx, **데이터)` — Jinja 템플릿을 렌더해 HTML 반환.
  쿼리스트링의 `?flash=...`를 토스트로 자동 전달한다.
- `render_list(...)` — 목록 전용. 요청 헤더에 `HX-Request`가 있으면(htmx 부분 요청)
  전체 페이지 대신 **표 부분(`_table.html`)만** 응답한다 → 검색/정렬/페이지 이동이 깜빡임 없이 갱신.

> 정리: **GET 라우트** = `require_admin` → 데이터 조회 → `render`.
> **POST 라우트** = `require_admin` → `validate_csrf` → 서비스 계층 호출 → 리다이렉트(303) 또는 `render`.

---

## 2. 데이터 구조 (이 기능이 만지는 테이블)

### `Service` (`app/models/service.py`)
| 컬럼 | 의미 |
|---|---|
| `id` (UUID) | 서비스 식별자 |
| `name` (unique) | 서비스명. **중복 불가** — 등록 충돌의 근거 |
| `allowed_ips` (JSONB list) | 외부 API 호출을 허용할 IPv4 목록 |
| `manager_email` | **대표 담당자 이메일** = 결제 실패·갱신 등 알림 수신처 |
| `api_key_hash` (unique) | 외부 API 인증에 쓰는 키의 SHA-256 해시. 인증은 이 값으로만 |
| `api_key_encrypted` | 키 복사 화면용 평문 키의 AES 암호문(표시 전용) |
| `hmac_secret_encrypted` | HMAC 서명 검증용 secret의 AES 암호문 |
| `status` | `ACTIVE` / `INACTIVE`(비활성 시 외부 API 거부) |
| `cancellation_enabled` | 단건(ONE_OFF) 결제 취소 허용 여부 (기본 `True`) |
| `cancellation_fee_percent` | 취소 수수료율 0~100% (기본 0). 환불액 = 금액 − (금액 × 수수료% // 100) |

핵심 보안 개념(초급자용): **키 원문은 DB에 평문으로 두지 않는다.**
- 인증용 → 해시(`api_key_hash`)만 저장. 들어온 키를 해시해서 비교(역산 불가).
- 키 복사 표시용 → AES로 **암호화**해 저장(`*_encrypted`). 복호화 키는 `ENCRYPTION_KEY`(env).

### 담당자(관리자↔서비스, 다대다)
한 명의 `SERVICE_MANAGER`가 여러 서비스를 담당할 수 있다. 두 곳에 나뉘어 표현된다:
- `User.service_id` — **주 서비스**(1개).
- `UserService(user_id, service_id)` — **추가 담당**(여러 개). (`app/models/user_service.py`)
- "유효 담당 서비스" = `User.service_id` ∪ `UserService`의 service_id들.

`Service`에는 `manager_email`(대표 1명)만 있고, "이 서비스를 담당하는 계정 목록"은
위 두 곳을 **역방향으로 조회**해서 구한다(아래 `_service_managers` 참고).

---

## 3. 서비스 등록 (가장 중요한 흐름) — 전 과정 상세

### 3-1. 등록 폼 열기 — `GET /admin/services/new`

```python
# routes/services.py:62
@router.get("/services/new")
async def services_new(request, ctx=Depends(require_admin), db=Depends(get_db)):
    return render(request, "services/new.html", ctx=ctx, error=None,
                  manager_options=await _manager_options(db))
```

- `_manager_options(db)` — 폼의 "담당자 계정" 체크박스/대표 select에 채울 후보.
  **삭제(DELETED)되지 않은 SERVICE_MANAGER 계정 전체**를 이메일순으로 조회.
- 후보가 0명이면 `new.html`이 폼 대신 "계정을 먼저 만드세요 + `/admin/users/new` 링크"를 보여준다
  (서비스 등록에는 담당자가 반드시 1명 필요하기 때문).

화면에서 입력하는 값: 서비스명(`name`), 담당자 체크박스(`manager_ids`, 복수),
대표 계정 select(`primary_user_id`), 허용 IP(옥텟 입력 → hidden `allowed_ips`).

### 3-2. 등록 처리 — `POST /admin/services`

```python
# routes/services.py:69
@router.post("/services")
async def services_create(request, ctx=Depends(require_admin),
                          db=Depends(get_db), cipher=Depends(get_cipher)):
    await validate_csrf(request, ctx)                 # ① CSRF 검증
    form = await request.form()

    async def form_error(message):                    # 에러 시 폼 다시 보여주는 헬퍼
        return render(request, "services/new.html", ctx=ctx, error=message,
                      manager_options=await _manager_options(db))

    try:                                              # ② 폼 → 타입 변환(UUID 파싱)
        manager_ids = [uuid.UUID(str(v)) for v in form.getlist("manager_ids")]
        primary_raw = str(form.get("primary_user_id", "")).strip()
        primary_id = uuid.UUID(primary_raw) if primary_raw else None
    except ValueError:
        return await form_error("유효하지 않은 담당자 계정입니다")

    try:                                              # ③ 서비스 계층 호출(핵심 로직)
        creds = await registry.register_service(
            db, cipher,
            name=str(form.get("name", "")),
            allowed_ips=_parse_ips(str(form.get("allowed_ips", ""))),
            manager_user_ids=manager_ids, primary_user_id=primary_id,
            actor_user_id=ctx.user.id)
    except DomainError as exc:                         # ④ 규칙 위반 → 폼에 에러 표시
        return await form_error(exc.message)

    return render(request, "services/keys.html", ctx=ctx, service=creds.service,  # ⑤ 키 1회 노출
                  api_key=creds.api_key, hmac_secret=creds.hmac_secret, flash=None)
```

**라우트가 하는 일은 딱 4가지뿐**이다: CSRF 검증 → 폼 파싱/타입 변환 → 서비스 계층 호출
→ 결과를 화면으로. 비즈니스 규칙은 전부 `register_service` 안에 있다.

- `_parse_ips` — 줄바꿈/콤마로 구분된 문자열을 IP 리스트로 변환(`["10.0.0.1", ...]`).
- 잘못된 UUID(②) / 도메인 규칙 위반(④)은 둘 다 **폼을 다시 렌더**해 에러 메시지를 보여준다
  (사용자가 입력값을 고칠 수 있게). 후보 목록도 다시 채워 넣는다.

### 3-3. 핵심 로직 — `registry.register_service` (`app/services/registry.py:94`)

이 함수가 "등록"의 실체다. 순서대로:

```python
async def register_service(db, cipher, *, name, allowed_ips,
                           manager_user_ids, primary_user_id,
                           cancellation_enabled: bool = True,
                           cancellation_fee_percent: int = 0,
                           actor_user_id=None):
    name = (name or "").strip()
    if not name:
        raise InputValidationError("서비스명은 필수입니다")        # 1. 이름 검증
    _validate_ips(allowed_ips)                                   # 2. IP 검증
    if not 0 <= cancellation_fee_percent <= 100:
        raise InputValidationError("취소 수수료율은 0~100 사이여야 합니다")  # 3. 수수료율 검증
    managers = await _validate_managers(db, manager_user_ids, primary_user_id)  # 4. 담당자 검증
    if await db.scalar(select(Service).where(Service.name == name)):
        raise ConflictError("이미 등록된 서비스명입니다")          # 5. 이름 중복(사전 체크)

    api_key = generate_service_api_key()       # "svc_" + 랜덤 43자   6. 키 2종 생성
    hmac_secret = generate_hmac_secret()       # 랜덤 64자
    service = Service(name=name, allowed_ips=allowed_ips,
                      manager_email=managers[0].email,            # 대표 = 목록 첫 번째
                      api_key_hash=sha256_hex(api_key),           # 인증용 해시
                      api_key_encrypted=cipher.encrypt(api_key),  # 표시용 암호문
                      hmac_secret_encrypted=cipher.encrypt(hmac_secret),
                      cancellation_enabled=cancellation_enabled,
                      cancellation_fee_percent=cancellation_fee_percent)
    db.add(service)
    try:
        await db.flush()                       # 6. INSERT 시도(아직 커밋 아님 → service.id 확보)
    except IntegrityError:
        await db.rollback()                    #    동시 등록 경쟁 → 유니크 제약이 최종 심판
        raise ConflictError("이미 등록된 서비스명입니다") from None

    for user in managers:                       # 7. 담당자 배정
        if user.service_id is None:
            user.service_id = service.id        #    주 서비스가 비었으면 주로
        else:
            db.add(UserService(user_id=user.id, service_id=service.id))  # 있으면 추가 담당

    await record_audit(db, actor_type="USER", actor_user_id=actor_user_id,  # 8. 감사로그
                       action="service.register", target_type="service",
                       target_id=str(service.id),
                       detail={"name": name, "manager_count": len(managers)})
    await db.commit()                           # 9. 여기서 한 번에 영구 저장(트랜잭션 경계)
    return IssuedCredentials(service, api_key, hmac_secret)   # 10. 평문 키를 호출자에게 반환
```

각 단계를 초급자 관점에서 풀어 설명:

1. **이름 검증** — 공백 제거 후 비면 `InputValidationError`(→ 라우트가 폼 에러로 표시).
2. **IP 검증** (`_validate_ips`) — 1개 이상 + 전부 유효한 IPv4여야 함. 아니면 검증 에러.
3. **취소 수수료율 검증** — `cancellation_fee_percent`가 0~100 범위를 벗어나면 `InputValidationError`.
4. **담당자 검증** (`_validate_managers`, 아래 3-4) — 대표 자동포함·중복제거·존재·역할 확인.
5. **이름 중복 사전 체크** — 같은 이름이 있으면 `ConflictError`(409 의미).
   *왜 사전 체크와 7번의 try가 둘 다 있나?* 사전 체크는 친절한 에러 메시지용, 7번은 동시에 같은
   이름이 들어온 **경쟁 상황**의 최종 방어선(DB 유니크 제약). 둘 다 같은 메시지를 던진다.
6. **키 생성** — API 키(`svc_...`)와 HMAC secret. 키는 해시 1개 + 암호문 1개로 나눠 저장.
   `manager_email`은 **검증된 담당자 목록의 첫 번째 = 대표**의 이메일(3-4에서 대표를 0번에 둠).
   취소 정책(`cancellation_enabled`, `cancellation_fee_percent`)도 함께 저장.
7. **flush** — INSERT를 DB로 보내 `service.id`(UUID)를 확정. 단 아직 commit 전이라
   같은 트랜잭션 내에서만 보인다. 유니크 충돌이면 `IntegrityError` → 롤백 후 409.
8. **담당자 배정** — 각 담당자에 대해: 주 서비스가 비었으면 그 서비스를 주로 설정, 이미 있으면
   `UserService`(추가 담당) 행을 만든다. (이 규칙은 `accounts.assign_service`와 동일하지만,
   커밋을 register_service가 한 번에 묶으려고 여기서 직접 처리한다.)
9. **감사로그** — `record_audit`은 행을 `db.add`만 하고 커밋은 안 한다(커밋은 호출자 몫).
10. **commit** — 7~9의 모든 변경(서비스 INSERT + 담당자 배정 + 감사로그)을 **한 트랜잭션으로**
    영구 저장. 중간에 실패하면 전부 롤백되어 "반쪽 등록"이 생기지 않는다.
11. **반환** — `IssuedCredentials(service, api_key, hmac_secret)`. **평문 키는 이 순간에만 존재**한다
    (DB엔 해시/암호문만). 그래서 라우트가 곧바로 `keys.html`로 1회 노출하는 것.

### 3-4. 담당자 검증 — `_validate_managers` (`registry.py:47`)

```python
async def _validate_managers(db, manager_user_ids, primary_user_id):
    if primary_user_id is None:
        raise InputValidationError("담당자를 1명 이상 선택해야 합니다")
    ordered = [primary_user_id]                      # 대표를 항상 맨 앞에
    for uid in manager_user_ids:
        if uid not in ordered:                       # 중복 제거(대표가 체크목록에도 있으면 1번만)
            ordered.append(uid)
    users = []
    for uid in ordered:
        user = await db.get(User, uid)
        if (user is None or user.status == UserStatus.DELETED
                or user.role != UserRole.SERVICE_MANAGER):
            raise InputValidationError("서비스 담당자 계정만 선택할 수 있습니다")
        users.append(user)
    return users
```

여기서 중요한 설계 두 가지:
- **대표 자동 포함**: 사용자가 대표 select만 고르고 체크박스를 안 눌러도, 대표가 `ordered[0]`로
  들어가므로 담당자에 자동 포함된다. 그래서 `managers[0]` = 항상 대표 → `manager_email` 결정.
- **방어적 검증**: 존재하지 않거나 / 삭제됐거나 / 역할이 SERVICE_MANAGER가 아닌 계정은 전부 거부.
  (UI가 정상 후보만 보여주지만, 직접 요청을 위조하는 경우까지 막는다.)

### 3-5. 결과 화면 — `keys.html`

`render(..., "services/keys.html", api_key=..., hmac_secret=...)`로 **발급된 키 2종을 1회만**
보여준다. 이 화면을 벗어나면 평문 키는 다시 볼 수 없고(키 복사 모달은 별도, 4-2 참고),
유출 시 재발급(4-3)만 가능하다. 담당자에게 이 키를 안전하게 전달하는 것은 운영자의 몫.

### 3-6. 등록 전체 흐름도

```
[브라우저] POST /admin/services (name, manager_ids[], primary_user_id, allowed_ips, csrf)
   │
   ▼ services_create (라우트)
   ├─ require_admin      : 세션→관리자 확인, AdminContext 주입
   ├─ validate_csrf      : 토큰 검증
   ├─ 폼 파싱            : manager_ids/primary_id를 UUID로 변환 (실패 → 폼 에러)
   ▼ registry.register_service (서비스 계층)
   ├─ 이름/IP/담당자 검증 (실패 → DomainError → 폼 에러)
   ├─ 이름 중복 체크
   ├─ 키 생성(해시+암호문), Service INSERT(flush)
   ├─ 담당자 배정(주 or UserService)
   ├─ record_audit("service.register")
   └─ commit  → IssuedCredentials(평문 키 포함)
   ▼
[브라우저] keys.html — API 키 / HMAC secret 1회 노출
```

---

## 4. 나머지 관리 기능

각각 "호출 URL → 라우트 → 서비스 계층 → 반환"만 간결히. 패턴은 등록과 동일
(POST는 CSRF 검증 후 서비스 계층 호출, 보통 **303 리다이렉트**로 상세 화면에 돌아옴).

### 4-1. 상세 보기 — `GET /admin/services/{id}` (`services_detail`)
- 서비스 + 요금제 목록(표시용 금액 계산) + 구독 수 + **담당자 목록/추가가능 계정** +
  하단 구독 페이지 + **일반결제(ONE_OFF) 표**(`_oneoff_table.html`)를 한 화면에 모아 렌더.
  각 표 상단에 **엑셀 다운로드** 버튼이 있고, 대응 엑셀 엔드포인트는 아래와 같다:
  - 구독 목록: `GET /admin/services/{id}/subs.xlsx`
  - 일반결제 목록: `GET /admin/services/{id}/oneoff.xlsx`
  - 요금제 목록: `GET /admin/services/{id}/plans.xlsx`
  (엑셀 포맷 상세는 문서 12 참고)
- 담당자 목록은 `_service_managers`가 계산:
  ```python
  primary  = User where service_id == 이 서비스        # 주 서비스로 가진 계정
  extra    = UserService where service_id == 이 서비스 → 그 user들  # 추가 담당
  managers = primary ∪ extra (중복 제거)
  assignable = 전체 SERVICE_MANAGER - managers          # 아직 담당 아닌 계정(추가 후보)
  ```
- htmx 부분 요청(`HX-Target`이 `list-svc-plans`/`list-svc-subs`/`list-svc-oneoff`)이면 해당 표 fragment만 응답.

### 4-2. 키 복사 모달 — `GET /admin/services/{id}/keys-modal` (`services_keys_modal`)
- 저장된 **암호문을 복호화**해 평문 키를 모달로 보여준다(`cipher.decrypt`).
- 복호화 실패해도 500으로 안 터지고 모달 안에 안내(`decrypt_error`)로 처리.
- 평문 노출이므로 **감사로그 `service.keys_viewed` 기록**(commit) + 응답 헤더 `Cache-Control: no-store`.

### 4-3. 키 재발급 — `POST /admin/services/{id}/rotate-keys` (`services_rotate` → `registry.rotate_keys`)
- 새 API 키/HMAC secret 생성 → 해시·암호문 교체 → 감사 `service.rotate_keys` → commit.
- **기존 키는 즉시 무효**(해시가 바뀌므로 옛 키로는 인증 실패). 새 키를 `keys.html`로 1회 노출.
- 용도: 키 유출 대응.

### 4-4. 담당자 추가 — `POST /admin/services/{id}/assign-manager` (`accounts.assign_service`)
- 폼의 `user_id`를 받아 그 계정을 이 서비스 담당으로 추가.
- 규칙(`assign_service`): 이미 담당이면 무시 / 주 서비스 비었으면 주로 / 아니면 `UserService` 추가.
  감사 `account.assign_service`. → 303 리다이렉트(에러는 `?error=`로 전달).

### 4-5. 대표 담당자 지정 — `POST /admin/services/{id}/primary-manager` (`registry.set_primary_manager`)
- 그 계정이 **이 서비스의 담당자인지 검증**(주 서비스이거나 UserService에 존재) 후
  `service.manager_email`을 그 계정 이메일로 교체 → 알림 수신처가 바뀜.
- 감사 `service.set_primary_manager`. 비담당/삭제/비-매니저면 검증 에러.

### 4-6. 담당자 해제 — `POST /admin/services/{id}/managers/{uid}/remove` (`accounts.unassign_service`)
- **대표는 해제 불가**: 라우트에서 `target.email == service.manager_email`이면 거부
  (먼저 다른 계정을 대표로 지정해야 함). 그 외에는 `unassign_service`가 `UserService` 제거,
  주 서비스였다면 남은 담당 중 하나를 주로 승격.

### 4-7. 허용 IP 변경 — `POST /admin/services/{id}/ips` (`registry.update_allowed_ips`)
- `_parse_ips`로 파싱 → `_validate_ips`(1개+IPv4) → `service.allowed_ips` 교체 → 감사 `service.update_ips`.
- 이 IP 목록이 외부 API 인증 단계(문서 08)에서 화이트리스트로 쓰인다.

### 4-8. 활성/비활성 — `POST /admin/services/{id}/status` (`registry.set_service_status`)
- `ACTIVE`/`INACTIVE`만 허용. `INACTIVE`면 외부 API 인증이 거부됨(서비스 일시 차단).
- 감사 `service.set_status`.

### 4-9. 삭제 — `POST /admin/services/{id}/delete` (`registry.delete_service`)
- **구독 이력이 1건이라도 있으면 삭제 불가**(`ConflictError`) → 대신 비활성화 권장.
  (DB도 FK RESTRICT로 이중 방어.)
- 구독이 없으면: 요금제 먼저 삭제 → 서비스 삭제. 이때 이 서비스를 **주 서비스로만 가진 담당자
  계정은 DB의 ON DELETE CASCADE로 함께 삭제**된다(서비스 없는 담당자는 무의미). 삭제된
  담당자 수를 감사 `service.delete`의 detail에 기록. → 목록으로 리다이렉트.

### 4-9b. 취소 정책 변경 — `POST /admin/services/{id}/cancel-policy` (`registry.update_cancel_policy`)

서비스 상세 화면에 **허용 IP 카드와 분리된 별도 카드**("일반결제 취소 정책")로 배치된다(요청 013).
한 줄 UI로 체크박스와 수수료율 입력이 나란히 표시된다:

```html
<!-- detail.html: 허용 IP 카드 아래, plans/subs/oneoff 표 위에 별도 div.card -->
<div class="card">
  <h3>일반결제 취소 정책</h3>
  <form method="post" action="/admin/services/{{ service.id }}/cancel-policy"
        style="display:flex;align-items:center;gap:12px">
    <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
    <label>
      <input type="checkbox" name="cancellation_enabled" {{ 'checked' if service.cancellation_enabled }}>
      취소 허용
    </label>
    <label>
      취소 수수료 <input type="number" name="cancellation_fee_percent" min="0" max="100"
        value="{{ service.cancellation_fee_percent }}"> %
    </label>
    <button type="submit">저장</button>
  </form>
</div>
```

- 체크박스 미체크(`cancellation_enabled` 키가 없음): `False`.
- 수수료율 0~100 범위 외 입력: `registry.update_cancel_policy`에서 `InputValidationError`.
- 감사 `service.cancel_policy_updated` → `detail={"enabled": bool, "fee_percent": int}`.
- 성공 시 서비스 상세(`/admin/services/{id}`)로 303 리다이렉트.

### 4-10. 목록 — `GET /admin/services` (`services_list`)
- `PageParams`로 검색(`q`: 서비스명·대표이메일)·상태 필터·정렬·페이지 처리 후 `paginate`.
- htmx면 표 fragment만 응답(`render_list`).

---

## 5. 예외 · 엣지 케이스 정리

| 상황 | 처리 | 코드 위치 |
|---|---|---|
| 담당자 0명(대표 미선택) | `InputValidationError` → 폼 에러 | `_validate_managers` |
| 존재하지 않는/삭제된/비-매니저 계정 선택 | 검증 에러 → 폼 에러 | `_validate_managers` |
| 폼 `manager_ids`/`primary_user_id`가 UUID 형식 아님 | 라우트에서 `ValueError` → 폼 에러 | `services_create` |
| 서비스명 중복(동시 요청 포함) | 사전 체크 + flush `IntegrityError` 양쪽에서 409 | `register_service` |
| IP 비었거나 IPv4 아님 | `InputValidationError` | `_validate_ips` |
| 대표 담당자 해제 시도 | 라우트에서 차단(다른 대표 먼저 지정) | `services_remove_manager` |
| 구독 있는 서비스 삭제 | `ConflictError` + FK RESTRICT | `delete_service` |
| 키 복호화 실패 | 모달 내 안내(500 아님) | `services_keys_modal` |
| 트랜잭션 중간 실패 | 전체 롤백(반쪽 등록 없음) | `register_service`의 단일 commit |

**동시성**: 같은 이름으로 두 요청이 거의 동시에 들어와도, 둘 다 사전 체크를 통과할 수 있지만
DB `name` 유니크 제약이 한쪽만 통과시키고 다른 쪽은 `IntegrityError` → 409로 수렴한다.

---

## 6. 관련 테스트 (동작을 고정하는 곳)

- 서비스 계층 단위/통합: `tests/integration/test_registry.py`
  - 키·담당자 배정, `manager_email`=대표, 계정 미생성(User 수 불변), 대표 자동 포함,
    검증 에러 4종(빈 목록/미존재/DELETED/비-매니저), 이름 중복, 대표 지정/거부, 삭제 CASCADE 등.
- e2e(HTTP 레벨): `tests/e2e/test_admin_services_plans.py`, `tests/e2e/test_service_detail_page.py`
  - 등록→키 1회 노출, 폼 후보 렌더/0명 안내, 대표만 선택 자동포함, 잘못된 IP 폼 에러,
    상세 대표 배지/지정/해제 차단, 개요 담당자 표기 등.

> 기능 추가/수정 시: **규칙을 바꾸면 `registry.py` + `test_registry.py`**,
> **화면/입력을 바꾸면 라우트 + 템플릿 + `test_admin_services_plans.py`** 를 함께 고치는 것이 패턴이다.

---

## 7. 이 기능을 확장할 때 체크리스트 (유지보수 가이드)

1. **취소 정책 수수료율 변경 규칙**: `cancellation_fee_percent` 0~100 검증은
   `register_service`와 `update_cancel_policy` 양쪽에 있다. 범위를 조정하면 둘 다 수정할 것.
   정책 변경 시 이미 DONE 상태인 결제의 취소 수수료율은 **변경 시점에 적용된 값** 기준이다
   (DB 취소 시 service 객체를 조회해 실시간 계산).
2. **새 입력 필드 추가** (예: 서비스 설명):
   - `Service` 모델에 컬럼 + Alembic 마이그레이션
   - `new.html` 폼 입력 + `services_create`에서 파싱 → `register_service` 파라미터 추가
   - 검증이 필요하면 `register_service` 안에서(라우트가 아니라). 테스트 추가.
2. **새 검증 규칙**: `register_service`/`_validate_*`에 추가하고 `InputValidationError`/`ConflictError`로
   던지면 라우트가 자동으로 폼 에러로 표시한다(라우트 수정 불필요).
3. **감사로그가 필요한 새 동작**: 서비스 계층에서 `record_audit(...)` 호출 후 같은 트랜잭션에서 commit.
   필요하면 `app/admin/audit_labels.py`에 액션 한글 라벨 추가(문서 10 참고).
4. **권한을 SERVICE_MANAGER에도 열고 싶다면**: 라우트의 `require_admin` → `require_any`로 바꾸고,
   `ctx.service_ids`로 스코프 제한을 직접 적용해야 한다(현재는 시스템관리자 전용이라 불필요).
5. **트랜잭션 경계 원칙 유지**: 서비스 계층 함수 하나가 "검증→변경→감사→commit"을 책임진다.
   라우트에서 commit 하거나, 한 동작을 여러 commit으로 쪼개지 말 것(반쪽 상태 위험).
