# 09. 서비스 등록·키 발급/회전·취소정책·담당자

> **상호참조**: 인증(API 키·HMAC) → [03. 인증과 보안 공통](03-auth-and-security.md) |
> 단건결제 취소정책 적용 → [07. 단건(일반) 결제 + 취소](07-one-off-payment.md) |
> 어드민 계정 관리 → [13. 어드민 계정·역할·로그인/비밀번호](13-admin-accounts.md) |
> 테이블 구조 → [02. 데이터베이스](02-database.md)

---

## 1. 한 줄 요약

사내 서비스를 결제/구독 API 서버에 **등록**하고, 외부 서비스가 API를 호출할 때 사용하는
**API 키·HMAC 시크릿을 발급/회전**하며, 담당자 계정과 단건결제 취소 정책을 관리하는
어드민 전용 기능입니다.

---

## 2. 언제 실행되나

| 트리거 | 설명 |
|--------|------|
| **어드민 콘솔** `GET /admin/services` | 등록된 서비스 목록 조회 |
| **어드민 콘솔** `GET /admin/services/new` | 서비스 등록 폼 |
| **어드민 콘솔** `POST /admin/services` | 서비스 등록(API 키·HMAC 시크릿 최초 발급) |
| **어드민 콘솔** `GET /admin/services/{id}` | 서비스 상세(요금제·구독·단건결제 탭 포함) |
| **어드민 콘솔** `GET /admin/services/{id}/keys-modal` | 저장된 키 복사 모달(htmx fragment) |
| **어드민 콘솔** `POST /admin/services/{id}/rotate-keys` | API 키·HMAC 시크릿 재발급 |
| **어드민 콘솔** `POST /admin/services/{id}/assign-manager` | 담당자 추가 |
| **어드민 콘솔** `POST /admin/services/{id}/primary-manager` | 대표 담당자 변경 |
| **어드민 콘솔** `POST /admin/services/{id}/managers/{uid}/remove` | 담당자 해제 |
| **어드민 콘솔** `POST /admin/services/{id}/ips` | 허용 IP 목록 수정 |
| **어드민 콘솔** `POST /admin/services/{id}/cancel-policy` | 단건결제 취소 정책 저장 |
| **어드민 콘솔** `POST /admin/services/{id}/status` | 서비스 상태 변경(ACTIVE/INACTIVE) |
| **어드민 콘솔** `POST /admin/services/{id}/delete` | 서비스 삭제 |

모든 엔드포인트는 **SYSTEM_ADMIN 전용**입니다(`require_admin`).
(`app/admin/routes/services.py:1-5` — 파일 최상단 docstring 참조)

---

## 3. 요청 진입점

### 3-1. 서비스 목록 조회

**`GET /admin/services`**

- 라우트 함수: `app/admin/routes/services.py:63` — `services_list()`
- 권한 검사: `Depends(require_admin)` — SYSTEM_ADMIN만 허용
  (`app/admin/deps.py:100` 에서 `require_role(UserRole.SYSTEM_ADMIN)` 으로 정의)
- htmx 판별: `render_list()`가 `HX-Request` 헤더를 보고 전체 페이지(`list.html`) 또는
  테이블 partial(`_table.html`)만 반환

검색·필터 쿼리 파라미터:

| 파라미터 | 설명 |
|----------|------|
| `q` | 서비스명 또는 담당자 이메일 부분 일치 검색 |
| `status` | `ACTIVE` / `INACTIVE` 상태 필터 |
| `sort`, `dir` | 정렬 컬럼·방향 (기본: `created_at` DESC) |
| `page` | 페이지 번호 |

정렬 가능 컬럼: `name`, `status`, `created_at`
(`app/admin/routes/services.py:36` — `_SVC_SORT` 딕셔너리)

---

### 3-2. 서비스 등록

**`POST /admin/services`**

- 라우트 함수: `app/admin/routes/services.py:179` — `services_create()`
- 서비스 레이어: `app/services/registry.py:94` — `register_service()`
- CSRF 검증: `validate_csrf(request, ctx)` 호출 필수
  (`app/admin/deps.py:105`)

폼 필드:

| 필드명 | 타입 | 설명 |
|--------|------|------|
| `csrf_token` | hidden | CSRF 토큰 |
| `name` | text | 서비스명 (전체 고유) |
| `manager_ids` | checkbox(복수) | 담당자 계정 UUID 목록 |
| `primary_user_id` | select | 대표 담당자 UUID (알림 수신처) |
| `allowed_ips` | hidden | 줄바꿈 구분 IPv4 목록 (IP 옥텟 UI가 자동 조립) |
| `cancellation_enabled` | checkbox | 단건결제 취소 허용 여부 (`on` = True) |
| `cancellation_fee_percent` | number | 취소 수수료율 0~100 (%) |

> **담당자 폼 UI 주의**: 체크박스(`manager_ids`)로 복수 선택, 드롭다운(`primary_user_id`)으로 대표 1명 선택.
> 대표는 체크 목록에 없어도 서버가 자동으로 포함합니다.
> (`app/admin/templates/services/new.html:17-29`)

성공 응답: `services/keys.html` 렌더 — **평문 키가 이때 단 1회 표시**됩니다.
(`app/admin/routes/services.py:221`)

---

### 3-3. 서비스 상세

**`GET /admin/services/{service_id}`**

- 라우트 함수: `app/admin/routes/services.py:301` — `services_detail()`

상세 페이지에는 세 탭이 있습니다. htmx로 탭 클릭 시 partial만 교체합니다:

| `HX-Target` 헤더 값 | 반환 partial | 내용 |
|---------------------|-------------|------|
| `list-svc-plans` | `services/_plans_table.html` | 요금제 목록 |
| `list-svc-subs` | `services/_subs_table.html` | 구독 목록 |
| `list-svc-oneoff` | `services/_oneoff_table.html` | 단건결제 목록 |
| (없음, 일반 요청) | `services/detail.html` | 전체 페이지 |

(`app/admin/routes/services.py:337-343`)

---

### 3-4. 키 재발급 (rotate)

**`POST /admin/services/{service_id}/rotate-keys`**

- 라우트 함수: `app/admin/routes/services.py:442` — `services_rotate()`
- 서비스 레이어: `app/services/registry.py:177` — `rotate_keys()`

> **주의**: 재발급 즉시 기존 키는 무효가 됩니다. 외부 서비스는 새 키로 교체해야 합니다.

---

## 4. 단계별 처리 흐름

### 4-1. 서비스 등록 전체 흐름

```
[브라우저] POST /admin/services
     │
     ▼
[routes/services.py:179  services_create()]
  1. CSRF 검증 (validate_csrf)
  2. 폼 파싱: manager_ids, primary_user_id, allowed_ips, 취소정책 필드
  3. UUID 파싱 오류 → form_error 렌더(200, 재시도)
  4. cancellation_fee_percent 정수 변환 오류 → form_error 렌더
  5. registry.register_service() 호출 →
     │
     ▼
[services/registry.py:94  register_service()]
  6.  서비스명 공백 검증 (없으면 InputValidationError)
  7.  _validate_ips(): IPv4 형식 검증(빈 목록 허용 = IP 제한 없음)
  8.  cancellation_fee_percent 0~100 범위 검증
  9.  _validate_managers(): primary 포함, 중복 제거, 역할·상태 확인
  10. 서비스명 중복 SELECT 선조회 (ConflictError)
  11. generate_service_api_key() → "svc_" + 32바이트 URL-safe 난수
        (app/core/security.py:19)
  12. generate_hmac_secret() → 48바이트 URL-safe 난수
        (app/core/security.py:28)
  13. Service 생성:
        - api_key_hash     = sha256_hex(api_key)    ← 검증용(평문 저장 안 함)
        - api_key_encrypted = cipher.encrypt(api_key) ← 화면 복사용(AES-GCM)
        - hmac_secret_encrypted = cipher.encrypt(hmac_secret)
        - manager_email = managers[0].email         ← 대표 담당자 알림 수신처
  14. db.flush() → IntegrityError 경쟁 처리 (동시 등록 시 최종 심판)
  15. 담당자 할당:
        - user.service_id == None → user.service_id = service.id (주 서비스)
        - user.service_id != None → UserService 행 추가 (다대다)
  16. record_audit(action="service.register") → db.commit()
  17. IssuedCredentials(service, api_key 평문, hmac_secret 평문) 반환
     │
     ▼
[routes/services.py:221]
  18. render "services/keys.html" — api_key·hmac_secret 평문 1회 표시
  19. (이후 평문은 파기됨 — DB에는 해시·암호문만 존재)
```

### 4-2. 키 재발급 흐름

```
[브라우저] POST /admin/services/{id}/rotate-keys
     │
     ▼
[routes/services.py:442  services_rotate()]
  1. CSRF 검증
  2. registry.rotate_keys(db, cipher, service_id) →
        - 새 api_key, hmac_secret 생성
        - service.api_key_hash 교체
        - service.api_key_encrypted 교체
        - service.hmac_secret_encrypted 교체
        - record_audit(action="service.rotate_keys") → commit
        (app/services/registry.py:177)
  3. render "services/keys.html" — 새 평문 키 1회 표시
```

---

## 5. 암호화 구조 — 키를 어떻게 저장하나

API 키와 HMAC 시크릿은 **절대 평문으로 DB에 저장하지 않습니다.**
두 가지 형태로 저장합니다.

### 5-1. API 키 이중 저장

| 컬럼 | 목적 | 방식 |
|------|------|------|
| `api_key_hash` (String 64) | **외부 API 인증** — 요청 때마다 대조 | SHA-256 해시 (`app/core/security.py:45`) |
| `api_key_encrypted` (String 512) | **어드민 화면 복사** — 키 분실 시 재조회 | AES-256-GCM 암호문 (`app/core/crypto.py:32`) |

인증 시 DB에서 `WHERE api_key_hash = sha256_hex(입력값)` 으로 조회합니다.
(`app/api/deps.py:95-98`)

### 5-2. HMAC 시크릿 저장

| 컬럼 | 목적 | 방식 |
|------|------|------|
| `hmac_secret_encrypted` (String 512) | 외부 API 서명 검증 시 복호화 사용 | AES-256-GCM 암호문 |

인증 6단계 중 HMAC 서명 검증 시 `cipher.decrypt(service.hmac_secret_encrypted)`로
복호화해 서명을 재계산합니다.
(`app/api/deps.py:127-131`)

### 5-3. AES-GCM 암호화 원리 (`app/core/crypto.py:14-50`)

```
encrypt(평문):
  nonce = os.urandom(12)         # 12바이트 난수 (매 호출마다 새 값)
  ciphertext = AES-GCM(key, nonce, 평문)
  저장 = base64(nonce + ciphertext + auth_tag)

decrypt(저장값):
  raw = base64_decode(저장값)
  nonce = raw[:12]
  ciphertext_with_tag = raw[12:]
  평문 = AES-GCM.decrypt(key, nonce, ciphertext_with_tag)
  # auth_tag 불일치 시 라이브러리가 예외 발생 (변조 감지)
```

키 소스: `Settings.encryption_key` (base64 인코딩된 32바이트)
(`app/core/crypto.py:21-30`)

### 5-4. 키 복사 모달의 Cache-Control: no-store

어드민이 `GET /admin/services/{id}/keys-modal` 을 호출하면 평문 키가 담긴
htmx fragment를 반환하는데, 이 응답에는 반드시 `Cache-Control: no-store` 헤더를 붙입니다.
브라우저가 history 복원 등으로 캐시된 응답을 재사용하는 것을 막기 위해서입니다.
(`app/admin/routes/services.py:258`)

> 모달을 열 때마다 `record_audit(action="service.keys_viewed")` 도 기록됩니다.
> (`app/admin/routes/services.py:251-253`)

---

## 6. 담당자 관리 구조

### 6-1. 담당자 두 가지 연결 방식

담당자(SERVICE_MANAGER 계정)와 서비스는 두 가지 방식으로 연결됩니다.

| 방식 | 위치 | 설명 |
|------|------|------|
| 주 서비스 | `users.service_id` (FK → services.id CASCADE) | 계정의 주 담당 서비스 |
| 추가 서비스 | `user_services` 테이블 (다대다) | 주 서비스 외 추가 담당 서비스 |

유효 담당 서비스 = `user.service_id` ∪ `user_services.service_id`
(`app/services/accounts.py:33-49` — `effective_service_ids()`)

할당 규칙: `user.service_id == None` 이면 주 서비스로 직접 지정, 이미 주 서비스가 있으면
`UserService` 행 추가.
(`app/services/accounts.py:283-287` — `assign_service()`)

### 6-2. 대표 담당자 (`manager_email`)

`Service.manager_email` 은 결제 실패·갱신 등 알림 이메일의 수신처입니다.
서비스 등록 시 `managers[0].email` 이 자동으로 채워집니다.
(`app/services/registry.py:139`)

**대표 변경**: `POST /admin/services/{id}/primary-manager`
→ `registry.set_primary_manager()` 가 조건 검증 후 `service.manager_email` 을 갱신합니다.
(`app/services/registry.py:288-312`)

조건:
- 대상 계정이 SERVICE_MANAGER 역할이고 DELETED 상태가 아닐 것
- 이 서비스의 담당자(주 서비스 또는 UserService 다대다)일 것

**대표 담당자 해제 보호**:
`service.manager_email` 과 이메일이 같은 계정은 해제할 수 없습니다.
먼저 다른 계정을 대표로 지정한 후 해제해야 합니다.
(`app/admin/routes/services.py:428-435`)

**담당자 계정 삭제 시 대표 보호**:
어카운트 삭제 시(`accounts.delete_account()`) 해당 계정이 어느 서비스의 `manager_email` 이면
삭제가 거부됩니다.
(`app/services/accounts.py:243-247`)

**이메일 변경 시 동기화**:
담당자의 이메일을 변경하면 그 계정이 대표인 서비스들의 `manager_email` 도 자동 갱신됩니다.
(`app/services/accounts.py:172-175`)

### 6-3. 담당자 상세 페이지 UI

상세 페이지 개요 카드 안에 담당자 목록이 표시됩니다:
- 대표 담당자 행: 해제 버튼 없음, "대표" 배지 표시
- 비대표 담당자 행: 해제 폼(`/managers/{uid}/remove`), 대표 지정 버튼(`/primary-manager`)
- 담당자 추가 토글 버튼 → 인라인 드롭다운 폼 표시

(`tests/e2e/test_service_detail_page.py:94-106` — UI 검증 테스트)

---

## 7. 허용 IP 관리

외부 서비스가 API를 호출할 수 있는 IP를 화이트리스트로 관리합니다.
인증 2단계에서 `service.allowed_ips` 와 클라이언트 IP를 대조합니다.
**단, 목록이 비어 있으면 IP 제한 없음(모든 IP 허용)** — 이 경우 HMAC 서명으로만 보호합니다.

### 7-1. 검증 규칙

- **IPv4 전용** — CIDR, IPv6 불허
- **선택(빈 목록 허용)** — 비우면 IP 제한 없음(모든 IP 허용). 값이 있으면 형식 검증.
- 파싱: 줄바꿈(`\n`) 및 콤마(`,`) 모두 허용 → `_parse_ips()`
  (`app/admin/routes/services.py:57-60`)
- IP 검증: `ipaddress.IPv4Address(ip)` — 형식 오류 시 `InputValidationError`
  (`app/services/registry.py:60-72` — `_validate_ips()`)

### 7-2. 허용 IP 수정

`POST /admin/services/{id}/ips`
→ `registry.update_allowed_ips()` 가 목록 전체를 교체합니다.
(`app/services/registry.py:193-202`)

### 7-3. 옥텟 UI

화면에서는 IP를 옥텟(`.` 구분 4자리) 입력 칸으로 표시합니다.
JavaScript가 제출 시 `hidden[name="allowed_ips"]` 에 줄바꿈 구분 문자열로 조립합니다.
(`app/admin/templates/services/new.html:31-42` — 등록 폼,
 `app/admin/templates/services/detail.html` — 상세 폼)

---

## 8. 취소 정책

단건(일반) 결제 취소 시 적용되는 정책입니다. 구독 정기결제에는 영향 없습니다.

### 8-1. 필드

| 컬럼 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `cancellation_enabled` | Boolean | `true` | 단건결제 취소 허용 여부 |
| `cancellation_fee_percent` | Integer | `0` | 취소 시 차감 수수료율 (0~100 %) |

(`app/models/service.py:30-33`)

### 8-2. 검증 규칙

- `cancellation_fee_percent` 는 0~100 정수만 허용
- 범위 초과 시 `InputValidationError` (`app/services/registry.py:130-131`)
- 정수가 아닌 값이 폼에서 들어오면 라우트에서 먼저 폼 오류 처리
  (`app/admin/routes/services.py:206-209`, `498-502`)

### 8-3. 수정

`POST /admin/services/{id}/cancel-policy`
→ `registry.update_cancel_policy(db, service_id, enabled, fee_percent)` 가
  검증 후 두 필드를 갱신합니다.
(`app/services/registry.py:205-228`)

폼 필드:

| 필드명 | 타입 | 설명 |
|--------|------|------|
| `cancellation_enabled` | checkbox | 체크 시 `"on"` 전송, 미체크 시 키 없음(False) |
| `cancellation_fee_percent` | number | 0~100 정수 |

### 8-4. 단건결제 취소에서의 사용

외부 서비스 API의 취소 요청 처리 시 이 정책을 읽어 취소 여부 및 수수료 계산에 사용합니다.
상세 내용은 [07. 단건(일반) 결제 + 취소](07-one-off-payment.md) 참조.

---

## 9. 서비스 상태 관리

### 9-1. ServiceStatus 값

| 값 | 의미 |
|----|------|
| `ACTIVE` | 정상 운영 — API 키 인증 통과, 구독·결제 가능 |
| `INACTIVE` | 비활성화 — API 키 해시가 있어도 인증 거부(`AuthenticationError`) |

(`app/models/enums.py:13-16`)

인증 시 상태 확인:
```python
# app/api/deps.py:97
if service is None or service.status != ServiceStatus.ACTIVE:
    raise AuthenticationError(AUTH_FAILED)
```

### 9-2. 상태 전이

```
ACTIVE ◄──► INACTIVE
```

`ACTIVE ↔ INACTIVE` 전환만 허용합니다. 삭제된 서비스는 이 함수로 복구 불가합니다.
(`app/services/registry.py:238-239`)

구독이 있어 삭제가 불가능할 때 INACTIVE로 전환해 실질적으로 비활성화합니다.

### 9-3. 상태 변경

`POST /admin/services/{id}/status`
→ `registry.set_service_status(db, service_id, status)` 호출
(`app/admin/routes/services.py:514-524`, `app/services/registry.py:231-246`)

---

## 10. 서비스 삭제

### 10-1. 삭제 거부 조건

구독 이력이 1건이라도 있으면 삭제 불가합니다.
서비스 레이어가 Subscription 수를 확인한 후 `ConflictError` 를 발생시킵니다.
(`app/services/registry.py:265-268`)

```python
if sub_count:
    raise ConflictError("구독 이력이 있는 서비스는 삭제할 수 없습니다. 비활성화를 사용하세요.")
```

라우트에서 이 오류를 받으면 오류 메시지를 상세 페이지 쿼리 파라미터로 전달합니다.
(`app/admin/routes/services.py:534-538`)

### 10-2. 삭제 시 CASCADE

**요금제**: 삭제 전 서비스에 연결된 Plan 들을 먼저 하드 삭제합니다
(구독이 없으므로 안전).
(`app/services/registry.py:272-273`)

**담당자 계정**: `users.service_id` 가 이 서비스를 가리키는 User 행이
DB 레벨 `ON DELETE CASCADE` 로 함께 삭제됩니다. 이는 의도된 설계이며,
감사 로그에 `cascade_deleted_managers` 수가 기록됩니다.
(`app/services/registry.py:249-279`, `app/models/user.py:33-34`)

> **주의**: 서비스 삭제는 담당자 계정까지 지웁니다. 구독이 없어도 신중히 결정하세요.
> 구독이 있다면 `INACTIVE` 전환으로 비활성화하는 것이 권장됩니다.

---

## 11. 서비스별 Toss 시크릿 키 관리

> **배경**: T1~T8(2026-06-23) 구현으로 토스 결제 호출에 서비스별 키를 사용하는 구조로 전환됨.
> 전역 `TOSS_SECRET_KEY` 환경변수는 제거됨. 각 서비스는 어드민에서 개별 키를 등록해야 한다.

### 11-1. 개요

| 항목 | 내용 |
|------|------|
| 저장 방식 | `services.toss_secret_key_encrypted` (AES-256-GCM 암호화, `String(512) nullable`) |
| 등록 경로 | 어드민 콘솔 → 서비스 상세 → **Toss 시크릿 키** 카드 → 키 입력 → **저장** |
| 평문 노출 | 절대 없음 (화면·로그·감사 모두 평문 미표시) |
| 미설정 시 | 결제·갱신·정산·웹훅 처리 시 `TossKeyNotConfiguredError` 발생 → HTTP 422, 코드 `TOSS_KEY_NOT_CONFIGURED` |

### 11-2. 키 등록/교체 흐름

```
[어드민] POST /admin/services/{id}/toss-secret-key
     │
     ▼
[routes/services.py:421  services_set_toss_secret_key()]
  1. CSRF 검증 (validate_csrf)
  2. 폼에서 toss_secret_key 읽기 — 빈 값이면 변경 없음(기존 키 유지)
  3. set_toss_secret_key(db, cipher, service_id=..., toss_secret_key=..., actor_user_id=...) 호출
     │
     ▼
[services/registry.py:241  set_toss_secret_key()]
  4. 키 미입력(빈 문자열) → InputValidationError
  5. 기존 키 존재 여부 확인 (set vs changed 구분)
  6. cipher.encrypt(toss_secret_key) → toss_secret_key_encrypted 갱신
  7. record_audit(action="service.toss_secret_key.set" 또는 "service.toss_secret_key.changed")
     - 감사 detail에 평문 키 값 절대 미기록
  8. db.commit()
```

### 11-3. 서비스별 키 사용 — TossClientProvider

모든 토스 API 호출(`TossClient`)은 `TossClientProvider.for_service(service)` 로 서비스별 키를 복호화해 생성한다.

```python
# app/toss/provider.py:35  for_service()
def for_service(self, service) -> TossClient:
    enc = getattr(service, "toss_secret_key_encrypted", None)
    if not enc:
        raise TossKeyNotConfiguredError()  # HTTP 422, 코드 TOSS_KEY_NOT_CONFIGURED
    secret = self._cipher.decrypt(enc)
    ...
```

적용 대상:
- `app/api/v1/subscriptions.py` (구독 생성·결제)
- `app/api/v1/payments.py` (단건결제·취소)
- `app/admin/routes/` (어드민 결제·정산)
- `app/services/renewals.py` (자동 갱신 배치)
- `app/services/reconciliation.py` (정산)
- `app/services/webhooks.py` (웹훅 처리)

### 11-4. 설계 결정 사항

| 결정 | 이유 |
|------|------|
| 서비스별 개별 키 저장 (전역 키 제거) | 서비스 간 키 격리 — 한 서비스 키 노출이 타 서비스에 영향 없음 |
| AES-256-GCM 암호화 저장, 평문 미노출 | 키 분실 시 재발급 유도; DB 덤프·로그 유출 방지 |
| 키 미설정 시 422 거부 (등록 선택) | 서비스 등록 직후 즉시 키 없이도 운영 가능(구독 없는 서비스 등 고려) |
| 감사 로그에 평문 값 미기록 | set/changed 사실만 기록, 키 값은 기록 안 함 |
| client_key(토스 위젯 공개키) 서버 미저장 | 프론트 전용 공개키이므로 서버에 저장할 필요 없음 |

### 11-5. 배포 순서 주의

> **주의**: 서비스별 키 체계로 전환 시 다음 순서를 반드시 지켜야 한다.

```
1. alembic upgrade head  — toss_secret_key_encrypted 컬럼 추가
2. 어드민에서 각 서비스의 Toss 시크릿 키 등록
3. .env / .env.prod 에서 TOSS_SECRET_KEY 항목 제거
```

키를 등록하기 전에 `TOSS_SECRET_KEY` 를 먼저 삭제하면 결제 기능이 동작하지 않는다.

### 11-6. 감사 로그 action 추가

| action | 설명 |
|--------|------|
| `service.toss_secret_key.set` | Toss 시크릿 키 최초 등록 (값 미기록) |
| `service.toss_secret_key.changed` | Toss 시크릿 키 교체 (값 미기록) |

---

## 12. 사용하는 DB 테이블·컬럼

### 쓰기 대상

| 테이블 | 작업 | 설명 |
|--------|------|------|
| `services` | INSERT | 서비스 등록 |
| `services` | UPDATE | 키 재발급, IP 수정, 취소정책 수정, 상태 변경, 대표 담당자 변경, toss_secret_key 등록 |
| `services` | DELETE | 서비스 삭제 |
| `users` | UPDATE | 담당자 할당/해제 시 `service_id` 갱신 |
| `user_services` | INSERT/DELETE | 다대다 담당자 할당/해제 |
| `plans` | DELETE | 서비스 삭제 시 연결 요금제 삭제 |
| `audit_logs` | INSERT | 모든 쓰기 작업마다 감사 로그 기록 |

### 읽기 대상

| 테이블 | 설명 |
|--------|------|
| `services` | 목록 조회, 상세 조회, 중복 이름 확인 |
| `users` | 담당자 목록 조회, 역할·상태 검증 |
| `user_services` | 담당자 스코프 조회, 중복 할당 확인 |
| `subscriptions` | 삭제 전 구독 이력 존재 확인 |
| `plans` | 상세 탭 요금제 목록 |
| `payments` | 상세 탭 단건결제 목록 |

### `services` 테이블 주요 컬럼 (`app/models/service.py:12-40`)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | UUID PK | 서비스 식별자 |
| `name` | String(100) UNIQUE | 서비스명 |
| `allowed_ips` | JSONB | IPv4 허용 목록 |
| `manager_email` | String(255) | 대표 담당자 알림 이메일 |
| `api_key_hash` | String(64) UNIQUE INDEX | SHA-256 해시, 인증 검증용 |
| `api_key_encrypted` | String(512) NULL | AES-GCM 암호문, 화면 복사용 |
| `hmac_secret_encrypted` | String(512) | AES-GCM 암호문 |
| `status` | String(20) | `ACTIVE` \| `INACTIVE` |
| `cancellation_enabled` | Boolean default=true | 단건결제 취소 허용 여부 |
| `cancellation_fee_percent` | Integer default=0 | 취소 수수료율 |
| `toss_secret_key_encrypted` | String(512) nullable | 서비스별 토스 시크릿(AES-256-GCM 암호화). NULL = 미설정 |

---

## 13. 권한·CSRF

모든 서비스 관리 엔드포인트는 **SYSTEM_ADMIN 전용**입니다.
SERVICE_MANAGER 계정은 403 응답을 받습니다.
(`app/admin/routes/services.py:4`, `app/admin/deps.py:100`)

모든 POST 요청은 반드시 CSRF 검증을 거칩니다:
- 폼 hidden 필드 `csrf_token` 값
- 또는 `X-CSRF-Token` 헤더 값
이 세션에 저장된 토큰과 불일치하면 `PermissionDeniedError(403)` 가 발생합니다.
(`app/admin/deps.py:105-110`)

---

## 14. 예외·엣지 케이스

| 상황 | 발생 위치 | 동작 |
|------|-----------|------|
| 서비스명 중복 | `registry.py:134` | `ConflictError` → 폼 오류 메시지 재렌더 |
| 공백만 있는 서비스명 | `registry.py:125-127` | `InputValidationError` |
| 잘못된 IP 형식 | `registry.py:70-71` | `InputValidationError` |
| IP 목록 비어있음 | `registry.py:65-66` | `InputValidationError` |
| 취소 수수료율 범위 초과 | `registry.py:130-131` | `InputValidationError` |
| 대표 담당자 미지정 | `registry.py:79` | `InputValidationError` |
| DELETED 계정을 담당자로 지정 | `registry.py:87-89` | `InputValidationError` |
| SYSTEM_ADMIN 계정을 담당자로 지정 | `registry.py:87-89` | `InputValidationError` |
| 구독 있는 서비스 삭제 | `registry.py:267-268` | `ConflictError` → 오류 메시지 + 서비스 유지 |
| 대표 담당자 해제 시도 | `routes/services.py:431-435` | 오류 메시지 + 해제 거부 |
| 키 복호화 실패 (모달) | `routes/services.py:246-250` | `decrypt_error=True` → 모달에 안내 문구 |
| `api_key_encrypted = None` (구버전 서비스) | `routes/services.py:247` | `None` 체크 후 안내 문구 |

---

## 15. 관련 테스트

### 통합 테스트

| 파일 | 주요 검증 내용 |
|------|---------------|
| `tests/integration/test_registry.py` | `register_service`, `rotate_keys`, `update_allowed_ips`, `delete_service`, `set_primary_manager` 각 함수의 정상·오류 케이스 |
| `tests/integration/test_accounts.py` | `assign_service`, `unassign_service`, 대표 담당자 보호, 이메일 변경 시 동기화 |

### e2e 테스트

| 파일 | 주요 검증 내용 |
|------|---------------|
| `tests/e2e/test_admin_services_plans.py` | 서비스 등록 → 키 1회 표시, IP 오류, rotate, 삭제 거부, 권한 검사 |
| `tests/e2e/test_service_detail_page.py` | IP 옥텟 UI, 키 복사 모달(Cache-Control·감사로그), 취소정책 폼, 대표 담당자 UI/보호 |

### 주요 테스트 케이스 예시

```python
# 키가 등록 화면에서 1회만 보이는지 확인
# tests/e2e/test_admin_services_plans.py:22-43
async def test_register_service_shows_keys_once(client, db, redis_client):
    ...
    # 상세 페이지에는 키가 다시 노출되지 않음
    detail = await client.get(f"/admin/services/{svc.id}")
    assert api_key not in detail.text

# 구독 있는 서비스 삭제 거부
# tests/integration/test_registry.py:151-156
async def test_delete_service_blocked_when_subscription_exists(db, cipher):
    ...
    with pytest.raises(ConflictError):
        await delete_service(db, svc.id)

# 키 재발급 시 기존 키 해시가 교체됨
# tests/integration/test_registry.py:131-139
async def test_rotate_keys_invalidates_old(db, cipher):
    ...
    assert svc.api_key_hash == sha256_hex(new_api_key)
    assert svc.api_key_hash != sha256_hex(creds.api_key)
```

---

## 16. 유지보수 팁

### 16-1. API 키를 분실한 경우

키 발급 시 1회만 평문을 보여주기 때문에 평문을 저장해두지 않으면 재조회할 수 없습니다.
단, AES-GCM 암호문(`api_key_encrypted`)으로 어드민 화면 키 복사 모달에서 복호화해서 볼 수 있습니다.

1. 어드민 → 서비스 상세 → 상단 **키 복사** 버튼 클릭
2. 모달에서 API 키·HMAC 시크릿 확인
3. 그래도 분실이면 **키 재발급** 버튼으로 회전

키 회전 절차:
1. 어드민 콘솔 → 서비스 상세 → **키 재발급** 클릭
2. 화면에 표시된 새 API 키·HMAC 시크릿을 즉시 복사
3. 외부 서비스(사내 앱) 환경 변수/설정 파일을 새 키로 교체
4. 외부 서비스 재배포·재시작

> **주의**: 재발급 직후 기존 키로 오는 요청은 즉시 401을 받습니다.
> 외부 서비스 배포 전까지 API 호출이 실패합니다.

### 16-2. 서비스를 삭제 대신 비활성화해야 하는 경우

구독 이력이 있으면 삭제가 불가하므로, 서비스를 중단하려면 **INACTIVE** 로 전환합니다:
- 어드민 → 서비스 상세 → **비활성화** 버튼
- INACTIVE 서비스는 API 키 인증에서 즉시 거부됩니다 (`app/api/deps.py:97`)
- 나중에 다시 **활성화** 버튼으로 복구 가능

### 16-3. 취소 정책을 바꾸려면

어드민 → 서비스 상세 → **일반결제 취소 정책** 카드:
- 취소 허용 체크박스 토글
- 수수료율 0~100 입력
- **저장** 클릭

0이면 전액 환불, 초과이면 `결제금액 × (1 - fee_percent/100)` 이 환불됩니다.

### 16-4. 허용 IP를 바꾸려면

어드민 → 서비스 상세 → **허용 IP** 카드:
- 옥텟 칸에 IP 입력 (IP 추가 버튼으로 행 추가)
- **저장** 클릭

저장 즉시 적용되며, 제거된 IP에서 오는 요청은 다음 인증부터 403 을 받습니다.

### 16-5. 담당자가 퇴사한 경우

1. 어드민 → 서비스 상세 → 담당자 계정 섹션
2. 퇴사자가 대표(`manager_email`)이면 먼저 다른 계정을 **대표로 지정**
3. 퇴사자 행의 **해제** 버튼 클릭
4. 계정 자체를 삭제하려면 어드민 → 계정 관리에서 삭제 (상세 절차: [13. 어드민 계정](13-admin-accounts.md))

### 16-6. 서비스 등록 폼에 담당자가 없는 경우

서비스 등록 폼(`/admin/services/new`)에는 `SERVICE_MANAGER` 역할의 계정이 없으면
IP·취소정책 폼 대신 "담당자 계정을 먼저 만드세요" 안내만 표시됩니다.
계정을 먼저 만든 후 다시 등록 폼에 접근하세요.
(`app/admin/templates/services/new.html:61-66`)

### 16-7. 서비스 등록/키 관련 감사 로그 action 목록

| action | 설명 |
|--------|------|
| `service.register` | 서비스 최초 등록 |
| `service.rotate_keys` | API 키·HMAC 시크릿 재발급 |
| `service.update_ips` | 허용 IP 수정 (detail에 새 IP 목록 포함) |
| `service.cancel_policy_updated` | 취소 정책 수정 (detail에 enabled·fee_percent 포함) |
| `service.set_status` | 서비스 상태 변경 (detail에 status 포함) |
| `service.set_primary_manager` | 대표 담당자 변경 (detail에 email 포함) |
| `service.delete` | 서비스 삭제 (detail에 name·cascade_deleted_managers 포함) |
| `service.keys_viewed` | 키 복사 모달 조회 (평문 키 열람 이력) |
| `account.assign_service` | 담당자 추가 할당 |
| `account.unassign_service` | 담당자 해제 |

감사 로그 구현: `app/services/audit.py:15` — `record_audit()` (commit은 호출자가 담당)
