# 03. 서비스 관리

> **대상**: SYSTEM_ADMIN 전용 (`require_admin` — `app/admin/deps.py:100`).
> SERVICE_MANAGER 는 이 화면에 **접근 불가** (403 PermissionDeniedError).

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

**서비스 관리**는 사내 구독/결제 시스템을 이용하려는 **외부 서비스(클라이언트 앱)**를 등록·관리하는 화면이다.
운영자는 여기서 서비스를 등록하고 발급된 API 키를 외부 서비스 담당자에게 전달한다.

| 항목 | 내용 |
|------|------|
| 접근 경로 | 사이드바 → **서비스** |
| 권한 | SYSTEM_ADMIN 전용 |
| 기본 URL | `GET /admin/services` |

---

## 2. 화면 구성

### 2-1. 서비스 목록

**경로**: `GET /admin/services`
**템플릿**: `app/admin/templates/services/list.html` (전체), `services/_table.html` (htmx partial)

| 컬럼 | 설명 | 정렬 |
|------|------|------|
| 이름 | `Service.name` | 가능 (`sort=name`) |
| 담당자 | `Service.manager_email` (대표 담당자) | 불가 |
| 허용 IP | `Service.allowed_ips` 콤마 결합 표시 | 불가 |
| 상태 | `ACTIVE` / `INACTIVE` 뱃지 | 가능 (`sort=status`) |

- **기본 정렬**: `created_at DESC`
- **검색 (`q`)**: `Service.name` 또는 `manager_email` 부분 일치 (`_table.html:3`, `services.py:49-50`)
- **상태 필터**: `ACTIVE` / `INACTIVE` / 전체 드롭다운 (`_table.html:4`)
- **행 클릭** → 해당 서비스 상세로 이동 (`_table.html:16`)
- **페이지당 기본 15건** (`pagination.py:18`)

---

### 2-2. 서비스 상세

**경로**: `GET /admin/services/{id}`
**템플릿**: `app/admin/templates/services/detail.html`

상단 헤더: 서비스명 + 상태 뱃지 + 버튼 그룹(활성화/비활성화·키 복사·키 재발급·삭제)

**상단 카드 2열**

| 영역 | 내용 |
|------|------|
| 개요 카드 (좌) | 요금제 수 / 구독 건수 / 담당자 목록 (대표 뱃지·수정·대표 지정·삭제 링크) + 담당자 추가 폼 |
| 허용 IP 카드 (우) | 옥텟 입력 UI로 IP 목록 표시·편집 |

**취소 정책 카드**: 일반결제 취소 허용 체크박스 + 수수료율(%) 입력

**하단 탭 3개** (htmx 부분 갱신, 각 partial)

| 탭 | `id` | 부분 템플릿 |
|----|------|------------|
| 요금제 | `list-svc-plans` | `services/_plans_table.html` |
| 구독 | `list-svc-subs` | `services/_subs_table.html` |
| 일반결제 | `list-svc-oneoff` | `services/_oneoff_table.html` |

#### 요금제 탭 컬럼 (`_plans_table.html:14`)

이름 / 정가 / 체험 / 첫구독 할인 / 첫 결제액 / 상시할인 / 정기 결제액 / 주기(반복회차) / 상태

- 각 요금제마다 **수정** / **비활성화|활성화** / **삭제** 버튼
- htmx 폼 (`hx-post`, `hx-target="#list-svc-plans"`, `hx-swap="outerHTML"`) — partial만 교체

#### 구독 탭 컬럼 (`_subs_table.html:11-18`)

사용자(external_user_id) / 요금제 / 상태 / 만료일 / 다음 결제

- 검색(`q`: external_user_id) + 상태 필터(TRIAL/ACTIVE/PAST_DUE/SUSPENDED/CANCELED/EXPIRED)
- 행 클릭 → `/admin/subscriptions/{id}` 상세

#### 일반결제 탭 컬럼 (`_oneoff_table.html`)

승인시각 / 사용자 / 주문번호 / 금액 / **환불 / 수수료** / 상태(한글)

- 취소(CANCELED) 건은 환불액(`canceled_amount`)·취소 수수료(`cancel_fee`)를 함께 표시(취소 아닌 건은 `-`), 상태는 `payment_status_ko()`로 한글 표기.
- 엑셀 다운로드(`/oneoff.xlsx`)도 동일하게 환불·수수료 컬럼을 포함한다.

#### 이벤트 섹션 (요청 015 — 서비스 상세 하단)

서비스 상세 맨 아래 **이벤트** 카드가 이 서비스 관련 감사 이력(최근 50건)을 보여준다(`_service_events()` in `services.py`). 컬럼: 시각 / 활동(한글) / 상세 / 행위자. 포함 범위:
- 서비스 자체 동작: `target_type='service'` (등록·상태변경·키재발급·키복사·IP갱신·취소정책·대표지정)
- 요금제 CRUD: 이 서비스의 `target_type='plan'` 이벤트
- 담당자 추가/해제: `account.(un)assign_service` 중 `detail.service_id`가 이 서비스

각 동작은 **변경 전 → 변경 후**(`detail_summary()`)로 표시된다. 예: "상태 ACTIVE → INACTIVE", "허용 IP 10.0.0.1 → 10.0.0.1, 10.0.0.2", "취소 수수료율(%) 0 → 10". 우상단 "감사로그" 버튼은 서비스명으로 검색된 `/admin/audit`로 이동.

---

## 3. 할 수 있는 동작

### 3-1. 운영자 흐름: 서비스 등록 → 키 전달 → 담당자 지정 → IP/취소정책 설정 → Toss 키 등록

```
① 서비스 등록 (new.html) → ② API 키 / HMAC Secret 1회 표시 (keys.html) → 복사 후 담당자 전달
→ ③ 상세에서 담당자 추가/대표 지정 → ④ 허용 IP 설정 → ⑤ 취소 정책 설정
→ ⑥ Toss 시크릿 키 등록 (필수: 결제 사용 전)
→ 운영 시작
```

> 키는 등록 직후 또는 재발급 직후에만 평문으로 표시된다. 이후 상세 화면에서 **키 복사** 버튼(모달)으로 재확인할 수 있지만, 서버 AES-GCM 복호화가 필요하므로 환경 변수 `CIPHER_KEY` 설정이 일치해야 한다.

---

### 3-2. 서비스 등록

| 항목 | 설명 |
|------|------|
| 경로 | `GET /admin/services/new` → 폼 / `POST /admin/services` → 생성 |
| 성공 결과 | `services/keys.html` — API 키·HMAC Secret 평문 **1회** 표시 |
| 실패 결과 | `new.html` 상단에 `error` 메시지 인라인 표시 |

**입력 항목** (`new.html:13-58`)

| 필드 | 형식 | 필수 | 비고 |
|------|------|------|------|
| 서비스명 | 텍스트 | 필수 | 중복 불가 |
| 담당자 계정 | 체크박스 복수 선택 (`manager_ids`) | — | SERVICE_MANAGER 중 DELETED 제외 목록 |
| 대표 계정 | 셀렉트 (`primary_user_id`) | 필수 | 알림 메일 수신처 — 체크 목록에 없어도 자동 담당자 포함 |
| 허용 IP | 옥텟 입력 UI (숫자만, 줄당 1개) | 필수 1개↑ | IPv4 전용, IPv6·CIDR 불가 |
| 일반결제 취소 허용 | 체크박스 (`cancellation_enabled`) | — | 기본 체크(True) |
| 취소 수수료(%) | 숫자 0~100 (`cancellation_fee_percent`) | — | 기본 0 (전액 환불) |

> **담당자 계정이 없는 경우**: `new.html:62-66` 에 안내 문구 + "계정 추가" 링크가 표시된다. 먼저 `/admin/users/new`에서 SERVICE_MANAGER 계정을 만들어야 한다.

---

### 3-3. API 키 발급 화면 (keys.html) / 키 모달

**keys.html** (`app/admin/templates/services/keys.html`)

- 등록 직후(`POST /admin/services`) 또는 키 재발급 후(`POST .../rotate-keys`) 렌더됨
- **서비스 API 키** + **HMAC Secret** 각각 박스 표시 + 복사 버튼
- `notice` 안내: "아래 키는 지금 한 번만 표시됩니다" (`keys.html:6`)
- 완료 후 "서비스 상세로" 버튼 → `/admin/services/{id}`

**키 복사 모달** (`_keys_modal.html`)

- 트리거: 상세 페이지 상단 **"키 복사"** 버튼 → `hx-get="/admin/services/{id}/keys-modal"` `hx-target="body"` `hx-swap="beforeend"` (`detail.html:15-18`)
- `Cache-Control: no-store` 헤더 강제 설정 (`services.py:258`)
- 복호화 실패 시 `decrypt_error=True` → 모달 내 오류 안내 (`_keys_modal.html:6-8`)
- API 키가 없는 경우(암호화 저장 이전 등록) → 별도 안내 및 재발급 유도 (`_keys_modal.html:17-19`)
- 감사 로그: `service.keys_viewed` (`services.py:251-253`)

---

### 3-4. 키 재발급

- 버튼: 상세 상단 **"키 재발급"** — `data-confirm` 확인 모달 (`detail.html:19-24`)
  - 확인 텍스트: "기존 키는 즉시 무효화되고 새 키가 발급됩니다."
- `POST /admin/services/{id}/rotate-keys` → 새 키·시크릿 생성 → `services/keys.html` 렌더
- **주의**: 재발급 즉시 기존 키는 무효. 외부 서비스에서 새 키로 재설정 필요.

---

### 3-5. 담당자 추가

- 상세 개요 카드 → **"담당자 추가"** 버튼 → `#assign-form` 토글 노출
- 드롭다운에서 담당 가능 계정 선택 → **추가** 클릭
- `POST /admin/services/{id}/assign-manager` (폼 필드: `user_id`)
- 성공: `saved_redirect` → "저장되었습니다" 모달
- 실패: `?error=` 쿼리파람으로 상세 페이지 리다이렉트 → 토스트 표시

---

### 3-6. 대표 담당자 지정

- 담당자 목록에서 비대표 계정의 **"대표 지정"** 버튼 클릭
  - `data-confirm` 확인 모달: "알림 메일 수신처가 이 계정으로 변경됩니다."
- `POST /admin/services/{id}/primary-manager` (폼 필드: `user_id`)
- 성공: `saved_redirect` → "변경되었습니다" 모달
- **효과**: `Service.manager_email` = 선택한 계정 이메일로 갱신

---

### 3-7. 담당자 해제

- 담당자 목록에서 비대표 계정의 **"삭제"** 버튼 클릭
  - `data-confirm` 확인 모달: "계정은 유지되고 이 서비스의 담당에서만 제외됩니다."
- `POST /admin/services/{id}/managers/{user_id}/remove`
- **대표 담당자는 해제 불가** — 먼저 다른 계정을 대표로 지정한 후 해제 (`services.py:431-435`)
- 성공: `saved_redirect` → "해제되었습니다" 모달

---

### 3-8. 허용 IP 업데이트

- 상세 허용 IP 카드 → 옥텟 UI로 IP 추가/삭제 → **"IP 갱신"** 버튼
- `POST /admin/services/{id}/ips` (폼 필드: `allowed_ips` — 줄바꿈/콤마 구분 평문)
  - `_parse_ips()` 가 줄바꿈과 콤마 모두 처리 (`services.py:57-60`)
- IPv4 전용, 최소 1개 필수. 위반 시 오류 → `?error=` 리다이렉트

---

### 3-9. 취소 정책 설정

- 상세 취소 정책 카드 → 체크박스·수수료율 입력 → **"저장"** 버튼
- `POST /admin/services/{id}/cancel-policy`
  - `cancellation_enabled`: 체크 시 `"on"` / 미체크 시 폼 키 자체 없음 → `False` 처리
  - `cancellation_fee_percent`: 0~100 정수. 비정수 입력은 오류 처리
- 성공: `saved_redirect` → "저장되었습니다" 모달

---

### 3-10. 상태 변경 (활성화 / 비활성화)

- 상세 헤더 버튼: `ACTIVE` 상태이면 **"비활성화"**, `INACTIVE`이면 **"활성화"** 표시
- `POST /admin/services/{id}/status` (폼 필드: `status` — `ACTIVE` 또는 `INACTIVE`)
- `ACTIVE ↔ INACTIVE` 만 허용 (`registry.py:238-239`)
- 성공: `saved_redirect` → "변경되었습니다" 모달
- **구독 있어 삭제 불가할 때 대신 INACTIVE 사용을 권장**

---

### 3-11. 서비스 삭제

- 상세 헤더 **"삭제"** 버튼 → `data-confirm` 확인 모달
  - 확인 텍스트: "구독 이력이 있는 서비스는 삭제할 수 없습니다. 정말 삭제할까요?"
- `POST /admin/services/{id}/delete`
- **구독 이력이 1건이라도 있으면 삭제 불가** → `?error=` 리다이렉트
- 삭제 성공: 해당 서비스의 요금제도 함께 하드 삭제. 담당자 `User` 계정은 DB `ON DELETE CASCADE` 로 함께 삭제 (`registry.py:272-274`)
- 성공: `saved_redirect("/admin/services", "삭제되었습니다")`

---

### 3-12. Toss 시크릿 키 등록

> **배경**: 2026-06-23부터 전역 `TOSS_SECRET_KEY` 환경변수가 제거됨.
> 결제 기능을 사용하려면 각 서비스마다 개별 토스 시크릿 키를 어드민에서 등록해야 한다.

- 위치: 서비스 상세 → **Toss 시크릿 키** 카드
- 경로: `POST /admin/services/{id}/toss-secret-key`
- 폼 필드: `toss_secret_key` — 빈 값이면 기존 키 유지 (삭제 불가)
- 저장 방식: AES-256-GCM 암호화 (`services.toss_secret_key_encrypted`). 평문은 화면·로그에 절대 표시하지 않음.
- 감사 로그:
  - 최초 등록: `service.toss_secret_key.set`
  - 교체: `service.toss_secret_key.changed`
  - (값 미기록 — set/changed 사실만 기록)

**키 미설정 시 결제 거부**:
키가 등록되지 않은 서비스에서 결제·갱신·정산·웹훅 처리를 시도하면
`TossKeyNotConfiguredError` (HTTP 422, 코드 `TOSS_KEY_NOT_CONFIGURED`) 가 반환된다.

---

### 3-13. 엑셀 다운로드

| 화면 | 버튼 위치 | URL | 컬럼 |
|------|-----------|-----|------|
| 목록 | 툴바 | `GET /admin/services/export.xlsx` | 서비스명 / 담당자 이메일 / 허용 IP / 상태 |
| 상세 요금제 탭 | 탭 헤더 | `GET /admin/services/{id}/plans.xlsx` | 요금제 / 결제주기 / 정가 / 첫 결제 / 정기 결제 / 상태 |
| 상세 구독 탭 | 탭 헤더 | `GET /admin/services/{id}/subs.xlsx` | 사용자 / 요금제 / 상태 / 만료일 / 다음 결제 |
| 상세 일반결제 탭 | 탭 헤더 | `GET /admin/services/{id}/oneoff.xlsx` | 승인시각 / 사용자 / 주문번호 / 금액 / 상태 |

현재 검색/필터가 목록·구독 탭 엑셀에 동일하게 적용된다 (`services.py:91-97`, `108-122`).

---

## 4. 개발 참조

### 4-1. 라우트 함수 일람

| 메서드 | 경로 | 함수 | file:line | 호출 서비스 |
|--------|------|------|-----------|-------------|
| GET | `/admin/services` | `services_list` | `services.py:63` | — |
| GET | `/admin/services/export.xlsx` | `services_export` | `services.py:86` | — |
| GET | `/admin/services/new` | `services_new` | `services.py:171` | — |
| POST | `/admin/services` | `services_create` | `services.py:179` | `registry.register_service` |
| GET | `/admin/services/{id}` | `services_detail` | `services.py:301` | — |
| GET | `/admin/services/{id}/keys-modal` | `services_keys_modal` | `services.py:225` | `record_audit` |
| POST | `/admin/services/{id}/rotate-keys` | `services_rotate` | `services.py:442` | `registry.rotate_keys` |
| POST | `/admin/services/{id}/assign-manager` | `services_assign_manager` | `services.py:379` | `account_service.assign_service` |
| POST | `/admin/services/{id}/primary-manager` | `services_set_primary_manager` | `services.py:398` | `registry.set_primary_manager` |
| POST | `/admin/services/{id}/managers/{user_id}/remove` | `services_remove_manager` | `services.py:417` | `account_service.unassign_service` |
| POST | `/admin/services/{id}/ips` | `services_update_ips` | `services.py:462` | `registry.update_allowed_ips` |
| POST | `/admin/services/{id}/cancel-policy` | `services_cancel_policy` | `services.py:480` | `registry.update_cancel_policy` |
| POST | `/admin/services/{id}/status` | `services_set_status` | `services.py:514` | `registry.set_service_status` |
| POST | `/admin/services/{id}/delete` | `services_delete` | `services.py:527` | `registry.delete_service` |
| POST | `/admin/services/{id}/toss-secret-key` | `services_set_toss_secret_key` | `services.py:421` | `registry.set_toss_secret_key` |
| GET | `/admin/services/{id}/subs.xlsx` | `service_subs_export` | `services.py:100` | — |
| GET | `/admin/services/{id}/oneoff.xlsx` | `service_oneoff_export` | `services.py:125` | — |
| GET | `/admin/services/{id}/plans.xlsx` | `service_plans_export` | `services.py:144` | — |

모든 라우트는 `require_admin` (`app/admin/deps.py:100`) — SYSTEM_ADMIN 전용.

---

### 4-2. 서비스 레이어

| 함수 | 파일:line | 동작 요약 |
|------|-----------|-----------|
| `registry.register_service` | `app/services/registry.py:94` | 서비스 생성 + API 키/HMAC 발급 + 담당자 배정 + 감사 로그 |
| `registry.rotate_keys` | `app/services/registry.py:177` | API 키/HMAC 재생성, 기존 즉시 무효 |
| `registry.update_allowed_ips` | `app/services/registry.py:193` | IP 목록 전체 교체 (최소 1개 IPv4 필수) |
| `registry.update_cancel_policy` | `app/services/registry.py:205` | 취소 허용 여부·수수료율 갱신 |
| `registry.set_service_status` | `app/services/registry.py:231` | ACTIVE/INACTIVE 상태 전환 |
| `registry.set_primary_manager` | `app/services/registry.py:288` | manager_email 갱신 |
| `registry.delete_service` | `app/services/registry.py:249` | 구독 있으면 ConflictError, 요금제 선삭제 후 서비스 삭제 |
| `account_service.assign_service` | `app/services/accounts.py:269` | 담당자 추가 (주 없으면 주, 있으면 UserService 다대다) |
| `account_service.unassign_service` | `app/services/accounts.py:294` | 담당자 해제 (주 해제 시 다른 서비스가 주로 승격) |

---

### 4-3. 템플릿 구조

```
app/admin/templates/services/
├── list.html           — 목록 전체 페이지 (base.html 확장)
├── _table.html         — 목록 htmx partial (id="list-services")
├── new.html            — 등록 폼
├── keys.html           — 키 발급 1회 표시 화면
├── detail.html         — 상세 전체 페이지
├── _keys_modal.html    — 키 복사 모달 fragment
├── _plans_table.html   — 요금제 탭 partial (id="list-svc-plans")
├── _subs_table.html    — 구독 탭 partial (id="list-svc-subs")
└── _oneoff_table.html  — 일반결제 탭 partial (id="list-svc-oneoff")
```

---

### 4-4. htmx 동작 상세

**목록 partial 갱신** (`_table.html:2-30`)
- 검색·필터·정렬·페이지네이션: htmx가 `HX-Request` 헤더를 포함해 요청 → `render_list()`가 partial만 반환 → `#list-services` 교체

**상세 탭 partial 갱신** (`services.py:339-343`)
```python
# HX-Target 값에 따라 3개 partial 중 하나만 반환
template = {
    "list-svc-plans":  "services/_plans_table.html",
    "list-svc-subs":   "services/_subs_table.html",
    "list-svc-oneoff": "services/_oneoff_table.html",
}.get(hx_target, "services/detail.html")
```

**요금제 탭 내 변이(비활성화/활성화/삭제)** (`_plans_table.html:41-66`)
- `hx-post` + `hx-target="#list-svc-plans"` + `hx-swap="outerHTML"` — 탭 partial만 교체
- 라우트는 `/admin/plans/{id}/archive`, `/admin/plans/{id}/activate`, `/admin/plans/{id}/delete`
  - `next` 폼 필드로 현재 서비스 상세 URL 전달 → 303 리다이렉트 후 htmx가 HX-Target 분기로 partial만 수신

**키 모달** (`detail.html:15-18`)
- `hx-get` + `hx-target="body"` + `hx-swap="beforeend"` — body 끝에 모달 fragment 삽입
- 응답에 `Cache-Control: no-store` 설정 (`services.py:258`)

---

### 4-5. 담당자 데이터 구조

`_service_managers()` (`services.py:353-376`) 가 두 경로를 합산:
- `User.service_id == service_id` → 주 담당자 계정
- `UserService.service_id == service_id` → 추가 담당 다대다

대표 담당자 식별: `m.email == service.manager_email` (detail.html:44)

---

### 4-6. 정렬 가능 컬럼 맵

```python
# services.py:36-38
_SVC_SORT = {"name": Service.name, "status": Service.status, "created_at": Service.created_at}
ONEOFF_SORT = {"requested_at": Payment.requested_at}
```

구독 탭 정렬은 `/admin/routes/subscriptions.py`의 `SUB_SORT` 를 임포트해 공유 (`services.py:19`).

---

### 4-7. 관련 기능 문서

- 서비스 등록·키 관리·IP 검증 내부: [../09-services-registry.md](../09-services-registry.md)
- 요금제 생성·수정·상태: [04-plans.md](04-plans.md)
- 구독 조회·강제취소: [05-subscriptions.md](05-subscriptions.md)
- 담당자 계정 생성·수정: [08-users.md](08-users.md)
- 감사 로그 확인: [09-audit.md](09-audit.md)

---

## 5. 주의사항 / 자주 하는 실수

### 키 관련
- **API 키·HMAC Secret은 발급 직후만 평문으로 표시된다.** 등록 완료 화면(keys.html) 또는 재발급 직후 반드시 복사해 담당자에게 전달할 것.
- 이후 **"키 복사"** 모달은 서버 AES-GCM 복호화를 수행하므로 `CIPHER_KEY` 환경변수가 일치하지 않으면 오류가 나타난다. 운영 환경 키 분실 시 재발급 필수.
- 키 재발급 즉시 기존 키는 무효. **외부 서비스에서 새 키로 재설정하기 전에 재발급하면 서비스 장애가 발생한다.**

### 담당자 관련
- 담당자 계정이 없으면 서비스를 등록할 수 없다 (`new.html:62-64`). 먼저 `/admin/users/new`에서 SERVICE_MANAGER 계정을 만들어야 한다.
- **대표 담당자는 담당자 해제 불가**. 다른 계정을 먼저 대표로 지정한 후 해제한다 (`services.py:431-435`).
- 대표 담당자(`service.manager_email`)는 결제 실패·구독 갱신 등 알림 메일 수신처다.

### 삭제 관련
- **구독 이력이 1건이라도 있으면 삭제 불가**. 운영 중단 시에는 삭제 대신 `INACTIVE` 상태로 전환할 것.
- 서비스 삭제 시 담당자 User 계정이 DB `ON DELETE CASCADE`로 함께 삭제된다. 담당자 계정이 다른 서비스에도 배정된 경우에도 계정 자체가 사라지므로 **주의**.

### IP 관련
- 허용 IP는 **최소 1개 이상 IPv4 주소** 필수. 빈 목록은 서비스 레이어에서 거부된다 (`registry.py:65-66`).
- IPv6·CIDR 형식은 지원하지 않는다 (`registry.py:68-70`).

### 취소 정책
- `cancellation_enabled` 체크박스는 미체크 시 폼 데이터에 키 자체가 없다(HTML 표준). 서버가 `"on"` 값 유무로 처리하므로 JavaScript 없이도 정상 동작한다.
- 수수료율 0%는 전액 환불, 0% 초과는 해당 비율 차감 후 환불.

### Toss 시크릿 키
- **결제 사용 전 필수 등록**: Toss 시크릿 키가 없으면 구독 생성·결제·갱신 등 모든 토스 호출이 422로 거부된다. 서비스 등록 직후 꼭 키를 등록할 것.
- 키 값은 화면(입력 후 저장 즉시 사라짐)·로그·감사 상세에 절대 표시되지 않는다.
- 기존 키 교체: 폼에 새 키 입력 → 저장. 빈 값으로 저장하면 기존 키가 유지된다(삭제 불가).

### htmx
- 요금제 탭의 비활성화/활성화/삭제 동작은 `hx-push-url` 를 **사용하지 않는다** (`_plans_table.html:38-39`). 변이 요청이 URL 히스토리를 오염시키지 않도록 의도된 설계.
- 키 모달 응답에는 항상 `Cache-Control: no-store` 가 붙는다. 브라우저 history 복원이나 캐시로 평문 키가 노출되지 않도록 하기 위함.
