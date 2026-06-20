# 08. 계정 관리

> **어드민 계정(SYSTEM_ADMIN · SERVICE_MANAGER)을 만들고 역할·서비스·비밀번호를 관리하는 화면.**  
> - **운영자**: 계정 생성 → 서비스 배정 → 역할/정보 수정 → 비밀번호 재설정 방법을 확인합니다.  
> - **개발자**: 라우트 위치(file:line)·서비스 레이어 호출·보호 규칙(본인/대표 담당자)을 확인합니다.  
>
> 계정·역할·인증 내부 흐름(로그인·세션·비밀번호 설정)은 → [../13-admin-accounts.md](../13-admin-accounts.md)

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

| 항목 | 내용 |
|---|---|
| 목적 | 어드민 콘솔에 로그인하는 계정(SYSTEM_ADMIN/SERVICE_MANAGER)을 생성·관리한다 |
| 접근 경로 | 좌측 LNB → **계정** |
| 권한 | **SYSTEM_ADMIN 전용** — 모든 라우트에 `Depends(require_admin)` 적용 |
| 라우트 모듈 | `app/admin/routes/users.py` |
| 템플릿 디렉터리 | `app/admin/templates/users/` |

`require_admin`은 `app/admin/deps.py:100`에서 `require_role(UserRole.SYSTEM_ADMIN)`의 축약으로 정의되어 있으며,
역할 불일치 시 `PermissionDeniedError(403)`를 반환한다.

---

## 2. 화면 구성

### 2-1. 목록 (`GET /admin/users`)

**파일**: `app/admin/templates/users/list.html` (전체 레이아웃), `users/_table.html` (htmx 리프레시용 partial)

| 컬럼 | 데이터 소스 | 정렬 가능 |
|---|---|---|
| 이메일 | `User.email` | ✓ (`email`) |
| 역할 | `User.role` → "시스템 관리자" / "서비스 담당자" | ✓ (`role`) |
| 주 서비스 | LEFT JOIN `Service.name` (없으면 `-`) | — |
| 상태 | `User.status` 배지 (ACTIVE / PENDING / LOCKED / DISABLED) | ✓ (`status`) |
| — | 상세 링크 (`상세 ›`) | — |

**툴바** (`users/_table.html:3-6`):

| 컨트롤 | 파라미터 |
|---|---|
| 이메일 검색 | `?q=` (iLIKE) |
| 역할 필터 | `?role=SYSTEM_ADMIN \| SERVICE_MANAGER` |
| 상태 필터 | `?status=ACTIVE \| PENDING \| LOCKED` (DELETED는 선택지에 없음) |
| 엑셀 다운로드 | `/admin/users/export.xlsx` (현재 검색·필터 유지) |
| 계정 추가 버튼 | `/admin/users/new` |

**기본 정렬**: `created_at` DESC, 페이지당 15건 (`PER_PAGE_DEFAULT`, `app/admin/pagination.py:18`).

> **DELETED 계정은 어떤 필터로도 목록에 나타나지 않는다.**  
> 쿼리 기본 조건이 `User.status != UserStatus.DELETED`(`app/admin/routes/users.py:53`)이기 때문이다.

---

### 2-2. 생성 폼 (`GET /admin/users/new`)

**파일**: `app/admin/templates/users/new.html`

| 항목 | 입력 유형 | 필수 여부 |
|---|---|---|
| 이메일 | `type="email"` | 필수 |
| 전화번호 | `type="tel"` | 선택 |
| 역할 | `<select>`: SERVICE_MANAGER(기본값) / SYSTEM_ADMIN | 필수 |
| 담당 서비스 | 체크박스 다중 선택 (SERVICE_MANAGER 역할 선택 시만 표시) | 선택 |

역할 셀렉트 변경 시 JavaScript `onchange`로 담당 서비스 블록(`#svc-block`)의 display를 `block/none`으로 토글한다 (`new.html:18`).  
담당 서비스가 없어도 계정 생성이 가능하다(서비스 등록 시 이후에 배정 가능).

---

### 2-3. 상세 (`GET /admin/users/{user_id}`)

**파일**: `app/admin/templates/users/detail.html`

**좌측 카드 — 계정 정보**:

| 항목 | 내용 |
|---|---|
| 이메일 | `account.email` |
| 전화번호 | `account.phone` (없으면 `-`) |
| 역할 | 한글 변환 표시 |
| 상태 | 배지 (ACTIVE / PENDING / LOCKED / DISABLED) |
| 버튼 | 비밀번호 재설정 메일 / 비활성화(또는 활성화) / 삭제 |

**우측 카드 — 담당 서비스** (`SERVICE_MANAGER`에게만 표시):

- 현재 담당 서비스 목록 + 개별 **해제** 버튼
- 미배정 서비스 드롭다운 + **서비스 추가** 버튼 (`assignable`이 있을 때만 표시)

---

### 2-4. 수정 폼 (`GET /admin/users/{user_id}/edit`)

**파일**: `app/admin/templates/users/edit.html`

수정 가능 항목: **이메일**, **전화번호**만. 역할 변경은 수정 폼에서 지원하지 않는다.

---

## 3. 할 수 있는 동작

### 3-1. 계정 목록 조회 / 검색 / 필터

1. LNB에서 **계정** 클릭 → `GET /admin/users` → 전체 계정 목록(DELETED 제외).
2. 검색창에 이메일 일부 입력 → htmx가 `GET /admin/users?q=…`로 `#list-users`를 부분 갱신.
3. 역할/상태 드롭다운 선택 → 즉시 필터 적용.
4. 컬럼 헤더 클릭 → 오름/내림 정렬 토글.
5. **엑셀** 버튼 → 현재 검색·필터를 그대로 적용한 전체 결과를 `.xlsx`로 다운로드.

---

### 3-2. 계정 생성

1. **계정 추가** 버튼 → `GET /admin/users/new`.
2. 이메일·역할(·담당 서비스) 입력 후 **계정 생성 + 설정 메일 발송** 클릭.
3. `POST /admin/users` 처리:
   - 이메일 소문자 정규화, 역할 유효성 검사.
   - SYSTEM_ADMIN 선택 시 서비스 목록 강제 초기화.
   - 이메일 중복 확인(SELECT 선조회 + `flush IntegrityError` 이중 방어).
   - 계정 `status=PENDING`으로 생성 → `PasswordSetupToken` 48시간 토큰 발급.
   - 감사 로그 기록 → DB 커밋.
   - **계정 설정 메일 발송** (`/admin/setup-password?token=…`).
4. 성공: `saved_redirect("/admin/users?…", "저장되었습니다")` → **완료 모달** + 메일 발송 결과 토스트.
5. 실패(중복 이메일 등): 폼 재렌더 + 에러 메시지 표시.

> **감사 상세(요청 015)**: 계정 동작은 감사 detail에 상세를 남긴다 — 생성(`account.create`: 이메일·역할·서비스 수), 수정(`account.update`: 이메일/전화 변경 전→후), 비활성화/활성화(`account.disable`/`enable`: 이메일·상태 전→후), 삭제(이메일), 담당 서비스 추가/해제(`account.(un)assign_service`: 이메일·서비스명), 비밀번호 재설정 메일(`user.password_reset_issued`: 이메일·발송 안내). 감사로그 화면에서 "변경 전 → 변경 후"로 표시된다.

> 메일 발송 실패해도 계정은 생성된다. 토스트에 "메일 발송에 실패했습니다. SMTP 설정을 확인하세요" 가 표시된다.

---

### 3-3. 계정 정보 수정

1. 상세 화면 우상단 **정보 수정** 버튼 → `GET /admin/users/{user_id}/edit`.
2. 이메일·전화번호 변경 후 **저장** 클릭.
3. `POST /admin/users/{user_id}/edit` 처리:
   - 이메일 변경 시 중복 체크.
   - 이 계정이 대표(`Service.manager_email`)인 서비스들의 `manager_email` 일괄 동기화.
   - 감사 로그 → DB 커밋.
4. 성공: 상세 페이지로 리다이렉트 + **완료 모달**.
5. 실패: 수정 폼 재렌더 + 에러 메시지.

> `DELETED` 상태 계정은 수정 폼(`GET …/edit`) 접근 시 404를 반환한다 (`users.py:209`).

---

### 3-4. 서비스 배정 / 해제

#### 서비스 배정

1. 상세 화면 우측 카드 드롭다운에서 서비스 선택 → **서비스 추가** 클릭.
2. `POST /admin/users/{user_id}/services` 처리:
   - `SERVICE_MANAGER` 역할이 아닌 계정은 오류.
   - `user.service_id`가 None이면 주 서비스로 직접 설정, 있으면 `UserService` 다대다로 추가.
   - 이미 담당 중이면 조용히 무시(중복 허용 안 함).
3. 성공: `saved_redirect` → **완료 모달**.
4. 실패: `?error=` 붙여 상세 페이지 리다이렉트 → 에러 토스트.

#### 서비스 해제

1. 담당 서비스 행 옆 **해제** 클릭.
2. `POST /admin/users/{user_id}/services/{service_id}/remove` 처리:
   - 주 서비스를 해제하면 `UserService`에서 다른 서비스를 주 서비스로 승격.
   - 남은 담당 서비스가 없으면 `user.service_id = None`.
3. 성공: `saved_redirect` → **"해제되었습니다" 모달**.

> **해제 버튼은 보호 확인 없이 즉시 처리**된다. 대표 담당자(`Service.manager_email`)는 서비스 배정 해제가 아닌 계정 삭제 시점에만 보호된다.

---

### 3-5. 비활성화 / 활성화

#### 비활성화

1. 상세 화면 **비활성화** 버튼 클릭 → **확인 모달** 표시.
   - 모달 메시지: "이 계정을 비활성화하면 로그인이 차단되고 기존 세션이 모두 종료됩니다."
2. 확인 후 `POST /admin/users/{user_id}/disable` (hidden: `disabled=true`) 처리:
   - **본인 계정 비활성화 불가** — `actor_user_id == user_id` 이면 에러.
   - `user.status = DISABLED`.
   - DB 커밋 후 Redis `destroy_user_sessions`로 해당 사용자 세션 즉시 파기.
3. 성공: `saved_redirect` → **"변경되었습니다" 모달**.
4. 실패: `?error=` → 에러 토스트.

#### 활성화

1. 상태가 `DISABLED`인 계정의 상세 화면에는 **비활성화** 대신 **활성화** 버튼이 표시됨 (`detail.html:26-39`).
2. **활성화** 클릭 → `POST /admin/users/{user_id}/disable` (hidden: `disabled=false`).
   - 비밀번호가 설정된 경우 → `status=ACTIVE`, 미설정 시 → `status=PENDING`으로 복구.

> 폼은 hidden input `disabled=true/false`로 의도를 명확히 전달한다. 값이 정확히 `"false"`일 때만 활성화로 처리 (`users.py:250`).

---

### 3-6. 계정 삭제

1. 상세 화면 **삭제** 버튼(빨간색) → **확인 모달** 표시.
   - 모달 메시지: "이 계정을 삭제하면 로그인이 영구 차단되고 담당 서비스 연결이 모두 해제됩니다. 이 작업은 되돌릴 수 없습니다."
2. 확인 후 `POST /admin/users/{user_id}/delete` 처리:
   - **본인 계정 삭제 불가** — `actor_user_id == user_id` 이면 에러.
   - **대표 담당자 보호** — 이 계정이 어느 서비스의 `Service.manager_email`이면 삭제 불가. 에러: `"'{서비스명}' 서비스의 대표 담당자입니다. 먼저 다른 계정을 대표로 지정하세요."`
   - 논리 삭제: `status=DELETED`, `service_id=None`, `UserService` 행 전체 삭제.
   - DB 커밋 → Redis 세션 파기.
3. 성공: `saved_redirect("/admin/users", "삭제되었습니다")` → 목록 + **완료 모달**.
4. 실패: `?error=` → 에러 토스트.

> 물리 삭제가 아닌 논리 삭제(소프트 삭제)를 사용하는 이유: 감사 로그에 `actor_user_id`로 남아 있는 외래 참조를 유지하기 위함 (`accounts.py:229`).

---

### 3-7. 비밀번호 재설정

1. 상세 화면 **비밀번호 재설정 메일** 버튼 클릭.
2. `POST /admin/users/{user_id}/reset-password` 처리:
   - `PasswordSetupToken` 48시간 유효 토큰 생성.
   - 감사 로그 → DB 커밋.
   - Redis에서 해당 사용자의 기존 세션 **즉시 파기** (계정 탈취 의심 상황 대비).
   - 재설정 메일 발송 (`/admin/setup-password?token=…`).
3. 결과: 상세 페이지로 303 리다이렉트 + 메일 발송 결과 토스트.

> 비밀번호 재설정은 **완료 모달이 아닌 토스트**로만 결과를 전달한다 (`saved_redirect`가 아닌 일반 `RedirectResponse` 사용, `users.py:293-294`).

---

## 4. 개발 참조

### 4-1. 라우트 함수 요약

| HTTP | 경로 | 함수 | file:line |
|---|---|---|---|
| `GET` | `/admin/users` | `users_list` | `app/admin/routes/users.py:79` |
| `GET` | `/admin/users/export.xlsx` | `users_export` | `app/admin/routes/users.py:100` |
| `GET` | `/admin/users/new` | `users_new` | `app/admin/routes/users.py:116` |
| `POST` | `/admin/users` | `users_create` | `app/admin/routes/users.py:124` |
| `GET` | `/admin/users/{user_id}` | `users_detail` | `app/admin/routes/users.py:151` |
| `POST` | `/admin/users/{user_id}/services` | `users_assign_service` | `app/admin/routes/users.py:172` |
| `POST` | `/admin/users/{user_id}/services/{service_id}/remove` | `users_unassign_service` | `app/admin/routes/users.py:190` |
| `GET` | `/admin/users/{user_id}/edit` | `users_edit` | `app/admin/routes/users.py:203` |
| `POST` | `/admin/users/{user_id}/edit` | `users_update` | `app/admin/routes/users.py:214` |
| `POST` | `/admin/users/{user_id}/disable` | `users_disable` | `app/admin/routes/users.py:233` |
| `POST` | `/admin/users/{user_id}/delete` | `users_delete` | `app/admin/routes/users.py:261` |
| `POST` | `/admin/users/{user_id}/reset-password` | `users_reset_password` | `app/admin/routes/users.py:278` |

---

### 4-2. 서비스 레이어 호출

모든 비즈니스 로직은 `app/services/accounts.py`에 집중되어 있다.

| 라우트 함수 | 호출 함수 | file:line |
|---|---|---|
| `users_create` | `account_service.create_account()` | `accounts.py:72` |
| `users_detail` | `account_service.list_managed_services()` | `accounts.py:316` |
| `users_assign_service` | `account_service.assign_service()` | `accounts.py:269` |
| `users_unassign_service` | `account_service.unassign_service()` | `accounts.py:294` |
| `users_update` | `account_service.update_account()` | `accounts.py:149` |
| `users_disable` | `account_service.set_account_disabled()` | `accounts.py:191` |
| `users_delete` | `account_service.delete_account()` | `accounts.py:223` |
| `users_reset_password` | `auth_service.issue_password_reset()` | `app/services/auth.py:257` |

서비스 목록 조회는 `app/services/registry.py`의 `list_services()` 사용 (생성 폼·상세 화면).

---

### 4-3. 권한 의존성 체인

```
HTTP 요청
  └─ require_admin (app/admin/deps.py:100)
       = require_role(UserRole.SYSTEM_ADMIN)
         └─ require_user (app/admin/deps.py:60)
              1. SESSION_COOKIE("admin_session") → Redis 세션 조회
              2. DB User 조회 + status == ACTIVE 확인
              3. admin_allowed_ips 검사 (설정된 경우)
              4. effective_service_ids() → AdminContext.service_ids
              → AdminContext 반환
         └─ role 검사 — SYSTEM_ADMIN 아니면 PermissionDeniedError(403)
```

---

### 4-4. CSRF 검증

모든 POST 라우트의 첫 번째 호출:

```python
await validate_csrf(request, ctx)  # app/admin/deps.py:105
```

폼에 항상 포함되어야 하는 hidden 필드:

```html
<input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
```

---

### 4-5. htmx 동작 (목록 화면)

- `render_list()` (`app/admin/__init__.py:67`) — `HX-Request` 헤더가 있으면 `_table.html` partial만 반환, 없으면 `list.html` 전체 렌더.
- `_table.html`의 `div#list-users`가 htmx 스왑 대상.
- 검색·필터·정렬·페이지 이동 모두 htmx가 `#list-users`만 교체한다.

---

### 4-6. 엑셀 다운로드 구조

`users_export` (`users.py:100-113`):

- `paginate`를 거치지 않고 쿼리를 직접 실행 → 페이지 제한 없이 전체 결과 출력.
- 컬럼: `["이메일", "역할", "주 서비스", "상태"]` (순서 고정).
- `xlsx_response("users", …, sheet_title="관리자")` (`app/admin/export.py:23`).
- 파일명 형식: `users-{YYYYmmdd-HHMM(KST)}.xlsx`.

---

### 4-7. 담당 서비스 데이터 모델

```
User.service_id           → 주 서비스 (최초 배정 또는 주 해제 후 승격된 서비스)
UserService(user_id, service_id) → 추가 담당 서비스 (다대다)

effective_service_ids(db, user) → 합집합 반환 (accounts.py:33)
  SYSTEM_ADMIN → None (전체 접근)
  SERVICE_MANAGER → {service_id} ∪ {UserService.service_id...}
```

배정 시: `service_id=None`이면 주 서비스로, 아니면 `UserService` 행 추가 (`accounts.py:284-287`).  
해제 시: 주 서비스를 해제하면 `UserService`의 다른 서비스가 주로 승격 (`accounts.py:300-309`).

---

### 4-8. 보호 규칙 정리

| 규칙 | 적용 동작 | 구현 위치 |
|---|---|---|
| 본인 비활성화 금지 | `POST …/disable` | `accounts.py:208-209` |
| 본인 삭제 금지 | `POST …/delete` | `accounts.py:241-242` |
| 대표 담당자 삭제 금지 | `POST …/delete` | `accounts.py:243-247` |
| DELETED 계정 수정 금지 | `GET …/edit` | `users.py:209` |
| DELETED 계정은 목록 제외 | `GET /users` 기본 쿼리 | `users.py:53` |

---

### 4-9. 저장 완료 모달 vs 토스트

| 동작 | 결과 UI |
|---|---|
| 생성·수정·서비스 배정·해제·비활성·활성·삭제 | **완료 모달** (`saved_redirect()` → `?saved=` → `showSaved` 이벤트) |
| 비밀번호 재설정 메일 발송 | **토스트만** (`?flash=` / `?flash_type=error`) |
| 메일 발송 실패 (생성·재설정) | **에러 토스트** (`flash_type=error`) |

`saved_redirect` 구현: `app/admin/__init__.py:39`.  
`email_flash_qs` 구현: `app/admin/flash.py:20`.

---

## 5. 주의사항 / 자주 하는 실수

### 운영자

- **대표 담당자 삭제 불가**: 계정 삭제 전 반드시 서비스 상세(`/admin/services/{id}`)에서 다른 계정을 대표로 변경한다 (`[../03-services.md](03-services.md)` 참고).
- **본인 계정 비활성화·삭제 불가**: 서버에서 거부한다.
- **PENDING 계정**: 계정 생성 직후 비밀번호를 설정하지 않은 상태. 로그인 불가. 재설정 메일 재발송으로 새 링크를 보낼 수 있다.
- **메일 발송 실패**: SMTP 설정 문제. 계정 자체는 생성/발급되므로 SMTP 수정 후 **비밀번호 재설정 메일** 버튼으로 재발송 가능.
- **비밀번호 설정 링크 유효기간**: 발송 후 **48시간**. 만료 시 재설정 메일 버튼으로 새 링크를 발급한다.

### 개발자

- **목록 쿼리**: `_build_users_query` (`users.py:34`)는 `User ✕ Service` OUTER JOIN을 수행한다. `svc`가 `None`일 수 있으므로 템플릿에서 `svc.name if svc else '-'`로 처리한다.
- **정렬 컬럼 맵**: `_USER_SORT` (`users.py:30`) — `email`, `role`, `status`, `created_at`만 허용. 이 외 값은 `PageParams.from_request`에서 `default_sort`로 대체된다.
- **비활성화 hidden input**: 체크박스 방식이 아닌 문자열 `"true"/"false"`. 서버 파싱: `str(…) != "false"` (`users.py:250`) — 값이 없거나 다른 값이면 비활성화로 처리.
- **소프트 삭제 이후 조회**: `_get_account()` (`accounts.py:141`)는 `DELETED` 상태를 `NotFoundError`로 처리한다. 따라서 삭제된 계정 ID로 수정/비활성화 API 호출 시 404가 반환된다.
- **서비스 배정 권한**: `assign_service`/`unassign_service`는 `SERVICE_MANAGER` 역할만 허용한다 (`accounts.py:278`, `303`). `SYSTEM_ADMIN`에게 서비스를 배정하려 하면 `InputValidationError`가 반환된다.
- **이메일 수정 시 서비스 manager_email 동기화**: `update_account` 내부에서 `Service.manager_email == old_email`인 서비스를 일괄 UPDATE한다 (`accounts.py:173-174`). 이 동기화는 수동으로 따로 호출할 필요 없다.

---

## 관련 문서

- 계정·역할·로그인 내부 처리 흐름 → [../13-admin-accounts.md](../13-admin-accounts.md)
- 인증·세션·CSRF 공통 구조 → [../03-auth-and-security.md](../03-auth-and-security.md)
- 서비스 대표 담당자 변경 → [03-services.md](03-services.md)
- 어드민 화면 공통 개념 (권한·UI 패턴) → [README.md](README.md)
