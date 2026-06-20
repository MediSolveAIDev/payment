# 13. 어드민 계정·역할·로그인/비밀번호

> **어드민 콘솔에 로그인하는 관리자 계정을 어떻게 만들고, 어떻게 인증하며,
> 역할에 따라 무엇이 달라지는지**를 처음부터 끝까지 추적한다.  
> 세션·CSRF 공통 구조는 [03. 인증과 보안 공통](03-auth-and-security.md) 참고.

---

## 1. 한 줄 요약

SYSTEM_ADMIN이 어드민 콘솔에서 관리자 계정(SYSTEM_ADMIN 또는 SERVICE_MANAGER)을
생성·수정·비활성화·삭제하고, 비밀번호 재설정 링크를 발급하며, 서비스 담당을 배정한다.

---

## 2. 언제 실행되나

| 트리거 | 경로 |
|---|---|
| 어드민이 로그인 폼 제출 | `POST /admin/login` |
| 어드민이 로그아웃 버튼 클릭 | `POST /admin/logout` |
| 어드민이 비밀번호 설정 링크(이메일)에서 폼 제출 | `POST /admin/setup-password` |
| SYSTEM_ADMIN이 계정 목록 조회 | `GET /admin/users` |
| SYSTEM_ADMIN이 신규 계정 생성 | `POST /admin/users` |
| SYSTEM_ADMIN이 계정 상세 조회 | `GET /admin/users/{user_id}` |
| SYSTEM_ADMIN이 계정 정보(이메일·전화번호) 수정 | `POST /admin/users/{user_id}/edit` |
| SYSTEM_ADMIN이 서비스 담당 할당 | `POST /admin/users/{user_id}/services` |
| SYSTEM_ADMIN이 서비스 담당 해제 | `POST /admin/users/{user_id}/services/{service_id}/remove` |
| SYSTEM_ADMIN이 계정 비활성화/활성화 | `POST /admin/users/{user_id}/disable` |
| SYSTEM_ADMIN이 계정 삭제 | `POST /admin/users/{user_id}/delete` |
| SYSTEM_ADMIN이 비밀번호 재설정 메일 발송 | `POST /admin/users/{user_id}/reset-password` |

---

## 3. 요청 진입점

### 3-1. 인증 라우트

`app/admin/routes/auth.py`

| HTTP | 경로 | 함수 | 설명 |
|---|---|---|---|
| `GET` | `/admin/login` | `login_page` (line 22) | 로그인 폼 렌더 |
| `POST` | `/admin/login` | `login_submit` (line 37) | 로그인 처리 + 쿠키 발급 |
| `POST` | `/admin/logout` | `logout` (line 71) | 세션 삭제 + 쿠키 제거 |
| `GET` | `/admin/setup-password` | `setup_password_page` (line 88) | 비밀번호 설정 폼 렌더 |
| `POST` | `/admin/setup-password` | `setup_password_submit` (line 98) | 비밀번호 설정 처리 |

### 3-2. 계정 관리 라우트

`app/admin/routes/users.py`

| HTTP | 경로 | 함수 | 설명 |
|---|---|---|---|
| `GET` | `/admin/users` | `users_list` (line 80) | 계정 목록(htmx partial 겸용) |
| `GET` | `/admin/users/export.xlsx` | `users_export` (line 100) | 엑셀 다운로드 |
| `GET` | `/admin/users/new` | `users_new` (line 116) | 계정 생성 폼 |
| `POST` | `/admin/users` | `users_create` (line 124) | 계정 생성 처리 |
| `GET` | `/admin/users/{user_id}` | `users_detail` (line 151) | 계정 상세 |
| `POST` | `/admin/users/{user_id}/services` | `users_assign_service` (line 172) | 서비스 담당 할당 |
| `POST` | `/admin/users/{user_id}/services/{service_id}/remove` | `users_unassign_service` (line 190) | 서비스 담당 해제 |
| `GET` | `/admin/users/{user_id}/edit` | `users_edit` (line 203) | 계정 수정 폼 |
| `POST` | `/admin/users/{user_id}/edit` | `users_update` (line 214) | 계정 수정 처리 |
| `POST` | `/admin/users/{user_id}/disable` | `users_disable` (line 233) | 비활성화/활성화 토글 |
| `POST` | `/admin/users/{user_id}/delete` | `users_delete` (line 261) | 계정 논리 삭제 |
| `POST` | `/admin/users/{user_id}/reset-password` | `users_reset_password` (line 278) | 비밀번호 재설정 메일 발송 |

> **접근 제한:** 계정 관리 라우트 전체에 `Depends(require_admin)` 이 붙어 있어
> SYSTEM_ADMIN만 사용할 수 있다. (`app/admin/routes/users.py:4`)

---

## 4. 단계별 처리 흐름

### 4-1. 로그인

```
POST /admin/login
  └─ app/admin/routes/auth.py:37  login_submit()
       1. 폼에서 email, password, client IP 추출
       2. auth_service.login(db, redis, settings, email=, password=, ip=)
            → app/services/auth.py:85
            1) DB에서 User 조회(email)
            2) 존재하지 않는 이메일: verify_password(_DUMMY_HASH) 후 거부
               (타이밍 균등화 — 계정 열거 방지)
            3) LOCKED:
               - locked_until 초과(만료) → status=ACTIVE, failed_login_count=0 자동 복구
               - locked_until 이내 → 즉시 거부("계정이 잠겼습니다")
            4) DELETED → 없는 것처럼 처리(LOGIN_FAILED_MESSAGE)
            5) DISABLED → "비활성화된 계정입니다. 관리자에게 문의하세요"
            6) PENDING → "비밀번호 설정이 필요합니다"
            7) verify_password(password, user.password_hash) — Argon2id 검증
               - 실패: failed_login_count += 1
               - 5회 이상: status=LOCKED, locked_until=now+15분
            8) 성공: failed_login_count=0, 감사 로그(auth.login), DB 커밋
            9) _create_session(redis) → Redis Hash에 세션 기록, session_id 반환
       3. 쿠키 설정 후 /admin 으로 303 리다이렉트
```

**쿠키 속성** (`app/admin/routes/auth.py:64-67`):
- `httponly=True` — JavaScript 접근 불가
- `samesite="lax"` — 외부 사이트 요청에서 쿠키 미전송
- `secure` — 운영(`environment == "prod"`)에서만 HTTPS 전용
- `max_age` — `settings.session_ttl_seconds`
- `path="/"` — 전체 경로(하위 경로만 지정하면 리다이렉트 시 쿠키 누락 가능)

### 4-2. Redis 세션 구조

`app/services/auth.py:55-82` `_create_session()`:

```
Redis Hash  key: "session:{session_id}"
  user_id     → UUID 문자열
  role        → SYSTEM_ADMIN | SERVICE_MANAGER
  service_id  → 주 서비스 UUID (없으면 빈 문자열)
  csrf_token  → 랜덤 32바이트 urlsafe 토큰

Redis Set   key: "user_sessions:{user_id}"
  members: session_id 목록 (활성 세션 전체)
  사용처: 비밀번호 변경·비활성화 시 전체 세션 파기
```

hset + expire를 파이프라인 트랜잭션으로 묶어 TTL 없는 불멸 세션을 방지한다.
(`app/services/auth.py:71` — `redis.pipeline(transaction=True)`)

**service_id를 세션에 캐시하지 않는 이유:**  
추가 담당 서비스(`UserService` 다대다)는 캐시하지 않고 매 요청마다 DB 조회한다.
권한 변경이 다음 요청에 즉시 반영되어야 하기 때문이다.
(`app/services/auth.py:63-65` 주석)

### 4-3. 로그아웃

```
POST /admin/logout
  └─ app/admin/routes/auth.py:71  logout()
       1. validate_csrf(request, ctx) — CSRF 검증
       2. auth_service.logout(redis, ctx.session_id)
            → app/services/auth.py:161
            - Redis에서 "session:{id}" 삭제
            - "user_sessions:{user_id}" Set에서 session_id 제거
       3. 쿠키 삭제 후 /admin/login 으로 303 리다이렉트
```

### 4-4. 계정 생성 + 비밀번호 설정 메일 발송

```
POST /admin/users
  └─ app/admin/routes/users.py:124  users_create()
       1. validate_csrf()
       2. 폼에서 email, role, service_ids(멀티셀렉트), phone 추출
       3. account_service.create_account(db, email_sender, ...)
            → app/services/accounts.py:72
            1) 이메일 소문자 정규화 + 전화번호 정규화(_PHONE_RE 정규식 검증)
            2) 역할 검증: SYSTEM_ADMIN | SERVICE_MANAGER 만 허용
            3) SYSTEM_ADMIN이면 service_ids 강제 빈 배열
            4) service_ids 중복 제거(순서 보존)
            5) 서비스 존재 확인(_validate_services_exist)
            6) 이메일 중복 확인(SELECT 선조회 + flush IntegrityError 이중 방어)
            7) User 생성: status=PENDING, service_id=service_ids[0] 또는 None
            8) service_ids[1:] → UserService 다대다로 추가
            9) PasswordSetupToken 생성: token_hash=sha256_hex(평문), expires_at=+48시간
            10) 감사 로그(account.create) → DB 커밋
            11) 설정 메일 발송(커밋 후 — 실패해도 계정은 유지됨), 발송 성공 여부 반환
       4. 계정 목록으로 리다이렉트 + flash 토스트(메일 발송 결과)
```

**평문 토큰 전달 경로:**  
`generate_setup_token()` 이 생성한 평문은 이메일 URL에만 포함된다.
DB에는 `sha256_hex(token)` 해시만 저장된다.
(`app/services/accounts.py:124-126`, `app/core/security.py:37-42`)

### 4-5. 비밀번호 설정 (setup-password 토큰 플로우)

```
이메일 링크: /admin/setup-password?token={평문 토큰}

POST /admin/setup-password
  └─ app/admin/routes/auth.py:98  setup_password_submit()
       1. 폼에서 token, password, password_confirm 추출
       2. 비밀번호 불일치 → 폼 재렌더(서비스 레이어 미진입)
       3. auth_service.setup_password(db, token=, password=, redis=)
            → app/services/auth.py:185
            1) 비밀번호 최소 길이 검증(MIN_PASSWORD_LENGTH=10자)
            2) DB에서 sha256_hex(token) 일치 + used_at IS NULL 조회
               (조회 결과 없거나 expires_at < now → "유효하지 않거나 만료된 토큰")
            3) User.password_hash = hash_password(password) — Argon2id 해시
               status=ACTIVE, failed_login_count=0, locked_until=None
            4) 현재 토큰 used_at=now 기록(1회용 소비)
            5) 같은 user_id의 다른 미사용 토큰 used_at=now 일괄 무효화
            6) 감사 로그(auth.password_set) → DB 커밋
            7) redis가 있으면 destroy_user_sessions — 기존 세션 전체 파기
       4. /admin/login 으로 303 리다이렉트
```

**redis를 전달하는 이유:**  
비밀번호 변경은 보안 이벤트이므로 이전에 탈취된 세션을 즉시 무효화한다.
라우트에서 `redis=redis` 를 명시적으로 전달한다.
(`app/admin/routes/auth.py:120`)

### 4-6. 비밀번호 재설정 메일 재발송 (관리자 주도)

```
POST /admin/users/{user_id}/reset-password
  └─ app/admin/routes/users.py:278  users_reset_password()
       1. validate_csrf()
       2. auth_service.issue_password_reset(db, email_sender, user_id=, redis=)
            → app/services/auth.py:257
            1) User 조회
            2) PasswordSetupToken 생성(48시간 유효) → DB에 해시 저장
            3) 감사 로그(user.password_reset_issued) → DB 커밋
            4) redis가 있으면 기존 세션 즉시 파기(탈취 의심 대응)
            5) 재설정 메일 발송 → 발송 성공 여부 반환
       3. 계정 상세로 303 리다이렉트 + flash 토스트(발송 결과)
```

### 4-7. 비활성화 / 활성화 토글

```
POST /admin/users/{user_id}/disable
  └─ app/admin/routes/users.py:233  users_disable()
       1. validate_csrf()
       2. 폼 값 "disabled"="true"/"false" 파싱
          (정확히 "false"일 때만 활성화, 나머지 모든 값은 비활성화)
       3. account_service.set_account_disabled(db, redis, ...)
            → app/services/accounts.py:191
            - disabled=True:
              * 본인 계정 비활성화 시 거부("본인 계정은 비활성화할 수 없습니다")
              * status=DISABLED, 감사 로그(account.disable) → DB 커밋
              * redis가 있으면 기존 세션 파기(커밋 후)
            - disabled=False:
              * 비밀번호 설정 여부에 따라 ACTIVE(있음) 또는 PENDING(없음)으로 복구
              * 감사 로그(account.enable) → DB 커밋
```

### 4-8. 계정 삭제 (소프트 삭제)

```
POST /admin/users/{user_id}/delete
  └─ app/admin/routes/users.py:261  users_delete()
       1. validate_csrf()
       2. account_service.delete_account(db, redis, ...)
            → app/services/accounts.py:223
            1) 본인 삭제 거부("본인 계정은 삭제할 수 없습니다")
            2) 대표 담당자(Service.manager_email) 여부 확인:
               해당 서비스의 manager_email이 이 계정이면 거부
            3) status=DELETED, service_id=None
            4) UserService 행 전체 삭제(DELETE ... WHERE user_id=)
            5) 감사 로그(account.delete) → DB 커밋
            6) redis가 있으면 기존 세션 파기
       3. /admin/users 로 303 리다이렉트
```

물리 삭제를 하지 않는 이유: `audit_logs.actor_user_id` 외래 참조 유지.
(`app/services/accounts.py:228-231` 주석)

### 4-9. 서비스 담당 할당 / 해제

**할당** (`app/services/accounts.py:269`):
1. SERVICE_MANAGER 역할 확인(`_get_manager`)
2. 서비스 존재 확인
3. `effective_service_ids`로 이미 담당 여부 확인(중복이면 조용히 무시)
4. `user.service_id == None` → 주 서비스로 직접 설정, 그 외 → `UserService` 다대다 추가

**해제** (`app/services/accounts.py:294`):
1. `UserService`에서 해당 행 삭제
2. 해제한 서비스가 `User.service_id`(주 서비스)이면 `UserService`에서 다른 서비스를
   꺼내 주 서비스로 승격. 남은 것이 없으면 `service_id=None`

---

## 5. 사용하는 DB 테이블·컬럼

### 5-1. users 테이블

`app/models/user.py:19`

| 컬럼 | 타입 | 설명 | 읽기/쓰기 |
|---|---|---|---|
| `id` | UUID PK | 자동 생성 | R |
| `email` | String(255) UNIQUE | 로그인 ID 겸 연락처 | R/W |
| `phone` | String(30) NULL | 연락처(선택) | R/W |
| `password_hash` | String(512) | Argon2id 해시; PENDING 시 빈 문자열 | R/W |
| `role` | String(20) | `SYSTEM_ADMIN` \| `SERVICE_MANAGER` | R/W |
| `service_id` | UUID FK NULL | 주 담당 서비스; SYSTEM_ADMIN은 NULL | R/W |
| `status` | String(20) | `PENDING`/`ACTIVE`/`LOCKED`/`DISABLED`/`DELETED` | R/W |
| `failed_login_count` | Integer | 연속 로그인 실패 횟수 | R/W |
| `locked_until` | DateTime NULL | 자동 잠금 해제 시각(UTC) | R/W |

**service_id 외래 키 삭제 정책:** `ondelete="CASCADE"` — 서비스가 삭제되면 `service_id`가
자동으로 NULL이 된다(연결 테이블 `user_services`도 CASCADE).
(`app/models/user.py:33-34`)

### 5-2. user_services 테이블 (다대다)

`app/models/user_service.py:11`

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `user_id` | UUID PK FK | 관리자 삭제 시 CASCADE |
| `service_id` | UUID PK FK | 서비스 삭제 시 CASCADE |
| `created_at` | DateTime | 담당 배정 시각(UTC) |

### 5-3. password_setup_tokens 테이블

`app/models/user.py:40`

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `id` | UUID PK | 자동 생성 |
| `user_id` | UUID FK | 계정 삭제 시 CASCADE |
| `token_hash` | String(64) UNIQUE | SHA-256 해시(평문 미저장) |
| `expires_at` | DateTime | 링크 유효 기한(생성 후 48시간) |
| `used_at` | DateTime NULL | 최초 사용 시각; NULL=미사용 |

### 5-4. audit_logs 테이블

`app/models/audit_log.py:17`

계정 관련 주요 `action` 값:

| action | 발생 시점 |
|---|---|
| `auth.login` | 로그인 성공 |
| `auth.login_failed` | 로그인 실패(이메일 없음 또는 비밀번호 불일치) |
| `auth.password_set` | 비밀번호 설정/재설정 완료 |
| `user.create_admin` | CLI로 최초 SYSTEM_ADMIN 생성 |
| `user.password_reset_issued` | 관리자가 비밀번호 재설정 토큰 발급 |
| `account.create` | 어드민에서 계정 생성 |
| `account.update` | 이메일·전화번호 수정 |
| `account.disable` | 계정 비활성화 |
| `account.enable` | 계정 활성화(복구) |
| `account.delete` | 계정 삭제(소프트) |
| `account.assign_service` | 서비스 담당 할당 |
| `account.unassign_service` | 서비스 담당 해제 |

---

## 6. 상태 전이 — UserStatus

`app/models/enums.py:23`

```
              계정 생성
                  │
                  ▼
             [PENDING]  ← 비밀번호 미설정 상태
                  │
          setup-password 완료
                  │
                  ▼
             [ACTIVE] ◄─── 관리자 활성화(비밀번호 있음)
              │    │
     5회 실패  │    │  관리자 비활성화
              │    ▼
              │  [DISABLED] ──► (관리자 활성화) ─► ACTIVE 또는 PENDING
              │
              ▼
           [LOCKED]  (15분 후 자동 또는 비밀번호 재설정 후 ACTIVE)
              │
          (관리자) 삭제
              │
              ▼
           [DELETED]  ← 소프트 삭제, 목록에서 숨김, 복구 불가
```

상태별 로그인 처리 (`app/services/auth.py:111-134`):

| 상태 | 로그인 결과 |
|---|---|
| `PENDING` | 거부 — "비밀번호 설정이 필요합니다" |
| `ACTIVE` | 비밀번호 검증 후 허용 |
| `LOCKED` | `locked_until > now` 이면 거부; 초과면 자동 ACTIVE 복구 |
| `DISABLED` | 거부 — "비활성화된 계정입니다. 관리자에게 문의하세요" |
| `DELETED` | 거부 — 계정 없는 것처럼 처리(LOGIN_FAILED_MESSAGE, 열거 방지) |

활성화 복구 시 ACTIVE/PENDING 분기 (`app/services/accounts.py:212-213`):
```python
user.status = UserStatus.ACTIVE if user.password_hash else UserStatus.PENDING
```
비밀번호가 설정된 계정(password_hash != "")은 ACTIVE로, 아직 설정하지 않은 계정은
PENDING으로 복구된다.

---

## 7. 예외·엣지 케이스 / 에러 응답

### 7-1. 본인 계정 보호

| 시도 | 결과 | 코드 위치 |
|---|---|---|
| 본인 비활성화 | `InputValidationError` — "본인 계정은 비활성화할 수 없습니다" | `app/services/accounts.py:209` |
| 본인 삭제 | `InputValidationError` — "본인 계정은 삭제할 수 없습니다" | `app/services/accounts.py:242` |

### 7-2. 대표 담당자 보호

서비스의 `manager_email`(대표 담당자)인 계정은 삭제할 수 없다.
(`app/services/accounts.py:243-247`)

```python
primary_of = await db.scalar(select(Service).where(
    Service.manager_email == user.email))
if primary_of is not None:
    raise InputValidationError(
        f"'{primary_of.name}' 서비스의 대표 담당자입니다. 먼저 다른 계정을 대표로 지정하세요.")
```

먼저 어드민 콘솔 서비스 상세(`/admin/services/{id}`)에서 다른 계정을 대표 담당자로
변경해야 한다.

**이메일 변경 시 동기화:**  
대표 담당자 계정의 이메일을 변경하면 해당 서비스의 `Service.manager_email`이 자동으로
함께 갱신된다. (`app/services/accounts.py:172-175`)

### 7-3. 중복 이메일

| 시점 | 방어 수단 | 코드 위치 |
|---|---|---|
| 계정 생성 시 | `SELECT` 선조회 + `flush()` IntegrityError 이중 방어 | `app/services/accounts.py:109-120` |
| 이메일 수정 시 | `SELECT` 선조회 + 커밋 시 IntegrityError 이중 방어 | `app/services/accounts.py:168-187` |

이메일은 전체 시스템에서 고유해야 한다(`users.email` UNIQUE 제약).

### 7-4. 비밀번호 설정 토큰 — 유효성 검증

토큰 유효 조건 (`app/services/auth.py:201-205`):
1. `token_hash == sha256_hex(평문 토큰)` — 해시 일치
2. `used_at IS NULL` — 미사용
3. `expires_at >= utcnow()` — 만료 전(48시간 이내)

세 조건 중 하나라도 실패: `InputValidationError` — "유효하지 않거나 만료된 토큰입니다"

**1회용 보장:**  
사용 완료된 토큰 `used_at` 기록 후, 같은 사용자의 **다른 미사용 토큰도 일괄 무효화**.
(`app/services/auth.py:213-219`)

### 7-5. 로그인 잠금

| 조건 | 처리 | 코드 위치 |
|---|---|---|
| 비밀번호 실패 | `failed_login_count += 1` | `app/services/auth.py:127` |
| 5회 이상 실패(`MAX_FAILED_LOGINS=5`) | `status=LOCKED`, `locked_until=now+15분` | `app/services/auth.py:128-130` |
| LOCKED + 만료 전 | 올바른 비밀번호도 거부 | `app/services/auth.py:111-113` |
| LOCKED + 만료 후 | 자동 ACTIVE 복구 후 정상 진행 | `app/services/auth.py:114-116` |

잠긴 계정 즉시 해제 방법:
- 관리자가 비밀번호 재설정 메일 발송 → 사용자가 setup-password 완료
  (`setup_password` 에서 `failed_login_count=0`, `locked_until=None` 초기화, `app/services/auth.py:208-210`)
- 또는 15분 대기 후 자동 해제

### 7-6. 이메일 발송 실패

계정 생성/비밀번호 재설정 메일은 **DB 커밋 후** 발송한다.  
발송이 실패해도 계정(또는 토큰)은 DB에 정상 생성된다.  
발송 실패 여부는 `sent=True/False`로 반환되어 flash 토스트로 안내한다.
(`app/services/accounts.py:132-138`, `app/services/auth.py:289-292`)

### 7-7. SYSTEM_ADMIN에게 서비스 할당 시도

`_get_manager`에서 `SERVICE_MANAGER` 역할을 확인한다.
SYSTEM_ADMIN에게 서비스를 할당하려 하면 `InputValidationError`.
(`app/services/accounts.py:259-266`)

### 7-8. 삭제된 계정 수정 시도

`_get_account`에서 `DELETED` 상태를 없는 것으로 처리한다.
수정/비활성화 시도 시 `NotFoundError`.
(`app/services/accounts.py:141-146`)

---

## 8. 관련 테스트

### 8-1. 통합 테스트 — 계정 서비스

`tests/integration/test_accounts.py`

| 테스트 | 검증 내용 |
|---|---|
| `test_create_manager_account_assigns_services_and_emails` (line 16) | 서비스 다대다 할당, 주/추가 구분, 유효 스코프, 메일·토큰 생성 |
| `test_create_admin_account_no_services` (line 37) | SYSTEM_ADMIN 생성 시 서비스 없음, `effective_service_ids=None` |
| `test_create_manager_without_service_allowed` (line 47) | SERVICE_MANAGER 서비스 0개 생성 허용 |
| `test_create_duplicate_email_conflicts` (line 57) | 중복 이메일 `ConflictError` |
| `test_assign_and_unassign_service` (line 65) | 서비스 할당·해제, 중복 할당 무시 |
| `test_assign_to_admin_rejected` (line 80) | SYSTEM_ADMIN에게 서비스 할당 거부 |
| `test_unassign_primary_promotes_or_clears` (line 87) | 주 서비스 해제 시 다음 서비스 승격 |
| `test_disable_and_enable_account` (line 129) | 비활성화/활성화, status 전이 |
| `test_enable_account_without_password_is_pending` (line 140) | 비밀번호 없는 계정 활성화 → PENDING |
| `test_soft_delete_account` (line 148) | 소프트 삭제, 담당 서비스 링크 제거, 삭제 후 수정 불가 |
| `test_update_account_email_syncs_primary_manager_email` (line 163) | 이메일 변경 시 `Service.manager_email` 동기화 |
| `test_delete_account_blocked_when_primary_manager` (line 173) | 대표 담당자 삭제 거부 |

### 8-2. 통합 테스트 — 인증 서비스

`tests/integration/test_auth_service.py`

| 테스트 | 검증 내용 |
|---|---|
| `test_login_success_creates_redis_session` (line 14) | 로그인 성공 시 Redis 세션 생성, CSRF 토큰 포함 |
| `test_login_wrong_password_generic_error` (line 25) | 잘못된 비밀번호 → 동일 에러 메시지 |
| `test_login_unknown_email_same_error_shape` (line 32) | 미존재 이메일 → 동일 에러(계정 열거 방지) |
| `test_lockout_after_5_failures` (line 38) | 5회 실패 후 LOCKED, 올바른 비밀번호도 거부 |
| `test_lock_expires_and_allows_login` (line 53) | 만료된 잠금 → 자동 복구 |
| `test_pending_user_cannot_login` (line 62) | PENDING 상태 로그인 거부 |
| `test_logout_destroys_session` (line 69) | 로그아웃 후 세션 없음 |
| `test_setup_password_with_valid_token` (line 77) | 유효 토큰으로 비밀번호 설정, 토큰 재사용 불가 |
| `test_setup_password_rejects_expired_token` (line 92) | 만료 토큰 거부 |
| `test_setup_password_rejects_weak_password` (line 102) | 10자 미만 비밀번호 거부 |
| `test_session_key_has_ttl` (line 112) | 세션 TTL 존재 확인(불멸 세션 방지) |
| `test_unknown_email_attempt_is_audited` (line 121) | 미존재 이메일 로그인 시도가 감사 로그에 기록됨 |
| `test_password_change_destroys_sessions_and_other_tokens` (line 132) | 비밀번호 변경 시 세션 파기 + 다른 토큰 무효화 |
| `test_login_rejected_for_disabled_account` (line 154) | DISABLED 계정 로그인 거부 |
| `test_login_rejected_for_deleted_account` (line 164) | DELETED 계정 로그인 거부 |

### 8-3. E2E 테스트 — 어드민 화면

`tests/e2e/test_accounts_admin.py`

| 테스트 | 검증 내용 |
|---|---|
| `test_admin_creates_manager_account_with_services` (line 14) | 어드민 화면에서 계정 생성 전체 플로우 |
| `test_create_account_page_requires_admin` (line 33) | SERVICE_MANAGER의 `/admin/users/new` 접근 403 |
| `test_manager_with_two_services_sees_both` (line 40) | 두 서비스 담당 매니저의 구독 스코프 확인 |
| `test_service_detail_assign_and_remove_manager` (line 62) | 서비스 상세 화면에서 담당자 할당·해제 |
| `test_account_detail_assign_remove_service` (line 84) | 계정 상세 화면에서 서비스 할당·해제 |
| `test_account_edit_updates_email_and_phone` (line 105) | 이메일·전화번호 수정 |
| `test_account_edit_duplicate_email_blocked` (line 116) | 중복 이메일 수정 차단 |
| `test_account_disable_and_delete` (line 128) | 비활성화 후 삭제 + 목록에서 숨김 확인 |
| `test_cannot_delete_self` (line 148) | 본인 삭제 거부 |

---

## 9. 유지보수 팁

### 9-1. 새 역할 추가

현재 역할은 `SYSTEM_ADMIN`, `SERVICE_MANAGER` 두 가지뿐이다.
새 역할을 추가하려면 아래 파일을 함께 수정해야 한다:

1. `app/models/enums.py:18` — `UserRole` 열거형에 값 추가
2. `app/services/accounts.py:98` — `create_account`의 역할 허용 목록에 추가
3. `app/admin/deps.py:86-102` — `require_role` 호출처에 새 역할 추가 여부 검토
4. 각 라우트의 `require_admin` / `require_any` 변경 또는 새 `require_role(...)` 호출 추가
5. `effective_service_ids`에서 `None` 반환 조건(`UserRole.SYSTEM_ADMIN` 비교) 검토

### 9-2. 비밀번호 정책 변경

최소 길이 수정:
- `app/services/auth.py:48` — `MIN_PASSWORD_LENGTH = 10` 값 변경

Argon2id 파라미터 변경(보안 강도 조정):
- `app/core/security.py:16` — `PasswordHasher()` 인수 추가
  예: `PasswordHasher(time_cost=4, memory_cost=131072)`  
  변경 시 기존 해시와의 호환성은 `argon2-cffi` 라이브러리가 자동 처리한다.

### 9-3. 로그인 잠금 임계·시간 변경

`app/services/auth.py:43-44`:
```python
MAX_FAILED_LOGINS = 5      # 잠금 임계 횟수
LOCK_DURATION = timedelta(minutes=15)  # 잠금 지속 시간
```
두 상수를 수정하면 된다. 운영 상황에 따라 `settings`로 외부 설정으로 옮기는 것도 고려할 수 있다(현재 코드 내 상수).

> 참고: 어드민 접속 IP 제한(`admin_allowed_ips`)은 `GlobalSettings`에서 설정하므로
> 코드 변경 없이 어드민 콘솔에서 즉시 적용된다. → [14. 전체 설정](14-global-settings.md)

### 9-4. 계정 복구 절차

| 상황 | 복구 방법 |
|---|---|
| 비밀번호 분실 / PENDING 상태 | SYSTEM_ADMIN이 `/admin/users/{id}` 에서 "비밀번호 재설정" 버튼 클릭 → 이메일로 새 링크 발송 |
| LOCKED 상태(즉시 해제) | 위와 동일한 비밀번호 재설정 → `setup_password` 완료 시 `failed_login_count=0`, `locked_until=None` 초기화 |
| LOCKED 상태(15분 대기) | 자동 해제 |
| DISABLED 상태 | SYSTEM_ADMIN이 `/admin/users/{id}` 에서 "활성화" 버튼 클릭 |
| DELETED 상태 | DB에는 남아 있으나 어드민 UI에서 복구 기능 없음. 필요 시 DB에서 직접 `status='ACTIVE'` 변경 |

### 9-5. 최초 SYSTEM_ADMIN 생성 (CLI)

신규 서버 셋업 시 어드민 콘솔 접근 전에 SYSTEM_ADMIN 계정이 필요하다.  
`app/services/auth.py:240-251` `create_system_admin()` 을 CLI 스크립트에서 호출한다:

```python
await auth_service.create_system_admin(db, email="admin@example.com", password="...")
```

비밀번호 최소 길이(`MIN_PASSWORD_LENGTH=10`)와 이메일 중복 조건을 검사한 뒤
`status=ACTIVE`, `role=SYSTEM_ADMIN` 으로 직접 생성된다(이메일 발송 없음).

### 9-6. effective_service_ids 캐시 금지

`effective_service_ids` 는 매 요청마다 DB를 조회한다(`app/services/accounts.py:33-49`).  
성능 최적화를 위해 세션에 캐시하면 안 된다:
서비스 담당 변경이 즉시 반영되지 않아 스코프 밖 데이터 접근 위험이 생긴다.
담당 서비스가 매우 많아 성능 문제가 생기면 인덱스 추가(`user_services.user_id`)를 먼저 검토한다.

### 9-7. 목록에서 DELETED 계정 표시

기본적으로 `users` 목록 쿼리에는 `User.status != UserStatus.DELETED` 조건이 붙어
삭제된 계정이 보이지 않는다. (`app/admin/routes/users.py:52-53`)

status 필터로 `DELETED`를 선택해도 이 조건이 먼저 적용되므로 어드민 UI에서는
삭제된 계정을 조회할 수 없다(의도된 동작). DB 직접 조회가 필요하면:

```sql
SELECT * FROM users WHERE status = 'DELETED';
```

---

## 상호 참조

| 문서 | 내용 |
|---|---|
| [03. 인증과 보안 공통](03-auth-and-security.md) | 세션 구조·CSRF·역할 검사·HMAC 공통 레이어 상세 |
| [02. 데이터베이스](02-database.md) | 전체 테이블 스키마·마이그레이션 |
| [09. 서비스 등록·키 발급·담당자](09-services-registry.md) | 서비스 생성 시 대표 담당자 배정, 서비스 측에서 담당자 할당 |
| [14. 전체 설정](14-global-settings.md) | 어드민 접속 IP 제한(`admin_allowed_ips`), 잠금 정책 |
