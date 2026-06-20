# 14. 전체 설정(재시도·어드민 IP·킬스위치)

> 상호참조: 인증/IP 검사/킬스위치 → [03. 인증과 보안 공통](03-auth-and-security.md) ·
> 갱신 재시도 배치 → [05. 구독 갱신·만료·재시도](05-subscription-renewal.md) ·
> DB 테이블 → [02. 데이터베이스](02-database.md)

---

## 1. 한 줄 요약

SYSTEM_ADMIN 전용 전체 설정 화면에서 **자동결제 재시도 정책**, **어드민 접속 허용 IP**, **결제서버 킬스위치** 세 가지를 DB 단일 행(`global_settings.id=1`)에 저장하고, 각 설정이 배치·어드민 IP 검사·외부 API 차단에 즉시 반영된다.

> **킬스위치 캐시(감사 Phase 3 — 성능 M4)**: `ensure_server_enabled`는 모든 외부 API
> 요청에서 실행되므로 결과를 Redis에 5초 TTL로 캐시한다(키 `cache:global:server_disabled`,
> ""=활성/비어있지 않으면 비활성 사유). 어드민에서 킬스위치를 전환하면 캐시를 즉시
> 무효화해 전파 지연이 없고, TTL은 다중 인스턴스 등 무효화가 닿지 않는 경우의
> 최대 지연 상한(5초)이다.

---

## 2. 언제 실행되나 (트리거)

어드민 콘솔에 SYSTEM_ADMIN 권한으로 로그인한 운영자가 `/admin/settings` 화면을 열거나 세 개의 폼 중 하나를 제출할 때 실행된다. 외부 API 호출로는 변경할 수 없다.

---

## 3. 요청 진입점

| 항목 | 내용 |
|------|------|
| 화면 조회 | `GET /admin/settings` |
| 재시도 설정 저장 | `POST /admin/settings/retry` |
| 어드민 IP 저장 | `POST /admin/settings/admin-ips` |
| 킬스위치 전환 | `POST /admin/settings/server-toggle` |
| 라우터 파일 | `app/admin/routes/settings.py` |
| 템플릿 파일 | `app/admin/templates/settings/index.html` |
| 인증 의존성 | `require_admin` (`app/admin/deps.py:100`) — SYSTEM_ADMIN 전용 |

### 권한 규칙

`require_admin`(`app/admin/deps.py:100`)은 `require_role(UserRole.SYSTEM_ADMIN)`의 축약이다.
SERVICE_MANAGER가 접근하면 403이 반환된다(`tests/e2e/test_admin_operations.py:140–147` 검증).

---

## 4. 단계별 처리 흐름

### 4-1. 화면 조회 (`GET /admin/settings`)

```
GET /admin/settings
     │
     ▼
[의존성] require_admin  app/admin/deps.py:100
  → require_user 호출(세션 쿠키 → Redis → DB 사용자 확인 + IP 검사)
  → role != SYSTEM_ADMIN이면 403
     │
     ▼
[핸들러] settings_page  app/admin/routes/settings.py:24–43
  → app_settings.get_global_settings(db)
     app/services/app_settings.py:18–30
     DB에서 id=1 행 조회. 없으면 기본값(retry_limit=4 등)으로 생성(get_or_create)
     │
     ▼
Jinja2 렌더: app/admin/templates/settings/index.html
  → gs(GlobalSettings 객체)를 템플릿에 전달 → 세 개 카드 출력
```

쿼리 파라미터 `?error=메시지` 가 있으면 오류 배너, `?saved=메시지` 가 있으면 완료 모달을 띄운다(`app/admin/routes/settings.py:38–43`).

### 4-2. 재시도 설정 저장 (`POST /admin/settings/retry`)

```
POST /admin/settings/retry
  폼 필드: csrf_token, retry_limit, retry_interval_hours, suspended_grace_days
     │
     ▼
validate_csrf(request, ctx)  app/admin/deps.py:105–110
  폼 hidden csrf_token 또는 X-CSRF-Token 헤더 검사
     │
     ▼
app_settings.update_retry_settings(db, ...)
  app/services/app_settings.py:33–56
  1. 값 범위 검사: retry_limit<0, retry_interval_hours<1, suspended_grace_days<0 → InputValidationError
  2. get_global_settings(db) 로 id=1 행 로드
  3. **변경 전 값 캡처** 후 gs.retry_limit / gs.retry_interval_hours / gs.suspended_grace_days 갱신
  4. record_audit: action="settings.retry_updated",
     detail={"old_retry_limit","new_retry_limit","old_interval_hours","new_interval_hours","old_grace_days","new_grace_days"}
     → 감사로그 상세에 "재시도 횟수 4 → 6" 처럼 변경 전·후가 표시된다 (commit은 아직 없음)
  5. await db.commit()
     │
     ▼
성공 → saved_redirect("/admin/settings", "저장되었습니다")  app/admin/routes/settings.py:72
  → /admin/settings?saved=저장되었습니다 303 리다이렉트 (완료 모달 트리거)
실패 → /admin/settings?error=<메시지> 303 리다이렉트
```

> **배치 적용 시점**: 저장 즉시 DB에 반영되며, 다음 갱신 배치(`process_due`) 실행 시
> `app/services/renewals.py:119`에서 `get_global_settings(db)`로 최신값을 읽어 사용한다.

### 4-3. 어드민 IP 저장 (`POST /admin/settings/admin-ips`)

```
POST /admin/settings/admin-ips
  폼 필드: csrf_token, admin_allowed_ips(줄바꿈 구분 IP 문자열)
     │
     ▼
validate_csrf  app/admin/deps.py:105–110
     │
     ▼
IP 목록 파싱  app/admin/routes/settings.py:93–97
  splitlines() → 빈 줄 제거 → stripped IP 리스트 생성
     │
     ▼
app_settings.update_admin_ips(db, ips=..., current_ip=get_client_ip(request, settings), ...)
  app/services/app_settings.py:59–90
  1. 각 IP를 ipaddress.ip_address()로 형식 검증 (IPv4/IPv6)
     유효하지 않으면 InputValidationError("유효하지 않은 IP: {ip}")
  2. lockout 방지: cleaned가 비어있지 않고 current_ip 미포함 시
     InputValidationError("현재 접속 IP를 포함해야 잠금을 피할 수 있습니다")  app/services/app_settings.py:82–83
  3. **변경 전 IP 목록 캡처(old_ips)** 후 gs.admin_allowed_ips = cleaned  (빈 리스트 = 제한 없음)
  4. record_audit: action="settings.admin_ips_updated", detail={"old_ips": [...], "new_ips": cleaned}
     → 감사로그 상세에 "허용 IP 10.0.0.1 → 10.0.0.1, 10.0.0.2" 처럼 변경 전·후가 표시된다
  5. await db.commit()
     │
     ▼
성공 → 303 /admin/settings?saved=저장되었습니다
실패 → 303 /admin/settings?error=<메시지>
```

> **IP 즉시 적용**: 저장 직후부터 `require_user`(`app/admin/deps.py:79–81`)가 모든
> 어드민 요청마다 `gs.admin_allowed_ips`를 확인한다. 비어있지 않은 목록에 접속 IP가 없으면
> `PermissionDeniedError(403)`를 반환한다.

### 4-4. 킬스위치 전환 (`POST /admin/settings/server-toggle`)

```
POST /admin/settings/server-toggle
  폼 필드: csrf_token, disabled("on"|"true"|"1"=비활성화, 나머지=활성화),
           reason(비활성화 시 필수), password(본인 비밀번호)
     │
     ▼
validate_csrf  app/admin/deps.py:105–110
     │
     ▼
disabled = str(form.get("disabled","")) in ("on","true","1")
  app/admin/routes/settings.py:132
     │
     ▼
app_settings.set_server_disabled(db, disabled=..., reason=..., actor_user=ctx.user, password=...)
  app/services/app_settings.py:105–134
  1. verify_password(password, actor_user.password_hash)
     app/core/security.py:87–95  (Argon2id 비교)
     불일치 → AuthenticationError("비밀번호가 일치하지 않습니다")
  2. disabled=True인데 reason이 비어있으면
     InputValidationError("비활성화 사유를 입력해야 합니다")
  3. gs.server_disabled = disabled
     gs.disabled_reason = reason.strip() if disabled else None
     gs.disabled_at = utcnow() if disabled else None
     gs.disabled_by = actor_user.id if disabled else None
  4. record_audit:
     action = "server.disabled" (비활성화) 또는 "server.enabled" (활성화)
     detail = {"reason": gs.disabled_reason}
  5. await db.commit()
     │
     ▼
성공 → 303 /admin/settings?saved=저장되었습니다
실패 → 303 /admin/settings?error=<메시지>
```

> **외부 API 즉시 차단**: `server_disabled=True`로 저장된 순간부터 외부 API 진입 직후
> `authenticate_service`(`app/api/deps.py:86`)가 `ensure_server_enabled(db)`를 호출하고,
> `gs.server_disabled==True`이면 `ServerDisabledError`(HTTP 503)를 발생시킨다.
> 어드민 라우트는 `authenticate_service`를 거치지 않으므로 킬스위치 영향을 받지 않는다.

---

## 5. 사용하는 DB 테이블·컬럼

### global_settings 테이블 (`app/models/global_settings.py:16–29`)

DB에 항상 `id=1` **단일 행만 존재**한다. 새로 생성하거나 삭제하지 않고 이 행의 컬럼만 갱신한다.

| 컬럼 | 타입 | 기본값 | 역할 |
|------|------|--------|------|
| `id` | Integer PK | `1` | 싱글톤 행 식별자 (항상 1) |
| `retry_limit` | Integer | `4` | 자동결제 실패 최대 재시도 횟수 |
| `retry_interval_hours` | Integer | `12` | 재시도 간격(시간) |
| `suspended_grace_days` | Integer | `30` | SUSPENDED 만료 유예(일) |
| `admin_allowed_ips` | JSONB (`[]`) | `[]` | 어드민 접속 허용 IP 목록 (빈 목록=제한 없음) |
| `server_disabled` | Boolean | `False` | 결제서버 킬스위치 (True=503 차단) |
| `disabled_reason` | String(500) | `NULL` | 비활성화 사유 (외부 API 오류 응답에 포함) |
| `disabled_at` | DateTime(TZ) | `NULL` | 비활성화 시각 (UTC) |
| `disabled_by` | UUID | `NULL` | 비활성화한 관리자 user id |

### audit_logs 테이블 (`app/models/audit_log.py:17–35`)

설정 변경 시 아래 액션 코드로 감사 로그가 기록된다.

| action | 발생 시점 |
|--------|---------|
| `settings.retry_updated` | 재시도 설정 저장 |
| `settings.admin_ips_updated` | 어드민 IP 저장 |
| `server.disabled` | 킬스위치 ON |
| `server.enabled` | 킬스위치 OFF(복구) |

---

## 6. 상태 전이

킬스위치는 이진 상태다.

```
server_disabled=False   ← 정상 운영(기본)
       │
       │  POST /settings/server-toggle (disabled=on)
       │  조건: 비밀번호 일치 + 사유 입력
       ▼
server_disabled=True    ← 비활성화(점검 중)
  외부 API: 503 SERVER_DISABLED
  어드민: 정상 접근 가능
       │
       │  POST /settings/server-toggle (disabled 비움)
       │  조건: 비밀번호 일치
       ▼
server_disabled=False   ← 정상 운영 복구
```

---

## 7. 예외·엣지 케이스 / 에러 응답

### 7-1. 재시도 설정

| 조건 | 오류 | 처리 |
|------|------|------|
| 숫자가 아닌 값 입력 | `ValueError` | "숫자를 입력하세요" 오류 리다이렉트 (`app/admin/routes/settings.py:69`) |
| `retry_limit < 0` | `InputValidationError` | "재시도 설정 값이 올바르지 않습니다" |
| `retry_interval_hours < 1` | `InputValidationError` | 위와 동일 |
| `suspended_grace_days < 0` | `InputValidationError` | 위와 동일 |

범위 검사: `app/services/app_settings.py:45–46`

### 7-2. 어드민 IP

| 조건 | 오류 |
|------|------|
| IP 형식 오류 | `InputValidationError("유효하지 않은 IP: {ip}")` (`app/services/app_settings.py:79`) |
| 목록이 비어있지 않은데 현재 접속 IP 미포함 | `InputValidationError("현재 접속 IP를 포함해야 잠금을 피할 수 있습니다")` (`app/services/app_settings.py:83`) |
| 목록을 완전히 비워서 저장 | 성공 — 이후 모든 IP에서 어드민 접속 허용 |

> **lockout 방지 원리**: 현재 접속 IP가 새 목록에 없으면 저장하는 즉시 본인도 접속
> 불가능해진다. 서비스 레이어가 이를 차단한다(`app/services/app_settings.py:82–83`).
> 테스트 클라이언트 IP는 `127.0.0.1`이므로 테스트 코드에서는 이 값을 포함해야 한다
> (`tests/e2e/test_admin_operations.py:95`).

### 7-3. 킬스위치

| 조건 | 오류 |
|------|------|
| 비밀번호 불일치 | `AuthenticationError("비밀번호가 일치하지 않습니다")` → 303 오류 리다이렉트 |
| `disabled=True`이고 `reason`이 비거나 공백만 있음 | `InputValidationError("비활성화 사유를 입력해야 합니다")` |
| `disabled_reason`이 없는 상태로 이미 킬스위치 ON → 외부 API 응답 | `"서비스 점검 중입니다"` 기본 안내 문구 사용 (`app/services/app_settings.py:102`) |

### 7-4. 화면 동작 — 버튼이 곧 동작

킬스위치 카드에는 체크박스가 없다. 현재 상태에 따라 버튼과 hidden 필드가 달라진다
(`app/admin/templates/settings/index.html:121–149`):

| 현재 상태 | 버튼 라벨 | hidden `disabled` 값 | 확인 다이얼로그 |
|---------|-----------|---------------------|---------------|
| 정상 운영 | "비활성화" (danger) | `on` | 있음 (`data-confirm`) |
| 비활성화 | "활성화(복구)" (primary) | `""` (빈 문자열) | 없음 |

버튼을 클릭하면 현재 상태의 **반대** 상태로 전환하는 hidden 값이 제출된다. 체크박스 방식에서는 의도치 않은 상태 변경이 생길 수 있어 이 방식을 채택했다.

---

## 8. 관련 테스트

### E2E 테스트

| 파일 | 테스트 함수 | 검증 내용 |
|------|-----------|---------|
| `tests/e2e/test_admin_operations.py:26` | `test_settings_page_and_forms` | 설정 화면 조회 + 재시도 저장 + 킬스위치 ON |
| `tests/e2e/test_admin_operations.py:85` | `test_settings_admin_ips_form` | 현재 IP 포함/제외 저장, lockout 거부 |
| `tests/e2e/test_admin_operations.py:140` | `test_settings_page_forbidden_for_manager` | SERVICE_MANAGER → 403 |
| `tests/e2e/test_admin_operations.py:149` | `test_admin_ip_restriction` | 허용 IP 제한 → 403, 빈 목록 복원 → 200 |
| `tests/e2e/test_killswitch.py:11` | `test_external_api_returns_503_when_server_disabled` | 킬스위치 ON → 외부 API 503 + SERVER_DISABLED |
| `tests/e2e/test_killswitch.py:33` | `test_admin_page_unaffected_when_server_disabled` | 킬스위치 ON → 어드민 정상(200) |

### 통합 테스트

| 파일 | 테스트 함수 | 검증 내용 |
|------|-----------|---------|
| `tests/integration/test_app_settings.py:8` | `test_get_global_settings_creates_singleton` | get_or_create, id=1 기본값 |
| `tests/integration/test_app_settings.py:17` | `test_update_retry_settings` | 재시도 설정 DB 갱신 |
| `tests/integration/test_app_settings.py:27` | `test_update_admin_ips_requires_current_ip` | lockout 방지 로직 |
| `tests/integration/test_app_settings.py:39` | `test_set_server_disabled_password` | 비밀번호 검증, 킬스위치 ON |
| `tests/integration/test_app_settings.py:51` | `test_set_server_disabled_reason_required` | 사유 필수 검사 |
| `tests/integration/test_killswitch.py:13` | `test_ensure_server_enabled_raises_when_disabled` | 503 + SERVER_DISABLED 코드 |
| `tests/integration/test_killswitch.py:33` | `test_ensure_server_enabled_passes_when_active` | 활성 상태 → 예외 없음 |
| `tests/integration/test_killswitch.py:39` | `test_ensure_server_enabled_uses_default_message_when_no_reason` | 사유 없을 때 기본 문구 |

테스트 실행:
```bash
uv run pytest tests/e2e/test_admin_operations.py::test_settings_page_and_forms -v
uv run pytest tests/e2e/test_killswitch.py tests/integration/test_app_settings.py tests/integration/test_killswitch.py -v
```

---

## 9. 유지보수 팁

### 9-1. 새 전역 설정 컬럼을 추가하려면

1. `app/models/global_settings.py`에 컬럼 추가 (기본값 반드시 설정)
2. Alembic 마이그레이션 생성: `uv run alembic revision --autogenerate -m "add new_setting to global_settings"`
3. `app/services/app_settings.py`에 갱신 함수 추가 (또는 기존 함수 확장)
4. `app/admin/routes/settings.py`에 새 POST 핸들러 추가
5. `app/admin/templates/settings/index.html`에 폼 카드 추가

### 9-2. 재시도 횟수·간격을 바꾸는 방법

화면에서 직접 변경한다: 어드민 콘솔 → 전체 설정 → "자동결제 재시도" 카드.
저장 즉시 다음 배치 실행(`process_due`, `app/services/renewals.py:96`)부터 반영된다.
코드 수정이 필요 없고 서버 재시작도 불필요하다.

기본값을 변경하고 싶다면 `app/models/global_settings.py:22–24`의 `default`/`server_default` 값을 수정한다.

### 9-3. 킬스위치 동작 원리 재확인

킬스위치가 외부 API를 차단하는 경로:

```
외부 API 요청
  → authenticate_service  app/api/deps.py:84–86
  → ensure_server_enabled(db)  app/services/app_settings.py:93–102
  → gs.server_disabled==True → ServerDisabledError(503)
```

어드민 라우트는 `authenticate_service`를 쓰지 않고 `require_user/require_admin`을 쓰므로 `ensure_server_enabled`가 호출되지 않는다. 따라서 킬스위치 ON 상태에서도 어드민에서 복구 작업을 할 수 있다.

`ServerDisabledError`의 오류 코드와 HTTP 상태: `app/core/errors.py:100–109`
```python
code = "SERVER_DISABLED"
http_status = 503
```

### 9-4. get_or_create 패턴 — id=1 행이 없을 때

프로덕션 초기 또는 테스트 환경에서 `global_settings` 행이 없을 수 있다.
`get_global_settings`(`app/services/app_settings.py:18–30`)는 `db.get(GlobalSettings, 1)`로
행을 조회하고, `None`이면 `GlobalSettings(id=1)`을 생성·커밋한다.
모든 설정 접근이 이 함수를 통하므로 직접 행을 INSERT할 필요는 없다.

### 9-5. 어드민 IP 잠금 해제 (비상 상황)

현재 IP가 허용 목록에 없어 어드민 접속이 막혔을 때:
- DB에 직접 접근해 `global_settings` 행의 `admin_allowed_ips`를 `'[]'`로 업데이트하면 제한이 해제된다.
- 운영 DB 직접 접근이 불가능하면 애플리케이션을 재시작하지 말고 DB 접근 권한이 있는 인원에게 요청한다.
- 제한을 해제한 뒤 즉시 올바른 IP 목록으로 다시 저장할 것.
