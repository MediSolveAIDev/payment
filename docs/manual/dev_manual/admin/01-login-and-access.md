# 01. 로그인·접근 (로그인 / 비밀번호 설정 / 로그아웃 / IP 제한)

> **대상 독자**: 어드민 콘솔을 사용하는 운영자(화면 조작 방법) + 인증 흐름을 파악해야 하는 개발자(라우트·코드 위치).
>
> 세션·CSRF·IP 제한의 심층 구현 상세는 [../03-auth-and-security.md](../03-auth-and-security.md)를 참고하세요.
> 계정 생성·비밀번호 재설정 발급·역할 관리는 [../13-admin-accounts.md](../13-admin-accounts.md)를 참고하세요.

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

| 화면 | URL | 메서드 | 접근 권한 |
|------|-----|--------|----------|
| 로그인 폼 | `/admin/login` | GET | 로그인 불필요(미인증 접근 가능) |
| 로그인 처리 | `/admin/login` | POST | 로그인 불필요 |
| 로그아웃 처리 | `/admin/logout` | POST | 로그인 필요 (`require_any`) |
| 비밀번호 설정 폼 | `/admin/setup-password?token=<TOKEN>` | GET | 로그인 불필요 (토큰으로 접근) |
| 비밀번호 설정 처리 | `/admin/setup-password` | POST | 로그인 불필요 (토큰으로 검증) |

- **로그인 화면**은 인증 여부와 무관하게 접근할 수 있습니다. 이미 로그인된 상태에서 접근해도 별도로 리다이렉트하지 않습니다.
- **비밀번호 설정 화면**은 계정 생성 시 또는 관리자가 재설정 발급 시 이메일로 전달된 토큰 링크를 통해서만 의미 있게 사용됩니다. 토큰 없이 폼을 열 수는 있지만 제출하면 오류가 납니다.
- 어드민 콘솔 전체(`/admin` 이하 모든 보호된 경로)는 **로그인 세션이 없으면** 자동으로 `/admin/login`으로 리다이렉트됩니다.
- 전역 설정(`GlobalSettings.admin_allowed_ips`)에 허용 IP 목록이 설정된 경우, 목록 외 IP에서는 로그인 후 보호 경로 접근 시 403으로 차단됩니다.

---

## 2. 화면 구성

### 로그인 화면 (`/admin/login`)

템플릿: `app/admin/templates/login.html`

- 페이지 제목: **결제 관리 로그인**
- **오류 메시지 영역** (`.error`): 인증 실패·계정 잠금·비활성화 등 오류 발생 시 폼 상단에 표시됩니다.
- **개발 모드 안내 배너** (`.notice`): `environment == "dev"`(로컬 개발)에서만 표시되며 "개발 모드 — 로그인 정보가 자동 입력되었습니다." 문구가 나타납니다. 스테이징(stg)·운영(prod)에서는 표시되지 않습니다.
- **이메일 입력** (`<input type="email" name="email">`): 로컬 개발(dev)에서만 설정값으로 미리 채워지고, 스테이징(stg)·운영(prod)에서는 비어 있습니다.
- **비밀번호 입력** (`<input type="password" name="password">`): 눈 아이콘 버튼(`.eye-btn`)으로 평문 표시 토글이 가능합니다.
- **로그인 버튼**: 폼 전체 너비 `btn btn-primary`. 클릭 시 `POST /admin/login`으로 제출됩니다.
- htmx 비동기 없음 — 일반 폼 `method="post"` 제출 방식입니다.

### 비밀번호 설정 화면 (`/admin/setup-password`)

템플릿: `app/admin/templates/setup_password.html`

- 페이지 제목: **비밀번호 설정**
- **오류 메시지 영역** (`.error`): 비밀번호 불일치·토큰 만료/사용됨 등 오류 표시.
- **토큰 hidden 필드** (`<input type="hidden" name="token">`): URL 쿼리 파라미터로 받은 토큰이 폼에 숨겨져 전송됩니다.
- **새 비밀번호** (`<input type="password" name="password" minlength="10">`): HTML 단에서 10자 미만 입력 차단. 눈 아이콘 토글 가능.
- **비밀번호 확인** (`<input type="password" name="password_confirm">`): 눈 아이콘 토글 가능.
- **설정 버튼**: `btn btn-primary`. 클릭 시 `POST /admin/setup-password`로 제출됩니다.

---

## 3. 할 수 있는 동작

### 3-1. 로그인

1. 브라우저에서 `/admin/login`에 접속합니다.
2. **이메일**과 **비밀번호**를 입력하고 **로그인** 버튼을 클릭합니다.
3. 성공하면 `/admin` (대시보드)으로 303 리다이렉트됩니다. 브라우저에 `admin_session` 쿠키가 설정됩니다.
4. 실패하면 오류 메시지와 함께 로그인 폼이 다시 렌더됩니다.

| 상태 | 화면에 표시되는 오류 메시지 |
|------|--------------------------|
| 존재하지 않는 이메일 | 이메일 또는 비밀번호가 올바르지 않습니다 |
| 비밀번호 불일치 | 이메일 또는 비밀번호가 올바르지 않습니다 |
| 5회 연속 실패로 잠금 | 계정이 잠겼습니다. 잠시 후 다시 시도해주세요 |
| 비활성화(DISABLED) 계정 | 비활성화된 계정입니다. 관리자에게 문의하세요 |
| 미설정(PENDING) 계정 | 비밀번호 설정이 필요합니다. 안내 메일을 확인해주세요 |

> **계정 잠금**: 비밀번호를 **5회** 연속 틀리면 계정이 **15분** 잠깁니다. 잠금 만료 후 올바른 비밀번호로 로그인하면 자동으로 잠금이 해제됩니다.

### 3-2. 로그아웃

- 어드민 콘솔 상단(탑바)의 **로그아웃** 버튼을 클릭합니다.
- `POST /admin/logout` 요청이 발생하고 Redis 세션이 삭제됩니다. `admin_session` 쿠키가 제거되고 `/admin/login`으로 리다이렉트됩니다.

### 3-3. 비밀번호 설정 (최초 설정 / 재설정)

1. 이메일로 수신한 링크(`/admin/setup-password?token=<TOKEN>`)를 브라우저에서 엽니다.
2. **새 비밀번호**(10자 이상)와 **비밀번호 확인**을 입력하고 **설정** 버튼을 클릭합니다.
3. 성공하면 `/admin/login`으로 리다이렉트됩니다. 이후 새 비밀번호로 로그인하면 됩니다.
4. 비밀번호 재설정의 경우 해당 계정의 **기존 세션이 모두 파기**됩니다(다른 브라우저·기기에서 로그인된 세션 포함).

| 오류 상황 | 화면에 표시되는 오류 |
|-----------|-------------------|
| 새 비밀번호와 확인 불일치 | 비밀번호가 일치하지 않습니다 |
| 10자 미만 비밀번호 | 비밀번호는 10자 이상이어야 합니다 |
| 만료됐거나 이미 사용된 토큰 | 유효하지 않거나 만료된 토큰입니다 |

> **토큰은 1회용입니다.** 설정 완료 후 같은 링크로 다시 접속하면 오류가 납니다.
> **토큰 유효 시간은 48시간**입니다. 만료 후에는 관리자에게 재발급을 요청해야 합니다.
> 같은 계정에 여러 미사용 토큰이 있을 경우 하나를 사용하면 나머지는 **자동으로 무효화**됩니다.

---

## 4. 개발 참조

### 4-1. 라우트 함수

모든 인증 라우트는 `app/admin/routes/auth.py`에 있습니다.

| 기능 | 함수 | 파일:줄 |
|------|------|---------|
| 로그인 폼 렌더 | `login_page` | `app/admin/routes/auth.py:23` |
| 로그인 처리 | `login_submit` | `app/admin/routes/auth.py:38` |
| 로그아웃 처리 | `logout` | `app/admin/routes/auth.py:72` |
| 비밀번호 설정 폼 렌더 | `setup_password_page` | `app/admin/routes/auth.py:89` |
| 비밀번호 설정 처리 | `setup_password_submit` | `app/admin/routes/auth.py:99` |

### 4-2. 서비스 레이어

| 기능 | 함수 | 파일:줄 |
|------|------|---------|
| 로그인 처리 (계정 잠금 포함) | `auth_service.login` | `app/services/auth.py:85` |
| 비밀번호 설정 (토큰 검증·세션 파기) | `auth_service.setup_password` | `app/services/auth.py:185` |
| 세션 생성 | `_create_session` | `app/services/auth.py:55` |
| 세션 조회 + TTL 연장 | `auth_service.get_session` | `app/services/auth.py:146` |
| 세션 삭제 (로그아웃) | `auth_service.logout` | `app/services/auth.py:161` |
| 사용자 전체 세션 파기 | `auth_service.destroy_user_sessions` | `app/services/auth.py:170` |
| 비밀번호 재설정 토큰 발급 + 메일 | `auth_service.issue_password_reset` | `app/services/auth.py:257` |

### 4-3. 인증 의존성 (`app/admin/deps.py`)

| 기능 | 심볼 | 파일:줄 |
|------|------|---------|
| 세션 쿠키 이름 상수 | `SESSION_COOKIE = "admin_session"` | `app/admin/deps.py:31` |
| 로그인 여부 확인·IP 제한 적용 | `require_user` | `app/admin/deps.py:60` |
| 역할 기반 접근 제어 팩토리 | `require_role` | `app/admin/deps.py:86` |
| SYSTEM_ADMIN 전용 축약 | `require_admin` | `app/admin/deps.py:100` |
| SYSTEM_ADMIN·SERVICE_MANAGER 허용 축약 | `require_any` | `app/admin/deps.py:102` |
| CSRF 토큰 검증 | `validate_csrf` | `app/admin/deps.py:105` |
| AdminAuthRequired 예외 → 리다이렉트 핸들러 | `register_admin_exception_handlers` | `app/admin/deps.py:118` |
| 인증 컨텍스트 데이터 클래스 | `AdminContext` | `app/admin/deps.py:42` |

### 4-4. 계정 잠금 상수 (`app/services/auth.py`)

| 상수 | 값 | 파일:줄 |
|------|----|---------|
| 연속 실패 허용 횟수 | `MAX_FAILED_LOGINS = 5` | `app/services/auth.py:43` |
| 잠금 지속 시간 | `LOCK_DURATION = timedelta(minutes=15)` | `app/services/auth.py:44` |
| 로그인 실패 메시지 | `LOGIN_FAILED_MESSAGE` | `app/services/auth.py:47` |
| 비밀번호 최소 길이 | `MIN_PASSWORD_LENGTH = 10` | `app/services/auth.py:48` |
| 비밀번호 설정 토큰 유효 시간 | `RESET_TOKEN_TTL = timedelta(hours=48)` | `app/services/auth.py:254` |

### 4-5. 세션 쿠키 속성 (`app/admin/routes/auth.py:64–67`)

로그인 성공 시 `response.set_cookie()`로 다음 속성이 적용됩니다.

| 속성 | 값 | 설명 |
|------|----|------|
| `httponly` | `True` | JavaScript에서 쿠키 접근 불가 (XSS 탈취 방지) |
| `samesite` | `"lax"` | CSRF 완화 (일반 링크 이동은 허용) |
| `secure` | `environment == "prod"` 일 때만 `True` | 운영에서만 HTTPS 전용 전송 |
| `max_age` | `settings.session_ttl_seconds` | 설정값 기반 세션 수명 |
| `path` | `"/"` | 전체 경로에서 쿠키 전송 (`/admin`만 지정하면 리다이렉트 시 누락 가능) |

### 4-6. 개발 환경 자동입력 (`app/admin/routes/auth.py:31–34`)

`settings.environment == "dev"` 조건으로 **로컬 개발에서만** `dev_login_email` / `dev_login_password`가 폼에 채워집니다. 스테이징(stg)·운영(prod) 등 외부에 노출되는 환경에서는 자격증명이 화면에 보이지 않도록 절대 채워지지 않습니다.

### 4-7. htmx 연동

로그인·로그아웃·비밀번호 설정 화면은 **htmx를 사용하지 않습니다** — 모두 일반 HTML `<form method="post">` 제출입니다.

단, 로그인이 필요한 경로를 htmx 요청(HX-Request 헤더 포함)으로 호출할 때 세션이 없으면 303 리다이렉트 대신 `HX-Redirect` 헤더(204 No Content)로 응답합니다(`app/admin/deps.py:118`). 이를 받은 htmx가 클라이언트 측에서 전체 페이지 이동을 수행합니다.

### 4-8. IP 제한 적용 위치 (`app/admin/deps.py:79–81`)

`require_user` 내에서 `GlobalSettings.admin_allowed_ips`를 매 요청마다 DB에서 조회합니다. 목록이 비어 있으면 제한 없음, 목록이 있으면 현재 클라이언트 IP가 포함돼야 합니다. 위반 시 `PermissionDeniedError("허용되지 않은 IP입니다")` — 403으로 처리됩니다.

### 4-9. 관련 문서 링크

- 세션·CSRF·IP 제한 심층 설명: [../03-auth-and-security.md](../03-auth-and-security.md)
- 어드민 계정 생성·역할·비밀번호 재설정 발급: [../13-admin-accounts.md](../13-admin-accounts.md)

---

## 5. 주의사항 / 자주 하는 실수

### 운영자

- **비밀번호 5회 실수 시 15분 잠금**됩니다. 잠금 중에는 올바른 비밀번호를 입력해도 로그인할 수 없습니다. 15분 후 자동으로 해제됩니다.
- **비밀번호 설정 링크는 1회만 사용**할 수 있습니다. 이미 설정 완료 후 같은 링크를 다시 열면 "유효하지 않거나 만료된 토큰입니다" 오류가 납니다. 재발급이 필요하면 시스템 관리자에게 요청하세요.
- **링크 유효 시간은 48시간**입니다. 이메일을 받은 후 이틀 내에 비밀번호를 설정해야 합니다.
- 비밀번호 재설정 완료 시 **다른 기기의 로그인 세션도 모두 강제 로그아웃**됩니다.

### 개발자

- 로그인 실패 메시지는 **존재하지 않는 이메일과 비밀번호 불일치를 구분하지 않습니다**. 계정 열거(enumeration) 공격 방지를 위한 의도된 설계입니다(`app/services/auth.py:47`). 메시지를 계정별로 다르게 바꾸면 보안 취약점이 됩니다.
- 존재하지 않는 이메일 입력 시에도 `verify_password(_DUMMY_HASH)`를 실행해 응답 시간을 균등화합니다(`app/services/auth.py:103`). 이 로직을 제거하면 타이밍 사이드채널로 이메일 존재 여부 추론이 가능해집니다.
- 쿠키 `path="/"` 설정이 의도된 값입니다(`app/admin/routes/auth.py:67`). `/admin`으로 변경하면 `/admin/login` 리다이렉트 후 쿠키가 누락되어 인증 루프가 발생할 수 있습니다.
- `require_admin`과 `require_any`는 단순 별칭입니다(`app/admin/deps.py:100–102`). 새 역할이 추가되면 `require_role`에 직접 전달하세요.
- 감사 로그(`auth.login` / `auth.login_failed`)는 DB 커밋 이후 세션을 생성하는 순서로 처리됩니다(`app/services/auth.py:140–142`). 커밋 실패 시 유효 세션이 발급되지 않도록 순서가 보장됩니다.
- `GlobalSettings.admin_allowed_ips` 설정 변경은 **즉시 반영**됩니다(캐시 없음, 매 요청 DB 조회). 잘못된 IP 목록을 저장하면 관리자 포함 전원 접근 차단이 발생합니다. 변경 전 반드시 자신의 IP가 목록에 포함돼 있는지 확인하세요.
