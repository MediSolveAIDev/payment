# 10. 전체 설정 (재시도 정책 · 어드민 IP · 킬스위치)

> **대상 독자**: 어드민 콘솔을 사용하는 운영자(화면 조작 방법) + 내부 구현을 파악해야 하는 개발자(라우트·서비스·모델 위치).
>
> GlobalSettings 모델·마이그레이션·감사 로그 상세는 [../14-global-settings.md](../14-global-settings.md)를 참고하세요.
> 어드민 IP 제한의 세션·미들웨어 적용 흐름은 [../03-auth-and-security.md](../03-auth-and-security.md)를 참고하세요.

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

| 화면/동작 | URL | 메서드 | 접근 권한 |
|-----------|-----|--------|----------|
| 전체 설정 화면 렌더 | `/admin/settings` | GET | `SYSTEM_ADMIN` |
| 재시도 정책 저장 | `/admin/settings/retry` | POST | `SYSTEM_ADMIN` |
| 어드민 허용 IP 저장 | `/admin/settings/admin-ips` | POST | `SYSTEM_ADMIN` |
| 킬스위치 전환(활성/비활성) | `/admin/settings/server-toggle` | POST | `SYSTEM_ADMIN` |

- **접근 경로**: 좌측 사이드바 → **전체 설정** 메뉴 클릭(또는 URL 직접 입력).
- **권한**: 모든 엔드포인트는 `require_admin`(= `SYSTEM_ADMIN` 전용)으로 보호됩니다. `SERVICE_MANAGER` 역할은 이 화면에 접근할 수 없으며 403으로 차단됩니다.
- 이 화면에서 변경한 설정은 **DB에 저장되어 런타임에 즉시 반영**됩니다(캐시 없음).

---

## 2. 화면 구성

템플릿: `app/admin/templates/settings/index.html`

화면 제목 **전체 설정** 아래 세 개의 카드가 2열 그리드로 배치됩니다. 세 번째 카드(킬스위치)는 `grid-column:1/-1`로 전체 폭을 차지합니다.

### 카드 1 — 자동결제 재시도

| 필드 | 입력 타입 | 현재 값 소스 |
|------|----------|------------|
| 최대 재시도 횟수 | `number` (min=0, max=99) | `gs.retry_limit` |
| 재시도 간격(시간) | `number` (min=1) | `gs.retry_interval_hours` |
| SUSPENDED 만료 유예(일) | `number` (min=0) | `gs.suspended_grace_days` |

- **저장** 버튼(`btn-primary`): `POST /admin/settings/retry` 제출.

### 카드 2 — 어드민 접속 허용 IP

- 저장된 IP가 옥텟 4칸 행으로 펼쳐집니다. 저장된 IP가 없으면 빈 행 1개가 기본으로 표시됩니다.
- 각 옥텟 칸은 `inputmode="numeric"` / `maxlength="3"`.
- **삭제** 버튼(`data-ip-del`): 해당 행 제거.
- **IP 추가** 버튼(`data-ip-add`): 빈 옥텟 행 1개 추가.
- **저장** 버튼(`btn-primary`): `POST /admin/settings/admin-ips` 제출.
- 폼에는 `data-ip-form` / `data-ip-target="admin_allowed_ips"` / `data-ip-allow-empty` 속성이 있습니다. `admin.js`가 submit 직전 모든 행의 옥텟을 조합하여 hidden 필드 `admin_allowed_ips`에 줄바꿈(`\n`) 구분 문자열로 채웁니다.
- **목록이 비어 있으면 모든 IP에서 접속 가능**합니다(`data-ip-allow-empty`로 빈 제출 허용).

### 카드 3 — 결제서버 킬스위치

#### 현재 상태 패널

| 상태 | 배지 | 추가 표시 |
|------|------|---------|
| 정상 운영 | `badge-ACTIVE` "정상 운영" | 없음 |
| 비활성화(점검 중) | `badge-INACTIVE` "비활성화(점검 중)" | 비활성화 사유(`gs.disabled_reason`) |

#### 폼 필드

- `<input type="hidden" name="disabled">`: 현재 상태에 따라 서버가 목표 상태를 주입합니다.
  - 현재 정상이면 `value="on"` → 제출 시 **비활성화**.
  - 현재 비활성이면 `value=""` → 제출 시 **활성화**.
- **비활성화 사유** 입력 (`name="reason"`, `maxlength="500"`): **정상 운영 상태일 때만** 표시됩니다. 비활성화 시 필수.
- **본인 비밀번호 확인** 입력 (`name="password"`, `type="password"`): 활성화·비활성화 모두에서 항상 표시됩니다.
- **버튼**: 현재 정상이면 `btn-danger` "비활성화", 현재 비활성이면 `btn-primary` "활성화(복구)".
- 비활성화 방향(정상 → 비활성) 시 폼에 `data-confirm` / `data-confirm-title` / `data-confirm-ok` 속성이 있어 `admin.js`가 제출 전 **확인 모달**을 표시합니다.

---

## 3. 할 수 있는 동작

### 3-1. 재시도 정책 변경

1. **카드 1**에서 세 개의 숫자 필드를 원하는 값으로 수정합니다.
2. **저장** 버튼을 클릭하면 `POST /admin/settings/retry`가 전송됩니다.
3. 성공 시 `/admin/settings?saved=저장되었습니다`로 303 리다이렉트 → **저장 완료 모달**이 표시됩니다.
4. 실패 시 `/admin/settings?error=<메시지>`로 리다이렉트 → 화면 상단 **오류 배너**가 표시됩니다.

| 입력 오류 | 화면에 표시되는 메시지 |
|-----------|---------------------|
| 재시도 횟수 < 0 또는 간격 < 1 또는 유예일 < 0 | "재시도 설정 값이 올바르지 않습니다" |
| 숫자가 아닌 입력 | "숫자를 입력하세요" |

> 변경된 값은 **다음 구독 갱신 배치 실행 시부터** 적용됩니다. 현재 처리 중인 배치에는 소급되지 않습니다.

**각 필드의 의미**

| 필드 | 의미 |
|------|------|
| 최대 재시도 횟수 (`retry_limit`) | 구독 갱신 결제 실패 시 최대 몇 번 재시도할지. 0이면 재시도 없음. |
| 재시도 간격(`retry_interval_hours`) | 재시도 사이의 간격(시간 단위). 1 이상 필수. |
| SUSPENDED 만료 유예(`suspended_grace_days`) | 모든 재시도 소진 후 구독이 SUSPENDED 상태로 전환될 때 실제 만료까지 유예되는 일수. 0이면 즉시 만료. |

### 3-2. 어드민 접속 허용 IP 변경

1. **카드 2**의 행에서 허용할 IP를 옥텟별로 입력합니다.
2. IP를 추가하려면 **IP 추가** 버튼을 클릭해 빈 행을 만들고 입력합니다.
3. 특정 행을 제거하려면 해당 행의 **삭제** 버튼을 클릭합니다.
4. **저장** 버튼을 클릭하면 `POST /admin/settings/admin-ips`가 전송됩니다.
5. 성공 시 저장 완료 모달, 실패 시 오류 배너가 표시됩니다.

**빈 목록 저장 시**: 어드민 IP 제한이 해제되어 모든 IP에서 접속 가능해집니다.

| 입력 오류 | 화면에 표시되는 메시지 |
|-----------|---------------------|
| 유효하지 않은 IP 형식 | "유효하지 않은 IP: <ip>" |
| 현재 접속 IP가 목록에 없음 | "현재 접속 IP를 포함해야 잠금을 피할 수 있습니다" |

> **중요**: 반드시 현재 접속 중인 본인의 IP를 목록에 포함해야 합니다. 포함하지 않으면 서버가 저장을 거부합니다. 이 검사는 운영자 전원이 어드민에서 잠기는 사고를 방지하는 lockout 보호 장치입니다.

### 3-3. 킬스위치 전환

#### 결제서버 비활성화 (정상 → 비활성)

1. **카드 3** 현재 상태가 **정상 운영**인 상태에서 **비활성화 사유**를 입력합니다(예: "정기 점검 2026-06-09 00:00~06:00").
2. **본인 비밀번호**를 입력합니다.
3. 빨간 **비활성화** 버튼을 클릭합니다.
4. `admin.js`가 **확인 모달** ("결제서버를 비활성화할까요?")을 표시합니다. **비활성화** 버튼을 클릭해야 실제 제출됩니다.
5. 성공 시 저장 완료 모달. 이후 화면을 새로고침하면 상태 패널이 "비활성화(점검 중)"와 사유로 바뀝니다.

#### 결제서버 활성화 (비활성 → 정상 복구)

1. **카드 3** 현재 상태가 **비활성화(점검 중)**인 상태에서 **본인 비밀번호**를 입력합니다(사유 입력 불필요).
2. 파란 **활성화(복구)** 버튼을 클릭합니다. 확인 모달 없이 바로 제출됩니다.
3. 성공 시 저장 완료 모달.

| 입력 오류 | 화면에 표시되는 메시지 |
|-----------|---------------------|
| 비밀번호 불일치 | "비밀번호가 일치하지 않습니다" |
| 비활성화 시 사유가 빈 문자열 | "비활성화 사유를 입력해야 합니다" |

> **비활성화 효과**: `GlobalSettings.server_disabled=True`가 저장된 순간부터 외부 API(`authenticate_service` 진입 시 `ensure_server_enabled` 게이트 호출) 전체가 HTTP 503을 반환합니다. **어드민 화면(`/admin`)은 킬스위치 영향을 받지 않습니다.**

---

## 4. 개발 참조

### 4-1. 라우트 함수

모든 설정 라우트는 `app/admin/routes/settings.py`에 있습니다.

| 기능 | 함수 | 파일:줄 |
|------|------|---------|
| 전체 설정 화면 렌더 | `settings_page` | `app/admin/routes/settings.py:25` |
| 재시도 정책 저장 | `settings_retry` | `app/admin/routes/settings.py:47` |
| 어드민 허용 IP 저장 | `settings_admin_ips` | `app/admin/routes/settings.py:76` |
| 킬스위치 전환 | `settings_server_toggle` | `app/admin/routes/settings.py:114` |

모든 라우트는 `require_admin`(`app/admin/deps.py:100`) 의존성으로 `SYSTEM_ADMIN` 권한을 강제하며, POST 라우트는 `validate_csrf`를 호출합니다.

### 4-2. 서비스 레이어 (`app/services/app_settings.py`)

| 기능 | 함수 | 파일:줄 |
|------|------|---------|
| GlobalSettings 단일행 조회 (없으면 기본값 생성) | `get_global_settings` | `app/services/app_settings.py:18` |
| 재시도 설정 갱신 | `update_retry_settings` | `app/services/app_settings.py:33` |
| 어드민 허용 IP 갱신 (lockout 방지 포함) | `update_admin_ips` | `app/services/app_settings.py:59` |
| 외부 API 킬스위치 게이트 (비활성 시 503) | `ensure_server_enabled` | `app/services/app_settings.py:93` |
| 킬스위치 전환 (비밀번호 재확인 포함) | `set_server_disabled` | `app/services/app_settings.py:105` |

모든 함수는 `id=1` 단일 행(`GlobalSettings`)만 읽고 씁니다. `get_global_settings`는 행이 없을 때 기본값으로 생성합니다(기본값: `retry_limit=4`, `retry_interval_hours=12`, `suspended_grace_days=30`, `admin_allowed_ips=[]`, `server_disabled=False`).

### 4-3. GlobalSettings 모델 (`app/models/global_settings.py:16`)

| 컬럼 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `id` | `Integer` PK | `1` | 싱글톤 — 항상 1 |
| `retry_limit` | `Integer` | `4` | 자동결제 재시도 최대 횟수 |
| `retry_interval_hours` | `Integer` | `12` | 재시도 간격(시간) |
| `suspended_grace_days` | `Integer` | `30` | SUSPENDED 유예 일수 |
| `admin_allowed_ips` | `JSONB` | `[]` | 어드민 허용 IP 목록 (빈=제한 없음) |
| `server_disabled` | `Boolean` | `false` | 킬스위치 상태 |
| `disabled_reason` | `String(500)` | `null` | 비활성화 사유 |
| `disabled_at` | `DateTime(tz)` | `null` | 비활성화 시각(UTC) |
| `disabled_by` | `UUID` | `null` | 비활성화한 관리자 user id |

### 4-4. 킬스위치 게이트 위치 (`app/api/deps.py:86`)

`authenticate_service` 함수(외부 API 공통 인증 의존성) 내 API 키 검사 직전에 `ensure_server_enabled(db)`를 호출합니다.

```python
# app/api/deps.py:85-86
# 킬스위치(요청 013): 서버 비활성화 상태면 API 키 읽기 전에 즉시 503 차단
await ensure_server_enabled(db)
```

`ensure_server_enabled`는 `GlobalSettings.server_disabled=True`이면 `ServerDisabledError`(HTTP 503, `app/core/errors.py:100`)를 발생시킵니다. 사유(`disabled_reason`)가 있으면 응답 메시지에 포함됩니다.

### 4-5. 어드민 IP 제한 적용 위치 (`app/admin/deps.py:78-81`)

`require_user` 내부에서 `GlobalSettings.admin_allowed_ips`를 매 요청마다 DB에서 조회합니다. 목록이 비어 있으면 제한 없음, 목록이 있으면 현재 클라이언트 IP가 포함돼야 합니다. 위반 시 `PermissionDeniedError("허용되지 않은 IP입니다")` → 403으로 처리됩니다. 캐시가 없으므로 설정 변경은 즉시 반영됩니다.

### 4-6. 폼 데이터 흐름 요약

| 라우트 | 폼 필드 | 파싱 위치 |
|--------|---------|---------|
| `/settings/retry` | `retry_limit`, `retry_interval_hours`, `suspended_grace_days` | `settings.py:59-65` (int 변환) |
| `/settings/admin-ips` | `admin_allowed_ips` (줄바꿈 구분 문자열) | `settings.py:92-97` (splitlines) |
| `/settings/server-toggle` | `disabled` (on/true/1=비활성), `reason`, `password` | `settings.py:132` (str 비교) |

`admin-ips` 폼의 옥텟 칸은 서버에 직접 전달되지 않습니다. `admin.js`의 `data-ip-form` 핸들러가 submit 이벤트를 가로채 옥텟을 `a.b.c.d` 형태로 조합한 뒤 `<input name="admin_allowed_ips">` hidden 필드를 채우고 제출합니다.

### 4-7. 감사 로그 (`record_audit` 호출 — `app/services/app_settings.py`)

| 동작 | `action` 값 | 기록 detail |
|------|------------|------------|
| 재시도 설정 저장 | `settings.retry_updated` | `retry_limit`, `interval_hours`, `grace_days` |
| 어드민 IP 저장 | `settings.admin_ips_updated` | `count` (저장된 IP 개수) |
| 킬스위치 비활성화 | `server.disabled` | `reason` |
| 킬스위치 활성화 | `server.enabled` | `reason: null` |

### 4-8. 관련 문서 링크

- GlobalSettings 모델·마이그레이션·기본값 상세: [../14-global-settings.md](../14-global-settings.md)
- 어드민 IP 제한 세션·미들웨어 흐름: [../03-auth-and-security.md](../03-auth-and-security.md)

---

## 5. 주의사항 / 자주 하는 실수

### 운영자

- **어드민 IP 설정 시 본인 IP 누락 주의**: 저장할 목록에 현재 접속 중인 본인의 IP가 없으면 서버가 저장을 거부합니다. 반드시 포함 여부를 확인한 뒤 저장하세요. 반대로, 모든 IP를 허용하려면 목록을 완전히 비워 저장합니다.
- **킬스위치 사용 전 필수 확인 사항**:
  1. **비활성화 사유**를 명확히 입력하세요(점검 시간대, 사유 등). 외부 서비스 담당자가 503 응답을 받을 때 이 사유가 메시지에 포함될 수 있습니다.
  2. **본인 비밀번호**를 입력해야 합니다. 비밀번호 불일치 시 전환이 거부됩니다.
  3. 비활성화 확인 모달에서 한 번 더 확인 후 버튼을 클릭합니다.
  4. 비활성화하면 **어드민 화면은 정상 사용 가능**하지만 외부 서비스의 구독·결제 API 전체가 즉시 503을 반환합니다. 미처리 결제 요청이 있을 수 있으므로 점검 공지 후 진행하는 것을 권장합니다.
  5. 복구는 **활성화(복구)** 버튼 + 비밀번호 확인으로 즉시 가능합니다. 확인 모달은 없으니 신중히 클릭하세요.
- **재시도 설정은 즉시 적용되지 않습니다**: 변경 후 다음 갱신 배치 실행부터 새 값이 사용됩니다. 현재 처리 중인 구독 갱신에는 영향을 미치지 않습니다.

### 개발자

- **GlobalSettings은 싱글톤 행(`id=1`)입니다.** 여러 행을 생성하면 안 됩니다. `get_global_settings`의 `get_or_create` 패턴(`app/services/app_settings.py:24-30`)이 항상 id=1 행만 관리하도록 설계되어 있습니다.
- **`update_admin_ips`의 lockout 검사는 비어있지 않은 목록에만 작동합니다** (`app/services/app_settings.py:82`). 빈 목록을 저장하면 lockout 검사를 건너뛰고 IP 제한이 해제됩니다. 이는 의도된 설계입니다(`data-ip-allow-empty`).
- **킬스위치 게이트는 `authenticate_service` 진입 직후 최우선으로 실행됩니다** (`app/api/deps.py:86`). 새 외부 API 엔드포인트를 추가할 때 `authenticate_service` 의존성을 반드시 포함해야 킬스위치가 적용됩니다. 어드민 라우트에는 이 의존성을 추가하지 마세요.
- **`set_server_disabled`의 비밀번호 검증은 `verify_password(평문, 해시)` 순서입니다** (`app/services/app_settings.py:120`). 인자 순서를 바꾸면 항상 실패합니다.
- **감사 로그는 DB 커밋 전에 `record_audit`을 호출하고 단일 `commit()`으로 함께 저장합니다**. `record_audit` 자체는 커밋하지 않으므로(`app/services/app_settings.py:1-5` docstring 참고) 설정 변경과 감사 로그가 원자적으로 처리됩니다.
- **어드민 IP는 변경 즉시 매 요청마다 적용됩니다** (캐시 없음, `app/admin/deps.py:80`). 잘못된 목록을 실수로 저장하면 즉시 전원 접근이 차단될 수 있습니다. 테스트 환경에서 변경 후 다른 탭에서 어드민 접속이 되는지 먼저 확인하는 습관을 권장합니다.
