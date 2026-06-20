# 04. 요금제 관리 (목록·생성·수정·활성/비활성·삭제)

> **운영자**: 요금제를 만들고 할인·체험·자동결제안함을 설정하는 법, 금액이 어떻게 계산되어 표시되는지 확인합니다.  
> **개발자**: 라우트 함수(file:line)·템플릿·htmx 동작·호출 서비스·수정 방법을 봅니다.  
> 금액 계산 규칙의 상세(서버 코드)는 [../08-plans.md](../08-plans.md)를, billing_math 함수 목록은 `app/services/billing_math.py`를 참고하세요.

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

**요금제 관리**는 사내 서비스에 적용할 구독 요금제를 생성·수정·상태 변경·삭제하는 어드민 화면입니다.

요금제를 관리하는 화면은 **두 곳**입니다.

| 화면 | 접근 URL | 특징 |
|------|----------|------|
| **전역 요금제 목록** | `/admin/plans` | 전체(또는 담당) 서비스의 요금제를 한 번에 조회·검색·필터·엑셀 다운로드. SERVICE_MANAGER는 자신의 주 서비스에 요금제를 추가할 수 있다. |
| **서비스 상세 내 요금제 탭** | `/admin/services/{service_id}` 하단 | 특정 서비스의 요금제만 표시. SYSTEM_ADMIN과 해당 서비스 담당 SERVICE_MANAGER 모두 접근 가능. htmx로 부분 갱신. |

### 접근 권한

| 역할 | 접근 가능 범위 |
|------|--------------|
| SYSTEM_ADMIN | 전체 서비스의 요금제 조회·생성·수정·상태변경·삭제 |
| SERVICE_MANAGER | 담당 서비스의 요금제만 조회·생성·수정·상태변경·삭제. 타 서비스 요금제는 목록에서 제외되고, 직접 접근 시 404. |

> 타 서비스 요금제에 직접 접근하면 403이 아닌 **404**를 반환합니다. 요금제 존재 여부를 외부에 노출하지 않기 위해 의도적으로 선택한 방식입니다. (`app/admin/routes/plans.py:50-59` `_authorize_plan`)

---

## 2. 화면 구성

### 2-1. 전역 요금제 목록 (`/admin/plans`)

**페이지 상단**: "요금제" 제목 + [요금제 생성] 버튼(SERVICE_MANAGER에게만 표시, `app/admin/templates/plans/list.html:8-10`).

**툴바**: 요금제명 검색(q), 서비스 선택, 요금제명 선택, 결제 주기(전체/연/월/주/일), 상태(전체/ACTIVE/ARCHIVED) 필터, 엑셀 다운로드 버튼. (`app/admin/templates/plans/_table.html:4-9`)

**테이블 컬럼** (왼쪽부터, `_table.html:14-23`):

| 컬럼 | 내용 | 정렬 |
|------|------|------|
| 서비스 | 소속 서비스명(muted) | — |
| 이름 | 요금제명(bold) | 클릭 정렬 가능 |
| 정가 | 원본 가격. 상시할인이 적용되면 취소선+회색 | 클릭 정렬 가능 |
| 체험 | 체험 일수(일). 없으면 `-` | — |
| 첫구독 할인 | 없음/무료/금액(원)/율(%) | — |
| 첫 결제액 | 계산된 첫 회차 결제 금액. 마우스오버 시 계산 내역 툴팁 | — |
| 상시할인 | 없으면 `−`; 금액(원) 또는 율(%) | — |
| 정기 결제액 | 계산된 2회차 이후 결제 금액(bold). 마우스오버 시 툴팁 | — |
| 주기 | YEAR/MONTH/WEEK/DAY. DAY면 `(N일)` 함께 표시 | — |
| 상태 | ACTIVE(파란 뱃지) / ARCHIVED(회색 뱃지) | 클릭 정렬 가능 |
| 동작 버튼 | 수정 / 비활성화(또는 활성화) / 삭제 (SERVICE_MANAGER 전용) · **사용일추가**(SYSTEM_ADMIN·SERVICE_MANAGER 모두) | — |

> **사용일추가(보너스, 요청)**: 행의 "사용일추가" 버튼을 누르면 **모달이 열리고 거기서 추가할 일수를 입력**한다(입력형 확인 모달 — `admin.js`의 `data-confirm-input` 지원: 모달에 입력칸을 띄우고 확인 시 폼의 hidden `days`에 값을 채워 제출). Enter로 확인, Esc로 취소, 1~3650 범위 검증. **수정/비활성화/삭제와 달리 SYSTEM_ADMIN에게도 노출**된다(`_table.html`에서 SERVICE_MANAGER 전용 블록 밖에 배치). `POST /admin/plans/{id}/bonus-days`로 제출하면 그 요금제를 쓰는 **현재 이용 중인 구독(ACTIVE·EXTENDED·PAST_DUE)만**의 만료일·다음 결제일이 입력 일수만큼 미뤄진다(체험·정지·취소예약·만료는 대상 아님, 상태는 변경하지 않음, 다음결제 None은 그대로). 서비스 `plans.add_bonus_days()`가 단일 SQL bulk UPDATE로 처리하고 적용 구독 수를 완료 메시지로 안내하며, 감사로그(`plan.bonus_days`: 요금제·추가 일수·적용 구독 수)를 남긴다. 1~3650일 범위. 권한 검사는 `_authorize_plan`(SYSTEM_ADMIN=전체, 담당자=담당 서비스).

> 동작 버튼은 `ctx.user.role == 'SERVICE_MANAGER'`인 경우에만 렌더됩니다(`_table.html:48-69`). SYSTEM_ADMIN은 목록에서 버튼을 볼 수 없습니다 — 수정·상태변경은 서비스 상세 탭(`_plans_table.html`)에서 수행하거나, 직접 URL로 접근합니다.

**페이지네이션**: 기본 15건/페이지. 하단 페이지 이동 버튼.

---

### 2-2. 서비스 상세 내 요금제 탭 (`services/_plans_table.html`)

서비스 상세 페이지(`/admin/services/{service_id}`)의 하단 카드에 포함됩니다(`services/detail.html:127`).

**탭 상단 버튼**: [엑셀] (`/admin/services/{service_id}/plans.xlsx`) / [요금제 추가] (`/admin/services/{service_id}/plans/new`).

**테이블 컬럼** (전역 목록과 순서 동일, `_plans_table.html:14`):

`이름 / 정가 / 체험 / 첫구독 할인 / 첫 결제액 / 상시할인 / 정기 결제액 / 주기(반복회차) / 상태 / 동작`

- "서비스" 컬럼 없음(이미 특정 서비스 상세이므로).
- 표는 `table-layout:fixed` + `<colgroup>` 비율 지정으로 **전폭에 균등하게 채워진다**(요청). 동작 셀은 안쪽 flex-wrap 래퍼라 버튼이 셀 폭을 넘으면 다음 줄로 흐른다.
- 동작 버튼(수정·비활성화·활성화·삭제·**사용일추가**)이 항상 표시됩니다(권한 확인은 서버에서).
- 수정 링크: `/admin/plans/{plan_id}/edit?next=/admin/services/{service_id}` — 저장 후 서비스 상세로 돌아옵니다.
- 비활성화·활성화·삭제·**사용일추가** 폼에 `hx-post` + `hx-target="#list-svc-plans"`가 붙어 있어 성공 시 이 테이블 영역만 부분 갱신됩니다(`_plans_table.html`).
- **사용일추가**는 전역 목록(`plans/_table.html`)과 서비스 상세 탭(`services/_plans_table.html`) **양쪽 모두**에 제공된다(숫자 입력 + 버튼). `POST /admin/plans/{id}/bonus-days` → `plans.add_bonus_days()`.

---

### 2-3. 요금제 생성·수정 폼 (`plans/form.html`)

최대 너비 560px 카드 폼 (`form.html:10`). 생성·수정 모두 같은 템플릿을 사용하며 `plan` 변수 유무로 구분합니다.

**폼 입력 항목**:

| 필드 | HTML | 설명 |
|------|------|------|
| 이름 | `<input name="name">` | 요금제 표시명. 필수. |
| 가격(원) | `<input name="price" type="number" min="1">` | 정가. 1원 이상. 필수. |
| 결제 주기 | `<select name="billing_cycle">` | 월/년/주/일(일수 지정). DAY 선택 시 '반복 일수' 입력란 표시(JS). |
| 반복 일수 | `<input name="cycle_days">` | DAY 주기일 때만 표시. |
| 첫구독 할인 유형 | `<select name="first_payment_type">` | 없음(정가)/무료/할인 금액(원)/할인율(%). |
| 첫구독 할인 값 | `<input name="first_payment_value">` | 할인 금액 또는 할인율 선택 시에만 옆에 표시(JS). |
| 상시 할인 유형 | `<select name="recurring_discount_type">` | 없음(정가)/할인 금액(원)/할인율(%). |
| 상시 할인 값 | `<input name="recurring_discount_value">` | 상시 할인 유형 선택 시에만 옆에 표시(JS). |
| 체험 제공 | `<input type="checkbox" name="trial_enabled">` | 체크 시 '체험 일수' 입력란 표시. |
| 체험 일수 | `<input name="trial_days">` | 체험 체크 시만 표시. 1 이상. |
| 자동결제 안함 | `<input type="checkbox" name="auto_renew_disabled">` | 체크 시 첫 주기 후 자동갱신 없이 만료. |
| 추가정보 | `<input name="extra_key">` / `<input name="extra_value">` (한 줄씩) | 키+값 행. [+ 항목 추가] 버튼으로 행 추가. [삭제] 버튼으로 행 제거. |

**금액 미리보기 박스** (폼 하단, `form.html:130-133`):  
가격·첫구독 할인·상시 할인 값을 입력하면 JS가 실시간으로 "첫 결제 금액"과 "다음 회차부터" 금액을 미리 계산해 보여줍니다. 표시 전용이며 실제 결제는 서버가 재계산합니다.

> 수정 폼에서는 페이지 로드 시 저장된 값으로 미리보기가 초기화됩니다 (`form.html:180` `recalc()`).

**수정 폼 주의 안내** (`form.html:33`): "결제 주기를 바꾸면 진행 중인 구독의 현재 주기에는 영향이 없고, 다음 갱신부터 새 주기로 적용됩니다."

---

## 3. 할 수 있는 동작

### 3-1. 요금제 생성

**진입 경로 A — 전역 목록 [요금제 생성] 버튼** (SERVICE_MANAGER 전용):
1. `/admin/plans` 오른쪽 상단 [요금제 생성] 클릭 → `/admin/plans/new` (GET)
2. 폼 작성 후 [저장] → `POST /admin/plans`
3. 성공: "저장되었습니다" 완료 모달 → `/admin/plans` 목록으로 이동.
4. 실패: 폼 상단에 오류 메시지 표시(빨간 박스).

> 이 경로는 SERVICE_MANAGER가 자신의 **주 서비스**(ctx.user.service_id)에 요금제를 추가합니다. 서비스를 선택하는 UI가 없습니다.

**진입 경로 B — 서비스 상세 [요금제 추가] 버튼** (SYSTEM_ADMIN 또는 담당 SERVICE_MANAGER):
1. `/admin/services/{service_id}` 요금제 탭 오른쪽 [요금제 추가] 클릭 → `GET /admin/services/{service_id}/plans/new`
2. 폼 제목에 서비스명 표시 (`"요금제 생성 · {service.name}"`).
3. [저장] → `POST /admin/services/{service_id}/plans`
4. 성공: 완료 모달 → `/admin/services/{service_id}` 서비스 상세로 이동.

---

### 3-2. 요금제 수정

1. 목록 또는 서비스 상세 탭의 [수정] 클릭 → `GET /admin/plans/{plan_id}/edit`
   - 서비스 상세에서 진입 시 URL이 `/edit?next=/admin/services/{service_id}`이므로 저장 후 서비스 상세로 돌아옵니다.
2. 수정 후 [저장] → `POST /admin/plans/{plan_id}`
3. 성공: "저장되었습니다" 완료 모달 → next_url(없으면 `/admin/plans`) 이동.
4. 실패: 폼에 오류 메시지 표시.

---

### 3-3. 비활성화(보관)

- 상태가 ACTIVE인 요금제의 [비활성화] 클릭 → 확인 모달 없이 즉시 `POST /admin/plans/{plan_id}/archive` 전송.
- 성공: "보관되었습니다" 완료 모달. 상태가 ARCHIVED로 변경. 신규 구독 불가(기존 구독 유지).
- 서비스 상세 탭에서는 htmx로 요금제 테이블만 부분 갱신(`hx-target="#list-svc-plans"`).

---

### 3-4. 활성화(재활성)

- 상태가 ARCHIVED인 요금제의 [활성화] 클릭 → 즉시 `POST /admin/plans/{plan_id}/activate` 전송.
- 성공: "활성화되었습니다" 완료 모달. 상태가 ACTIVE로 변경. 신규 구독 재개.

---

### 3-5. 삭제

1. [삭제] 클릭 → 확인 모달 표시: 제목 "요금제를 삭제할까요?", 본문 "구독이 있는 요금제는 삭제할 수 없습니다. 정말 삭제할까요?", 버튼 [삭제] (`_table.html:63-64`, `_plans_table.html:61-62`).
2. [삭제] 확인 → `POST /admin/plans/{plan_id}/delete`
3. 성공: "삭제되었습니다" 완료 모달.
4. 실패(구독 있음): 삭제 거부, 목록으로 리다이렉트 + `?error=메시지` → 상단 토스트로 오류 표시.

> 구독 레코드가 1건이라도 있으면(ACTIVE·EXPIRED 등 상태 무관) 삭제가 거부됩니다. 이 경우 먼저 [비활성화]로 보관(ARCHIVED) 처리해 신규 구독을 막고, 기존 구독이 모두 만료된 후 삭제하세요.

---

### 3-6. 엑셀 다운로드

- 전역 목록 툴바의 [엑셀] → `GET /admin/plans/export.xlsx`  
  현재 검색어·필터가 모두 반영된 전체 데이터(페이지네이션 무시). 컬럼: 서비스, 요금제, 결제주기, 정가, 첫 결제, 정기 결제, 상태.
- 서비스 상세 탭 [엑셀] → `GET /admin/services/{service_id}/plans.xlsx`  
  해당 서비스의 요금제만 다운로드.

---

## 4. 개발 참조

### 4-1. 라우트 함수 목록

파일: `app/admin/routes/plans.py`

| HTTP 메서드·경로 | 함수 | 권한 | 파일:라인 |
|----------------|------|------|---------|
| `GET /plans` | `plans_list` | `require_any` | `plans.py:187` |
| `GET /plans/export.xlsx` | `plans_export` | `require_any` | `plans.py:166` |
| `GET /plans/new` | `plans_new` | `require_manager` | `plans.py:226` |
| `POST /plans` | `plans_create` | `require_manager` | `plans.py:237` |
| `GET /services/{service_id}/plans/new` | `service_plan_new` | `require_any` + `_can_manage` | `plans.py:260` |
| `POST /services/{service_id}/plans` | `service_plan_create` | `require_any` + `_can_manage` | `plans.py:280` |
| `GET /plans/{plan_id}/edit` | `plans_edit` | `require_any` | `plans.py:308` |
| `POST /plans/{plan_id}` | `plans_update` | `require_any` | `plans.py:322` |
| `POST /plans/{plan_id}/archive` | `plans_archive` | `require_any` | `plans.py:359` |
| `POST /plans/{plan_id}/activate` | `plans_activate` | `require_any` | `plans.py:374` |
| `POST /plans/{plan_id}/delete` | `plans_delete` | `require_any` | `plans.py:389` |

### 4-2. 권한 헬퍼

- `require_manager` — `require_role(UserRole.SERVICE_MANAGER)`: SERVICE_MANAGER 전용 엔드포인트에 사용 (`plans.py:30`).
- `require_any` — `require_role(SYSTEM_ADMIN, SERVICE_MANAGER)`: 두 역할 모두 허용 (`deps.py:102`).
- `_can_manage(ctx, service_id)` (`plans.py:37-39`): `ctx.service_ids is None`(SYSTEM_ADMIN)이거나 service_id가 담당 목록에 있으면 True. 서비스 상세에서 요금제 생성·수정·삭제 시 추가 검사.
- `_authorize_plan(db, ctx, plan_id)` (`plans.py:50-59`): 단일 요금제 조회 + 스코프 검사. 스코프 외이거나 없으면 `NotFoundError`(404).

### 4-3. 템플릿 파일

| 파일 | 역할 |
|------|------|
| `app/admin/templates/plans/list.html` | 전역 목록 전체 페이지 골격 |
| `app/admin/templates/plans/_table.html` | 전역 목록 테이블 partial (htmx partial 대상 `id="list-plans"`) |
| `app/admin/templates/plans/form.html` | 생성·수정 공용 폼 |
| `app/admin/templates/services/_plans_table.html` | 서비스 상세 내 요금제 탭 partial (`id="list-svc-plans"`) |
| `app/admin/templates/services/detail.html:127` | `{% include "services/_plans_table.html" %}` 삽입 위치 |

### 4-4. htmx 동작

**전역 목록 (`plans/_table.html`)**:
- 검색·필터·정렬 링크는 `hx-get` + `hx-target="#list-plans"`로 테이블 부분만 교체합니다(`_list.html` 매크로).
- 상태변경·삭제 폼은 htmx를 사용하지 않고 일반 `method="post"` 폼으로 처리합니다(303 리다이렉트 → 목록 전체 새로고침).

**서비스 상세 탭 (`services/_plans_table.html`)**:
- 비활성화·활성화·삭제 폼에 `hx-post` + `hx-target="#list-svc-plans"` + `hx-swap="outerHTML"` 설정(`_plans_table.html:41-54`). 성공 시 이 테이블 영역만 부분 갱신.
- 303 리다이렉트를 htmx가 XHR로 따라가며 HX-Trigger 헤더를 유지하므로 완료 모달도 정상 표시됩니다.

### 4-5. 폼 파싱 헬퍼 함수

**`_form_plan_fields(form)`** (`plans.py:86-129`):
폼 데이터를 서비스 레이어 인자 dict로 변환합니다. 주요 변환:
- `auto_renew_disabled` 체크박스 "on"/"true"/"1" → `auto_renew=False`, 미체크 → `auto_renew=True`
- `recurring_discount_type == "NONE"` → `recurring_discount_value=None` 강제(값 필드가 숨겨져도 DB 오염 방지)
- `trial_enabled` 미체크 → `trial_days=None` 강제
- `extra_key`/`extra_value` 병렬 목록 → `_collect_extra_info` 호출

> 반드시 `try … except DomainError` 블록 **안**에서 호출해야 합니다. `_collect_extra_info`가 `InputValidationError`(DomainError 하위)를 던질 수 있기 때문입니다(`plans.py:244-255` 참고).

**`_collect_extra_info(form)`** (`plans.py:62-83`):
`extra_key[]`/`extra_value[]` 병렬 목록을 행 단위로 zip해 dict로 수집합니다.
- 키·값 모두 빈 행 → 무시
- 값만 있고 키가 비면 → `InputValidationError("추가정보 키를 입력하세요(값: ...)")`
- 키 중복 → 마지막 값이 우선

### 4-6. 금액 계산

서버 함수(`app/services/billing_math.py`):
- `plan_first_amount(plan)` — 첫 결제 금액(정가 기준, 첫구독 할인만 적용)
- `plan_recurring_amount(plan)` — 정기 결제 금액(정가 기준, 상시 할인만 적용)
- `first_amount_breakdown(plan)` — 첫 결제 금액 계산 내역 문자열(테이블 툴팁용)
- `recurring_amount_breakdown(plan)` — 정기 결제 금액 계산 내역 문자열(테이블 툴팁용)

목록 라우트에서 각 plan 인스턴스에 동적으로 주입(`plans.py:208-212`):
```python
plan.recurring_amount = plan_recurring_amount(plan)
plan.first_amount = plan_first_amount(plan)
plan.first_tooltip = first_amount_breakdown(plan)
plan.recurring_tooltip = recurring_amount_breakdown(plan)
```

JS 미리보기는 `form.html:138-182`에 인라인 스크립트로 구현되어 있으며 `billing_math.py`의 `compute_recurring_amount` / `compute_first_amount` 로직을 JS로 미러합니다. **`billing_math.py`를 수정할 경우 이 JS도 함께 수정해야 합니다.**

### 4-7. 결제 주기는 수정 불가(요청)

결제 주기(`billing_cycle`)와 주기일수(`cycle_days`)는 **생성 시에만 정하며, 수정할 수 없습니다.**
- 수정 폼(`form.html`)에서는 결제 주기를 **비활성(읽기 전용)으로만 표시**하고 폼에 전송하지 않습니다("결제 주기는 수정할 수 없습니다" 안내).
- 수정 라우트(`plans_update`)는 `billing_cycle`/`cycle_days`를 읽지 않으며, 서비스 `update_plan()`도 해당 인자를 **받지 않습니다**(전달 시 `TypeError`). 따라서 기존 주기가 항상 그대로 유지됩니다.
- 주기를 바꾸려면 **새 요금제를 생성**해야 합니다(기존 구독은 자신의 주기를 그대로 유지).

### 4-8. open redirect 방어

`_safe_next(value, fallback)` (`plans.py:42-47`): `next` URL이 `/admin/`로 시작하지 않으면 fallback(`/admin/plans`)으로 대체합니다. 외부 URL로의 리다이렉트를 방지합니다.

### 4-9. 쿼리 구조

`_build_plans_query(pp, ctx)` (`plans.py:132-163`):
- `Plan` INNER JOIN `Service` (모든 요금제는 반드시 서비스에 속함)
- `ctx.service_ids is None`(SYSTEM_ADMIN) → 전체; 목록 있으면(SERVICE_MANAGER) `WHERE plan.service_id IN (...)` 추가
- 필터: `q`(이름 ILIKE), `status`, `billing_cycle`, `plan_name`(정확 일치), `service_id`(UUID 파싱 실패 시 무시)

목록과 엑셀 라우트 모두 이 함수를 공유합니다.

### 4-10. CSRF

모든 POST 핸들러 첫 줄에 `await validate_csrf(request, ctx)` 호출 (`deps.py:105-110`). 폼 hidden `csrf_token` 또는 `X-CSRF-Token` 헤더 값이 Redis 세션 토큰과 일치하지 않으면 `PermissionDeniedError`(403).

---

## 5. 주의사항 / 자주 하는 실수

### 금액 계산 — 첫 결제와 상시 할인은 독립

첫 결제에는 상시 할인이 **적용되지 않습니다**. 정가를 기준으로 첫구독 할인만 계산합니다.

```
예) 정가 10,000원, 첫구독 할인 30%, 상시 할인 20%
  첫 결제:  10,000 − (10,000 × 30%) = 7,000원  ← 상시 할인 무관
  정기 결제: 10,000 − (10,000 × 20%) = 8,000원  ← 첫구독 할인 무관
```

폼 하단 안내 문구(`form.html:72`): "첫 결제는 정가 기준으로 첫구독 할인만 적용됩니다(상시 할인 미적용)."

자세한 계산 규칙(열거값·클램프·퍼센트 계산 방식)은 [../08-plans.md §7](../08-plans.md) 참고.

---

### 구독이 있는 요금제는 삭제 불가

구독 레코드가 1건이라도 있으면 서비스 레이어가 `ConflictError("구독이 있는 요금제는 삭제할 수 없습니다. 보관(아카이브)을 사용하세요.")`를 던지고, 라우트가 `?error=메시지`로 리다이렉트합니다(`plans.py:404-412`).

**올바른 처리 순서**: [비활성화] → 기존 구독이 모두 만료 대기 → 나중에 [삭제].

---

### 자동결제 안함(`auto_renew=False`) 체크박스 이름

폼 필드명은 `auto_renew_disabled`이며, 체크 시 `auto_renew=False`(자동갱신 없음), **미체크 시 `auto_renew=True`(자동갱신 있음)**으로 변환됩니다(`plans.py:114`). 필드명과 의미가 반전되어 있으니 코드 수정 시 혼동하지 않도록 주의하세요.

`auto_renew=False`인 요금제로 생성된 구독은 `next_billing_at=None`이고 기간 종료 시 자동으로 EXPIRED 처리됩니다. **이미 생성된 구독에는 소급 적용되지 않습니다.**

---

### 체험(trial)과 자동결제 안함은 공존 가능

`trial_enabled=True` + `auto_renew=False` 동시 설정이 허용됩니다. 동작:
> 체험 기간 종료 → 첫 결제 발생 → 첫 주기 종료 시 자동갱신 없이 만료.

---

### 추가정보 — 값만 있고 키가 없으면 서버 오류

`extra_value`에 값을 입력하고 `extra_key`를 비워두면 서버가 `InputValidationError`를 반환해 폼 오류 메시지가 표시됩니다. 빈 행(키·값 모두 빈)은 무시되므로 [+ 항목 추가] 후 저장하지 않아도 됩니다.

---

### 결제 주기는 수정 불가

결제 주기는 요금제 생성 시에만 정하며 **수정할 수 없습니다**(위 4-7 참조). 다른 주기가 필요하면 새 요금제를 만들어야 합니다.

---

### SYSTEM_ADMIN은 전역 목록에서 동작 버튼이 없음

`_table.html:48` 조건 `{% if ctx.user.role == 'SERVICE_MANAGER' %}`에 의해 SYSTEM_ADMIN은 전역 요금제 목록에서 수정·비활성화·삭제 버튼을 볼 수 없습니다. SYSTEM_ADMIN이 요금제를 조작하려면 서비스 상세 화면(`/admin/services/{service_id}`)의 요금제 탭을 이용하거나 직접 URL로 접근하세요.

---

### billing_math.py 변경 시 JS 미러도 함께 수정

`form.html:138-182`의 인라인 JS는 `billing_math.py`의 계산 로직을 JS로 복제한 것입니다. 서버 로직(`compute_first_amount`, `compute_recurring_amount`)을 변경하면 폼 미리보기 JS의 `applyDiscount` 함수도 함께 수정해야 합니다.
