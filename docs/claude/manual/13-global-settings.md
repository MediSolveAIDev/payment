# 13. 전역 설정 (GlobalSettings) — 재시도 · 어드민 IP · 킬스위치

> **런타임 변경 가능한 전역 운영 설정**이다. `.env`(`Settings`)와 달리 서버를 재시작하지 않고도
> Admin 화면에서 즉시 값을 바꿀 수 있다. 자동결제 재시도 횟수·간격·유예, 어드민 접속 IP 제한,
> 결제서버 킬스위치가 이 단일 테이블 한 행에 모인다.
>
> 선행: [00-overview.md](00-overview.md), [08-api-auth.md](08-api-auth.md)(외부 API 인증).

---

## 0. 한눈에 보기

| 설정 그룹 | 관여 코드 | 적용 지점 |
|---|---|---|
| 자동결제 재시도 (`retry_*`, `suspended_grace_days`) | `app/services/renewals.py` `process_due` → `_Cfg(gs)` | 갱신 배치 매 실행마다 DB 로드 |
| 어드민 접속 IP (`admin_allowed_ips`) | `app/admin/deps.py` `require_user` | 모든 Admin 요청 — 인증 직후 |
| 결제서버 킬스위치 (`server_disabled`, `disabled_reason`) | `app/api/deps.py` `authenticate_service` → `ensure_server_enabled` | 외부 API 진입 직후 |

관련 파일:
- 모델: `app/models/global_settings.py`
- 헬퍼: `app/services/app_settings.py`
- 어드민 라우트: `app/admin/routes/settings.py`
- 어드민 게이트: `app/admin/deps.py` `require_user`
- 외부 API 게이트: `app/api/deps.py` `authenticate_service`
- 예외: `app/core/errors.py` `ServerDisabledError`
- 마이그레이션: `alembic/versions/e5f6a7b8c9d0_global_settings.py`

---

## 1. 데이터 모델 — `GlobalSettings` (`app/models/global_settings.py`)

`global_settings` 테이블에는 **id=1 단일 행만 존재**한다. 행이 없으면 헬퍼의
`get_or_create`가 기본값으로 자동 생성한다.

| 컬럼 | 타입 | 기본값 | 의미 |
|---|---|---|---|
| `id` | Integer PK | 1 (싱글톤) | 항상 1로 고정 |
| `retry_limit` | Integer | 4 | 자동결제 실패 재시도 최대 횟수 |
| `retry_interval_hours` | Integer | 12 | 재시도 간격(시간) |
| `suspended_grace_days` | Integer | 30 | SUSPENDED → EXPIRED 유예 일수 |
| `admin_allowed_ips` | JSONB (list) | `[]` | 어드민 접속 허용 IP 목록. **빈 배열 = 제한 없음** |
| `server_disabled` | Boolean | False | 킬스위치: True면 외부 API 전체 차단 |
| `disabled_reason` | String(500), nullable | null | 비활성화 사유(503 응답에 포함) |
| `disabled_at` | DateTime(tz), nullable | null | 비활성화 시각(UTC) |
| `disabled_by` | UUID, nullable | null | 비활성화한 관리자 user id |

`TimestampMixin`이 `created_at`/`updated_at`을 자동 추가한다.

---

## 2. 헬퍼 함수 — `app/services/app_settings.py`

모든 함수는 id=1 단일 행에만 접근한다.

### `get_global_settings(db)` — 단일 행 get_or_create

```python
gs = await db.get(GlobalSettings, 1)
if gs is None:
    gs = GlobalSettings(id=1)   # 기본값: retry 4/12/30, 제한 없음, 활성
    db.add(gs); await db.commit(); await db.refresh(gs)
return gs
```

행이 없으면 기본값으로 생성한다. 이 함수는 어드민 게이트·외부 API 게이트·갱신 배치
세 곳에서 공통으로 호출하는 단일 진실 공급원이다.

### `update_retry_settings(db, *, retry_limit, retry_interval_hours, suspended_grace_days, actor_user_id)`

자동결제 재시도 설정 변경. **다음 배치 실행부터 즉시 적용**된다(배치가 매번 DB를 읽으므로).

검증:
- `retry_limit < 0` → `InputValidationError`
- `retry_interval_hours < 1` → `InputValidationError`
- `suspended_grace_days < 0` → `InputValidationError`

감사 액션: `settings.retry_updated`, `detail={"retry_limit": ..., "interval_hours": ..., "grace_days": ...}`

### `update_admin_ips(db, *, ips, current_ip, actor_user_id)`

어드민 접속 허용 IP 목록 변경. 빈 목록으로 저장하면 제한이 해제된다.

**lockout 방지 규칙**: 목록이 비어있지 않을 때 현재 접속 IP가 목록에 포함되지 않으면
`InputValidationError("현재 접속 IP를 포함해야 잠금을 피할 수 있습니다")` — 자기 자신을
잠그는 실수를 막는다.

IP 형식(IPv4/IPv6)은 `ipaddress.ip_address()`로 검증한다.

감사 액션: `settings.admin_ips_updated`, `detail={"count": <허용 IP 수>}`

### `ensure_server_enabled(db)` — 킬스위치 게이트 헬퍼

```python
gs = await get_global_settings(db)
if gs.server_disabled:
    raise ServerDisabledError(gs.disabled_reason or "서비스 점검 중입니다")
```

외부 API(`authenticate_service`)의 **진입 직후 첫 줄**에서 호출된다.
어드민 라우트에는 사용하지 않는다(어드민은 킬스위치 영향을 받지 않는다).

### `set_server_disabled(db, *, disabled, reason, actor_user, password)`

결제서버 킬스위치 전환. 중요한 운영 조작이므로 **본인 비밀번호 재확인**이 필수다.

```python
# app/services/app_settings.py
if not verify_password(password, actor_user.password_hash):
    raise AuthenticationError("비밀번호가 일치하지 않습니다")
if disabled and not (reason or "").strip():
    raise InputValidationError("비활성화 사유를 입력해야 합니다")
```

- `disabled=True`: `server_disabled=True`, `disabled_reason`, `disabled_at=utcnow()`, `disabled_by=actor_user.id` 기록.
- `disabled=False`: 위 필드를 모두 None으로 초기화.

감사 액션: `server.disabled` 또는 `server.enabled`, `detail={"reason": ...}`

> **`verify_password` 인자 순서**: `verify_password(평문, 해시)` — `core/security.py`의 실제 시그니처.

---

## 3. 적용 지점

### (1) 갱신 배치 — `process_due` (`app/services/renewals.py`)

배치 1회 실행 맨 앞에서 DB의 GlobalSettings를 읽어 `_Cfg(gs)` 설정 객체를 만든다:

```python
async with session_factory() as db:
    gs = await get_global_settings(db)   # 재시도 한계·간격·유예를 DB 전역설정에서 로드
    cfg = _Cfg(gs)
    canceled_due = ...
    suspended_due = ...   # cfg.suspended_grace 사용
    renew_due = ...       # next_billing_at 기반
    non_renewing_due = ...  # auto_renew=False 구독 기간 만료 대상
```

`_Cfg`는 `retry_limit` / `retry_interval`(`timedelta`) / `suspended_grace`(`timedelta`)를 담는
컨테이너다. GlobalSettings와 Settings 모두 속성명이 같으므로 동일 클래스로 처리된다.

Admin에서 `retry_limit`를 변경하면 **다음 배치 실행부터 바로 적용**된다
(배치가 호출될 때마다 DB를 읽기 때문).

### (2) 어드민 접속 IP 제한 — `require_user` (`app/admin/deps.py`)

세션·사용자 검증 후, `AdminContext` 반환 직전:

```python
# app/admin/deps.py
gs = await get_global_settings(db)
if gs.admin_allowed_ips and get_client_ip(request, settings) not in gs.admin_allowed_ips:
    raise PermissionDeniedError("허용되지 않은 IP입니다")
```

`admin_allowed_ips`가 빈 배열이면 제한 없음(기본 동작). 목록에 IP가 1개 이상 있을 때만
접속 IP를 검사한다. 이 검사는 `require_admin` / `require_any` 모두를 포괄한다
(둘 다 `require_user`를 호출하므로).

### (3) 결제서버 킬스위치 — `authenticate_service` (`app/api/deps.py`)

```python
# app/api/deps.py
await ensure_server_enabled(db)   # 킬스위치: 비활성화면 503+사유로 즉시 차단
api_key = request.headers.get("x-service-key", "")
# ... 이후 6단계 인증
```

`ensure_server_enabled`가 `ServerDisabledError`(503, `code="SERVER_DISABLED"`)를 발생시키면
나머지 인증 단계(API 키·IP·HMAC 등)는 실행되지 않는다. 외부 서비스는 503 응답의 `message`로
비활성화 사유를 확인할 수 있다.

어드민 라우트는 `authenticate_service`를 거치지 않으므로 킬스위치와 무관하다.

---

## 4. 어드민 전체설정 화면 — `/admin/settings`

`app/admin/routes/settings.py`에 정의된 `SYSTEM_ADMIN` 전용 화면이다.

| HTTP | URL | 함수 | 동작 |
|---|---|---|---|
| GET | `/admin/settings` | `settings_page` | 현재 GlobalSettings 값 렌더 |
| POST | `/admin/settings/retry` | `settings_retry` | 재시도 설정 저장 |
| POST | `/admin/settings/admin-ips` | `settings_admin_ips` | 어드민 IP 목록 저장 |
| POST | `/admin/settings/server-toggle` | `settings_server_toggle` | 킬스위치 전환 |

모든 POST는 `validate_csrf` + `require_admin`을 통과해야 한다.

**`settings_admin_ips`**: 폼 textarea(`admin_allowed_ips`)에서 줄바꿈으로 구분된 IP를 파싱해
`update_admin_ips`에 전달한다. `get_client_ip(request, settings)`로 현재 접속 IP를 구해 lockout 검사.

**`settings_server_toggle`**: 폼 `disabled` 체크박스(`on`/`true`/`1` = True), `reason`(사유),
`password`(본인 비밀번호)를 받아 `set_server_disabled` 호출. 비밀번호 불일치나 사유 미입력은
`/admin/settings?error=<메시지>`로 리다이렉트한다.

성공 시 모든 POST가 `/admin/settings?ok=1`로 303 리다이렉트한다.

---

## 5. 예외 — `ServerDisabledError` (`app/core/errors.py`)

```python
class ServerDisabledError(DomainError):
    """결제서버 전체 비활성화(킬스위치) 상태 — 외부 API 차단 (HTTP 503)."""
    code = "SERVER_DISABLED"
    http_status = 503
```

JSON 응답 형식(다른 도메인 예외와 동일):
```json
{ "error": { "code": "SERVER_DISABLED", "message": "<disabled_reason 또는 기본 안내>" } }
```

---

## 6. 감사 액션

| 액션 | 한글 라벨 | 발생 시점 |
|---|---|---|
| `settings.retry_updated` | 재시도 설정 변경 | `update_retry_settings` |
| `settings.admin_ips_updated` | 어드민 IP 변경 | `update_admin_ips` |
| `server.disabled` | 결제서버 비활성화 | `set_server_disabled(disabled=True)` |
| `server.enabled` | 결제서버 활성화 | `set_server_disabled(disabled=False)` |

`target_type="global_settings"`, `target_id="1"` 고정.

---

## 7. 마이그레이션

`alembic/versions/e5f6a7b8c9d0_global_settings.py`

- `down_revision`: `d4e5f6a7b8c9`(서비스 취소 정책 추가 마이그레이션)
- `upgrade()`: `global_settings` 테이블 생성(id, retry_limit, retry_interval_hours,
  suspended_grace_days, admin_allowed_ips, server_disabled, disabled_reason, disabled_at,
  disabled_by, created_at, updated_at).
- `downgrade()`: `global_settings` 테이블 삭제.

---

## 8. 엣지 케이스 · 주의사항

| 상황 | 처리 |
|---|---|
| 첫 요청 시 행이 없음 | `get_or_create`가 기본값(retry 4/12/30, 제한 없음, 활성)으로 자동 생성 |
| 빈 `admin_allowed_ips`로 저장 | 제한 해제 — 어드민 접속 IP 검사 건너뜀 |
| lockout: 현재 IP가 빠진 목록 저장 시도 | `InputValidationError` — 저장 거부, 기존 목록 유지 |
| 킬스위치 ON 상태에서 외부 API 요청 | 503 + disabled_reason 반환 (어드민은 정상) |
| 비밀번호 틀린 킬스위치 전환 | `AuthenticationError(401)` → 라우트가 `/admin/settings?error=` 리다이렉트 |
| 비활성화 사유 없이 `disabled=True` | `InputValidationError(422)` → 라우트가 error 리다이렉트 |

---

## 9. 유지보수 체크리스트

1. **새 전역 설정 추가 시**: `GlobalSettings` 컬럼 → Alembic 마이그레이션 → 헬퍼 함수(update_*) → 라우트 폼 → 적용 지점 코드 → 이 문서 갱신.
2. **어드민 IP 변경 후 자신이 잠겼을 때**: DB에서 직접 `UPDATE global_settings SET admin_allowed_ips='[]' WHERE id=1;` 실행으로 초기화.
3. **킬스위치 남용 방지**: 비밀번호 재확인 + 사유 필수 + 감사로그. 어드민 전원이 로그를 볼 수 있다.
4. **갱신 배치와의 타이밍**: `retry_limit`를 낮추면 다음 배치 실행부터 즉시 적용된다. 이미 PAST_DUE인 구독에도 소급 적용되므로 의도치 않은 SUSPENDED 전환이 발생할 수 있다.
5. **`verify_password` 인자 순서 고정**: `verify_password(평문, 해시)` — 반대로 넘기면 항상 인증 실패.
