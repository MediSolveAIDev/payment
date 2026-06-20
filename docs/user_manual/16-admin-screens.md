# 16. 어드민 화면별 설명

이 문서는 htmx 어드민(`/admin/*`)을 **화면별로** 정리한 개발자용 지도입니다. 각 화면의 경로(GET/POST)·라우트 함수 위치(`file:line`)·템플릿·하는 일·필요 권한을 표로 정리하므로, 화면을 고치거나 디버깅할 때 어디를 봐야 하는지 바로 찾을 수 있습니다.

> 참고: 라우터 등록 순서·렌더 헬퍼는 `app/admin/__init__.py`에 있습니다. 권한·CSRF·세션은 `app/admin/deps.py`, 목록 공통(페이지네이션·정렬·검색)은 `app/admin/pagination.py`에 있습니다.

> 함께 보기: [관리자 콘솔(사용자용)](01-admin-console.md)

---

## 16.1 어드민 공통 구조

화면별 설명에 들어가기 전, 모든 화면이 공유하는 골격을 먼저 정리합니다.

### 인증·권한 (deps.py)

인증은 `require_user`(`app/admin/deps.py:60`)가 세션 쿠키(`admin_session`) → Redis 세션 → DB 사용자 확인 → `AdminContext` 주입의 순서로 처리합니다. `AdminContext`(`app/admin/deps.py:42`)는 `user`, `session_id`, `csrf_token`, `service_ids`를 담습니다. `service_ids`는 SYSTEM_ADMIN이면 `None`(전체 접근), SERVICE_MANAGER이면 담당 서비스 UUID 목록입니다.

권한 Depends는 `require_role` 팩토리(`app/admin/deps.py:88`)로 만든 두 축약을 씁니다.

| Depends | 정의 위치 | 허용 역할 | 쓰는 화면 |
|---------|-----------|-----------|-----------|
| `require_admin` | `app/admin/deps.py:102` | SYSTEM_ADMIN 전용 | 서비스·계정·감사·전체설정·카드 |
| `require_any` | `app/admin/deps.py:104` | SYSTEM_ADMIN + SERVICE_MANAGER | 대시보드·구독·결제·정산·요금제 |

> 주의: `require_any` 화면이라도 데이터 범위는 `service_scope(ctx)`(`app/admin/deps.py:115`, = `ctx.service_ids`)로 갈라집니다. SERVICE_MANAGER는 담당 서비스만 보이며, 비담당 리소스 직접 접근은 **403이 아니라 404**로 응답합니다(존재 여부 미노출). `require_user`는 또한 `GlobalSettings.admin_allowed_ips`가 비어 있지 않으면 접속 IP를 검사합니다(루프백 IP는 항상 허용).

미인증 시 `AdminAuthRequired`(`app/admin/deps.py:34`)가 발생하고, `register_admin_exception_handlers`(`app/admin/deps.py:120`)가 일반 요청은 303 리다이렉트, htmx 요청은 `HX-Redirect` 헤더(204)로 `/admin/login`에 보냅니다.

### CSRF

모든 admin POST는 `validate_csrf(request, ctx)`(`app/admin/deps.py:107`)를 첫 줄에서 호출해야 합니다. 폼 hidden 필드 `csrf_token` 또는 헤더 `X-CSRF-Token`을 세션 토큰과 상수시간 비교합니다. 불일치 시 `PermissionDeniedError`(403). 토큰 값은 템플릿에서 `{{ ctx.csrf_token }}`로 폼에 주입합니다.

### htmx 부분 갱신 패턴 (__init__.py)

목록 화면은 `render_list`(`app/admin/__init__.py:77`)를 사용합니다. `HX-Request` 헤더가 있으면 리스트 **partial**(`_table.html`)만, 없으면 전체 페이지(`list.html`)를 렌더합니다.

```python
def render_list(request, full_name, partial_name, ctx=None, **extra):
    name = partial_name if request.headers.get("HX-Request") else full_name
    return render(request, name, ctx=ctx, **extra)
```

서비스 상세처럼 한 화면에 탭이 여러 개인 경우는 `HX-Target` 헤더 값으로 partial을 갈라 렌더합니다(`app/admin/routes/services.py:322`).

`render`(`app/admin/__init__.py:61`)는 공통으로 `?flash`/`?saved` 쿼리를 컨텍스트에 넣고, `saved`가 있으면 `HX-Trigger: showSaved` 헤더를 붙여 admin.js가 완료 모달(✓)을 띄우게 합니다. DB 쓰기 성공 후에는 `saved_redirect`(`app/admin/__init__.py:49`)로 대상 URL에 `?saved=` 메시지를 덧붙여 리다이렉트합니다.

### 페이지네이션·정렬·검색 (pagination.py)

목록 라우트는 `PageParams.from_request`(`app/admin/pagination.py:31`)로 쿼리스트링을 파싱합니다.

| 항목 | 쿼리 파라미터 | 비고 |
|------|---------------|------|
| 페이지 | `page`(기본) | `page_param`으로 변경 가능(한 화면 다중 페이저 분리) |
| 검색어 | `q` | `pp.q` |
| 정렬 | `sort`, `dir` | `sortable` 화이트리스트 밖이면 default로 보정 |
| 필터 | `filter_keys`로 지정 | `pp.filters` dict |

`paginate`(`app/admin/pagination.py:115`)가 count 쿼리를 내부 생성(`count_of`)해 실행하며, `flatten=True`면 단일 엔티티 Row를 엔티티로 평탄화합니다. 날짜 범위 필터는 `date_range`(`app/admin/pagination.py:150`)가 `from`/`to`(YYYY-MM-DD)를 UTC 반개구간으로 변환합니다. 정렬 가능 컬럼 맵(`_*_SORT`)과 공유 쿼리 빌더 일부는 `app/admin/filters.py`에 있습니다.

### 라우터 등록·내비게이션

라우터 등록은 `app/admin/__init__.py:101`부터입니다. `services_export`/`services_managers`를 `services`보다 **먼저** 등록해 `/services/export.xlsx`가 `/services/{service_id}`(UUID 경로)에 잡히지 않게 합니다. 대시보드는 `prefix="/admin"`에서 `GET /admin`(트레일링 슬래시 없음)이 해석되도록 `add_api_route("")`로 직접 등록합니다(`app/admin/__init__.py:119`). 좌측 메뉴(LNB)는 `app/admin/templates/base.html:37`에 있으며, `관리` 카테고리(서비스·계정·전체 설정·감사 로그)는 `ctx.user.role == 'SYSTEM_ADMIN'`일 때만 렌더됩니다.

---

## 16.2 로그인·접근

> 라우트 파일: `app/admin/routes/auth.py` · 템플릿: `login.html`, `setup_password.html`

| 경로 | 라우트 함수 | 템플릿 | 권한 |
|------|-------------|--------|------|
| `GET /admin/login` | `login_page` `app/admin/routes/auth.py:45` | `login.html` | 없음 |
| `POST /admin/login` | `login_submit` `app/admin/routes/auth.py:69` | `login.html`(실패 시) | 없음 |
| `POST /admin/logout` | `logout` `app/admin/routes/auth.py:107` | — | `require_any` + CSRF |
| `GET /admin/setup-password` | `setup_password_page` `app/admin/routes/auth.py:124` | `setup_password.html` | 토큰 |
| `POST /admin/setup-password` | `setup_password_submit` `app/admin/routes/auth.py:134` | `setup_password.html`(실패 시) | 토큰 |
| `GET /admin/intro` | `intro_page` `app/admin/routes/auth.py:60` | (정적 HTML) | 없음 |

하는 일: 로그인은 IP당 분당 시도 제한(`_login_rate_limited`, `app/admin/routes/auth.py:31`)을 먼저 검사한 뒤 `auth_service.login`으로 인증하고, 성공 시 `admin_session` 쿠키(HttpOnly, SameSite=Lax, prod에서만 secure)를 발급하고 `/admin`으로 보냅니다. 개발 환경(`environment != "prod"`)에서는 폼에 `dev_login_email`/`dev_login_password`를 미리 채웁니다. 비밀번호 설정/재설정은 메일로 받은 `token`을 hidden으로 전달받아 처리하며, 확인 불일치는 폼 단에서 즉시 오류로 막습니다.

---

## 16.3 대시보드

> 라우트 파일: `app/admin/routes/dashboard.py` · 템플릿: `dashboard.html` (+ `_charts.html` 인클루드)

| 경로 | 라우트 함수 | 템플릿 | 권한 |
|------|-------------|--------|------|
| `GET /admin` | `dashboard` `app/admin/routes/dashboard.py:24` | `dashboard.html` | `require_any` |

하는 일: `build_dashboard(db, ctx.service_ids)`(`app/services/dashboard.py`)로 집계 데이터를 조회해 렌더합니다. `is_admin = ctx.user.role == SYSTEM_ADMIN` 플래그로 전체 통계·관리자 전용 섹션을 조건부 노출합니다. SERVICE_MANAGER는 `service_ids` 스코프 내 데이터만 집계됩니다. 등록은 서브라우터가 아니라 `__init__.py:119`의 `add_api_route("")`로 직접 합니다.

---

## 16.4 서비스 (+등록 카드/키)

> 라우트 파일: `app/admin/routes/services.py`, `services_managers.py`, `services_export.py`
> 템플릿: `services/` 디렉터리(`list.html`, `_table.html`, `new.html`, `detail.html`, `keys.html`, `_keys_modal.html`, `_plans_table.html`, `_subs_table.html`, `_cards_table.html`, `_oneoff_table.html`, `_events_table.html`)

모든 서비스 엔드포인트는 **SYSTEM_ADMIN 전용**(`require_admin`)입니다.

### 목록·등록·키

| 경로 | 라우트 함수 | 템플릿 |
|------|-------------|--------|
| `GET /admin/services` | `services_list` `app/admin/routes/services.py:59` | `services/list.html` / `_table.html`(htmx) |
| `GET /admin/services/new` | `services_new` `app/admin/routes/services.py:87` | `services/new.html` |
| `POST /admin/services` | `services_create` `app/admin/routes/services.py:95` | `services/keys.html`(성공) |
| `GET /admin/services/export.xlsx` | `services_export` `app/admin/routes/services_export.py:30` | (xlsx) |
| `GET /admin/services/{id}/keys-modal` | `services_keys_modal` `app/admin/routes/services.py:138` | `services/_keys_modal.html` |
| `POST /admin/services/{id}/rotate-keys` | `services_rotate` `app/admin/routes/services.py:347` | `services/keys.html` |

하는 일: 등록 성공 시 평문 API 키·HMAC 시크릿을 **일회성**으로 `keys.html`에 표시합니다(키는 암호화 저장되므로 평문을 볼 수 있는 유일한 기회). 키 복사 모달과 재발급은 감사 로그를 남기고 `Cache-Control: no-store`로 캐시를 막습니다.

### 상세 (탭)

| 경로 | 라우트 함수 | 템플릿 |
|------|-------------|--------|
| `GET /admin/services/{id}` | `services_detail` `app/admin/routes/services.py:284` | `services/detail.html` / 탭 partial |

상세는 `HX-Target` 헤더로 탭 partial을 갈라 렌더합니다(`app/admin/routes/services.py:325`).

| `HX-Target` | partial | 탭 데이터 빌더 |
|-------------|---------|----------------|
| `list-svc-plans` | `services/_plans_table.html` | `_plans_tab` `app/admin/routes/services.py:163` |
| `list-svc-subs` | `services/_subs_table.html` | `_subs_tab` `app/admin/routes/services.py:175` |
| `list-svc-cards` | `services/_cards_table.html` | `_cards_tab` `app/admin/routes/services.py:211` |
| `list-svc-oneoff` | `services/_oneoff_table.html` | `_oneoff_tab` `app/admin/routes/services.py:187` |
| `list-svc-events` | `services/_events_table.html` | `_events_tab` `app/admin/routes/services.py:238` |

> 참고: 등록 카드 탭은 `cards` 테이블을 `(service_id, external_user_id)`당 1건으로 페이징합니다(`kpage`, 10건). 단건결제 탭은 `kind == ONE_OFF` 고정(`opage`), 이벤트 탭은 이 서비스 관련 감사 로그(서비스·요금제·담당자 할당·카드)를 모읍니다(`epage`).

### 설정 변경 (POST)

| 경로 | 라우트 함수 | 하는 일 |
|------|-------------|---------|
| `POST /admin/services/{id}/ips` | `services_update_ips` `app/admin/routes/services.py:367` | 허용 IP 목록 갱신(줄바꿈/콤마 파싱) |
| `POST /admin/services/{id}/cancel-policy` | `services_cancel_policy` `app/admin/routes/services.py:385` | 단건결제 취소 허용·수수료율 |
| `POST /admin/services/{id}/notification-url` | `services_notification_url` `app/admin/routes/services.py:416` | 아웃고잉 웹훅 URL 저장(빈값=NULL) |
| `POST /admin/services/{id}/notification-test` | `services_notification_test` `app/admin/routes/services.py:445` | 테스트 알림 동기 전송 |
| `POST /admin/services/{id}/status` | `services_set_status` `app/admin/routes/services.py:468` | 서비스 상태(ACTIVE/INACTIVE) |
| `POST /admin/services/{id}/delete` | `services_delete` `app/admin/routes/services.py:481` | 삭제(구독 있으면 DomainError 거부) |

### 담당자 관리 (services_managers.py)

| 경로 | 라우트 함수 |
|------|-------------|
| `POST /admin/services/{id}/assign-manager` | `services_assign_manager` `app/admin/routes/services_managers.py:56` |
| `POST /admin/services/{id}/primary-manager` | `services_set_primary_manager` `app/admin/routes/services_managers.py:75` |
| `POST /admin/services/{id}/managers/{user_id}/remove` | `services_remove_manager` `app/admin/routes/services_managers.py:94` |

> 참고: 담당자 목록 헬퍼 `service_managers`(`app/admin/routes/services_managers.py:30`)는 서비스 상세 화면도 사용합니다. 대표 담당자는 해제할 수 없으며 이 규칙은 도메인(`accounts.unassign_service`)이 `ConflictError`로 강제합니다.

### 서비스 상세 탭 엑셀 (services_export.py)

`GET /admin/services/{id}/subs.xlsx`(`:44`), `/oneoff.xlsx`(`:66`), `/plans.xlsx`(`:90`) — 각 탭의 현재 검색/필터를 그대로 적용해 다운로드합니다(공유 쿼리 빌더 사용, 행 상한 `EXPORT_MAX_ROWS`).

---

## 16.5 요금제

> 라우트 파일: `app/admin/routes/plans.py` · 템플릿: `plans/list.html`, `plans/_table.html`, `plans/form.html`

| 경로 | 라우트 함수 | 템플릿 | 권한 |
|------|-------------|--------|------|
| `GET /admin/plans` | `plans_list` `app/admin/routes/plans.py:187` | `plans/list.html` / `_table.html`(htmx) | `require_any` |
| `GET /admin/plans/export.xlsx` | `plans_export` `app/admin/routes/plans.py:166` | (xlsx) | `require_any` |
| `GET /admin/plans/new` | `plans_new` `app/admin/routes/plans.py:225` | `plans/form.html` | `require_manager` |
| `POST /admin/plans` | `plans_create` `app/admin/routes/plans.py:236` | `plans/form.html`(실패 시) | `require_manager` |
| `GET /admin/services/{id}/plans/new` | `service_plan_new` `app/admin/routes/plans.py:259` | `plans/form.html` | `require_any` + `_can_manage` |
| `POST /admin/services/{id}/plans` | `service_plan_create` `app/admin/routes/plans.py:279` | `plans/form.html`(실패 시) | `require_any` + `_can_manage` |
| `GET /admin/plans/{id}/edit` | `plans_edit` `app/admin/routes/plans.py:307` | `plans/form.html` | `require_any` |
| `POST /admin/plans/{id}` | `plans_update` `app/admin/routes/plans.py:321` | `plans/form.html`(실패 시) | `require_any` |
| `POST /admin/plans/{id}/archive` | `plans_archive` `app/admin/routes/plans.py:352` | — | `require_any` |
| `POST /admin/plans/{id}/activate` | `plans_activate` `app/admin/routes/plans.py:368` | — | `require_any` |
| `POST /admin/plans/{id}/delete` | `plans_delete` `app/admin/routes/plans.py:384` | — | `require_any` |
| `POST /admin/plans/{id}/bonus-days` | `plans_bonus_days` `app/admin/routes/plans.py:413` | — | `require_any` |

하는 일: 목록은 각 Plan에 표시용 금액·툴팁(`plan_first_amount`/`plan_recurring_amount`/`*_breakdown`)을 동적으로 주입합니다(`:207`). 폼 파싱은 `_form_plan_fields`(`:86`)와 추가정보 수집 `_collect_extra_info`(`:62`)가 담당합니다. 권한 분기: `require_manager`(SERVICE_MANAGER) 진입점은 본인 주 서비스에 추가하는 기존 플로우, 서비스 상세 경유는 `require_any` + `_can_manage`(`:37`)로 담당 여부를 추가 검사합니다. `_authorize_plan`(`:50`)은 비담당 요금제를 **404**로 처리합니다.

> 주의: 결제 주기(`billing_cycle`/`cycle_days`)는 수정 불가입니다 — 폼이 보내지 않고 `update_plan`도 인자를 받지 않아 기존 주기가 유지됩니다. 삭제/보너스일은 next URL을 `_safe_next`(`:42`)로 open redirect 방어합니다(반드시 `/admin/`로 시작).

---

## 16.6 구독

> 라우트 파일: `app/admin/routes/subscriptions.py` · 템플릿: `subscriptions/list.html`, `subscriptions/_table.html`, `subscriptions/detail.html`

| 경로 | 라우트 함수 | 템플릿 | 권한 |
|------|-------------|--------|------|
| `GET /admin/subscriptions` | `subscriptions_list` `app/admin/routes/subscriptions.py:71` | `subscriptions/list.html` / `_table.html`(htmx) | `require_any` |
| `GET /admin/subscriptions/export.xlsx` | `subscriptions_export` `app/admin/routes/subscriptions.py:47` | (xlsx) | `require_any` |
| `GET /admin/subscriptions/{id}` | `subscription_detail` `app/admin/routes/subscriptions.py:105` | `subscriptions/detail.html` | `require_any`(스코프) |
| `POST /admin/subscriptions/{id}/force-cancel` | `subscription_force_cancel` `app/admin/routes/subscriptions.py:166` | — | `require_any` + CSRF |
| `POST /admin/subscriptions/{id}/extend` | `subscription_extend` `app/admin/routes/subscriptions.py:186` | — | `require_any` + CSRF |
| `POST /admin/subscriptions/{id}/retry-payment` | `subscription_retry_payment` `app/admin/routes/subscriptions.py:221` | — | `require_any` + CSRF |

하는 일: 목록·엑셀·서비스 상세 탭은 공유 빌더 `subscription_query`(`app/admin/filters.py`)로 동일 필터를 보장합니다(스코프는 `service_scope(ctx)`). 상세는 최근 결제 200건 + DONE 건수, 연장 이력(`subscription.extended` 감사로그), 체험 사용 여부(`subscription.create` 감사 detail), 등록 카드(`card_service.get_card`로 `cards` 테이블 조회)를 함께 렌더합니다. 강제 해지/연장/재결제는 스코프·감사 기록을 서비스 레이어에 위임하고, 도메인 오류는 `?error=`로 상세 페이지에 표시합니다(스코프 밖은 404 전파).

---

## 16.7 결제 (+취소)

> 라우트 파일: `app/admin/routes/payments.py` · 템플릿: `payments/list.html`, `payments/detail.html`

| 경로 | 라우트 함수 | 템플릿 | 권한 |
|------|-------------|--------|------|
| `GET /admin/payments` | `payments_list` `app/admin/routes/payments.py:186` | `payments/list.html` | `require_any` |
| `GET /admin/payments/export.xlsx` | `payments_export` `app/admin/routes/payments.py:90` | (xlsx) | `require_any` |
| `GET /admin/payments/{id}` | `payment_detail` `app/admin/routes/payments.py:151` | `payments/detail.html` | `require_any`(스코프) |
| `POST /admin/payments/{id}/cancel` | `payment_cancel` `app/admin/routes/payments.py:115` | — | `require_any` + CSRF |

하는 일: 목록은 partial이 없어 `render`로 전체 페이지만 렌더합니다(htmx 부분 갱신 대상 아님). 공유 쿼리 `_build_payments_query`(`:36`)는 단건(ONE_OFF) 결제를 포함하려고 Subscription/Plan을 **OUTER JOIN**, Service는 INNER JOIN합니다. 상세는 구독 결제면 Subscription을 추가 조회하고, 결제 카드(`card_service.get_card`)·누적 환불액·잔여 환불가능액을 계산해 전달합니다(`:178`). 취소는 단건(ONE_OFF) 결제 대상이며 폼 `cancel_amount`가 비면 전액, 숫자면 부분(누적) 취소입니다. 어드민 취소는 수수료 없이 항상 허용되며 상태(DONE)·잔여 한도 검증은 도메인이 합니다.

---

## 16.8 카드 상세/토글

> 라우트 파일: `app/admin/routes/cards.py` · 템플릿: `cards/detail.html`

| 경로 | 라우트 함수 | 템플릿 | 권한 |
|------|-------------|--------|------|
| `GET /admin/cards/{id}` | `cards_detail` `app/admin/routes/cards.py:66` | `cards/detail.html` | `require_admin` |
| `POST /admin/cards/{id}/toggle` | `cards_toggle` `app/admin/routes/cards.py:31` | `services/_cards_table.html`(htmx) | `require_admin` + CSRF |

하는 일: 카드 상세는 등록 카드 정보 + 이 카드로 결제한 내역을 보여줍니다. Payment에는 `card_id`가 없으므로 `(service_id, external_user_id)`가 일치하는 결제를 페이징합니다(구독·일반 모두 포함). 토글은 `set_card_active`로 활성↔비활성을 반전합니다(비활성화 시 해당 카드 결제 차단).

> 참고: 토글 응답은 호출 위치에 따라 다릅니다. 서비스 상세 '등록 카드' 리스트에서 호출(htmx)이면 갱신된 `services/_cards_table.html` partial을, 카드 상세에서 호출(일반 요청)이면 카드 상세로 리다이렉트합니다. partial 재렌더에는 `services` 라우트의 `_cards_tab`을 함수 내부에서 import해 씁니다(순환 import 방지).

---

## 16.9 정산

> 라우트 파일: `app/admin/routes/settlement.py` · 템플릿: `settlement/index.html`

| 경로 | 라우트 함수 | 템플릿 | 권한 |
|------|-------------|--------|------|
| `GET /admin/settlement` | `settlement_view` `app/admin/routes/settlement.py:99` | `settlement/index.html` | `require_any` |
| `GET /admin/settlement/export.xlsx` | `settlement_export` `app/admin/routes/settlement.py:164` | (xlsx) | `require_any` |

하는 일: 두 가지 모드가 있습니다. **전체 모드**(`service_id` 미지정)는 `settlement_summary`로 스코프 내 서비스별 요약 테이블을 만들고 구독/일반 매출·환불·순매출을 합산합니다. **서비스별 모드**(`service_id` 지정)는 `_settlement_payment_query`(`:35`)로 그 서비스의 결제 건별 페이지를 추가 조회합니다(상태 DONE+CANCELED 포함). 기간·스코프·선택 서비스 판정은 공통 헬퍼 `_settlement_context`(`:60`)가 처리하며 기본 기간은 당월 1일~오늘입니다. SERVICE_MANAGER가 담당하지 않는 서비스 ID 지정 시 404입니다.

---

## 16.10 계정

> 라우트 파일: `app/admin/routes/users.py` · 템플릿: `users/list.html`, `users/_table.html`, `users/new.html`, `users/detail.html`, `users/edit.html`

모든 계정 엔드포인트는 **SYSTEM_ADMIN 전용**(`require_admin`)입니다.

| 경로 | 라우트 함수 | 템플릿 |
|------|-------------|--------|
| `GET /admin/users` | `users_list` `app/admin/routes/users.py:79` | `users/list.html` / `_table.html`(htmx) |
| `GET /admin/users/export.xlsx` | `users_export` `app/admin/routes/users.py:99` | (xlsx) |
| `GET /admin/users/new` | `users_new` `app/admin/routes/users.py:115` | `users/new.html` |
| `POST /admin/users` | `users_create` `app/admin/routes/users.py:123` | `users/new.html`(실패 시) |
| `GET /admin/users/{id}` | `users_detail` `app/admin/routes/users.py:150` | `users/detail.html` |
| `POST /admin/users/{id}/services` | `users_assign_service` `app/admin/routes/users.py:171` | — |
| `POST /admin/users/{id}/services/{service_id}/remove` | `users_unassign_service` `app/admin/routes/users.py:189` | — |
| `GET /admin/users/{id}/edit` | `users_edit` `app/admin/routes/users.py:202` | `users/edit.html` |
| `POST /admin/users/{id}/edit` | `users_update` `app/admin/routes/users.py:213` | `users/edit.html`(실패 시) |
| `POST /admin/users/{id}/disable` | `users_disable` `app/admin/routes/users.py:232` | — |
| `POST /admin/users/{id}/delete` | `users_delete` `app/admin/routes/users.py:260` | — |
| `POST /admin/users/{id}/reset-password` | `users_reset_password` `app/admin/routes/users.py:277` | — |

하는 일: 목록·엑셀 공유 쿼리 `_build_users_query`(`:34`)는 DELETED 계정을 기본 제외하고 Service를 LEFT OUTER JOIN합니다(담당 서비스 없는 계정 누락 방지). 계정 생성/비밀번호 재설정은 메일 발송 결과를 `email_flash_qs`로 토스트에 표시합니다. 비활성화·삭제는 Redis로 기존 세션을 즉시 무효화합니다. 비활성화는 체크박스가 아니라 hidden `disabled`("true"/"false") 문자열로 의도를 명확히 전달합니다(`:249`).

> 참고: 상세의 `managed`(현재 담당 서비스)·`assignable`(미담당 서비스) 구분은 추가 할당 드롭다운에 쓰입니다.

---

## 16.11 감사로그

> 라우트 파일: `app/admin/routes/audit.py` · 템플릿: `audit/list.html`, `audit/_table.html`

모든 감사 엔드포인트는 **SYSTEM_ADMIN 전용**(`require_admin`)입니다.

| 경로 | 라우트 함수 | 템플릿 |
|------|-------------|--------|
| `GET /admin/audit` | `audit_list` `app/admin/routes/audit.py:136` | `audit/list.html` / `_table.html`(htmx) |
| `GET /admin/audit/export.xlsx` | `audit_export` `app/admin/routes/audit.py:154` | (xlsx) |
| `POST /admin/audit/purge` | `audit_purge` `app/admin/routes/audit.py:176` | — |

하는 일: 목록·엑셀 공유 쿼리 `_build_audit_query`(`:95`)는 키워드(`q`)를 행위자 이메일·서비스명·target_id·detail JSON에서 검색하고, 행위자 유형(`actor_type`)·활동(`action`)으로 필터합니다. `_resolve_names`(`:53`)가 actor/target UUID를 배치 조회해 사람이 읽는 이름으로 바꾸고, `_build_rows`(`:114`)가 화면/엑셀 공용 dict로 변환합니다(라벨은 `app/admin/audit_labels.py`). purge는 기준일(UTC 자정) 이전 로그를 일괄 삭제하고 삭제 행위 자체를 `audit.purge`로 감사 기록합니다.

> 주의: audit 화면에는 `?error=` 표시 블록이 없어 입력 오류는 `?flash=…&flash_type=error` 토스트로 통일합니다(`:188`).

---

## 16.12 전체 설정

> 라우트 파일: `app/admin/routes/settings.py` · 템플릿: `settings/index.html`

모든 전체설정 엔드포인트는 **SYSTEM_ADMIN 전용**(`require_admin`)입니다. 단일 화면(`settings/index.html`)에 여러 폼이 섹션으로 들어가고, 각 섹션이 별도 POST로 저장됩니다.

| 경로 | 라우트 함수 | 하는 일 |
|------|-------------|---------|
| `GET /admin/settings` | `settings_page` `app/admin/routes/settings.py:25` | 현재 `GlobalSettings` 렌더 |
| `POST /admin/settings/retry` | `settings_retry` `app/admin/routes/settings.py:47` | 자동결제 재시도(횟수·간격·유예일) |
| `POST /admin/settings/security-policy` | `settings_security_policy` `app/admin/routes/settings.py:76` | 로그인 잠금 임계치·잠금시간·단건결제 상한(런타임 즉시 적용) |
| `POST /admin/settings/admin-ips` | `settings_admin_ips` `app/admin/routes/settings.py:104` | 어드민 접속 허용 IP(줄바꿈 구분) |
| `POST /admin/settings/server-toggle` | `settings_server_toggle` `app/admin/routes/settings.py:142` | 결제서버 킬스위치(활성/비활성) |

하는 일: 저장값은 `app/services/app_settings.py`를 통해 `GlobalSettings`에 반영됩니다. 각 POST는 성공 시 `?saved=`(완료 모달), 실패 시 `?error=`로 같은 화면에 돌아옵니다.

> 주의: 어드민 IP 저장은 lockout 방지를 위해 현재 접속 IP가 목록에 없으면 `InputValidationError`로 거부합니다. 킬스위치 비활성화는 사유(`reason`)와 작업자 본인 비밀번호 재확인(`password`)이 필요하고, Redis로 킬스위치 캐시를 즉시 무효화해 전파 지연을 없앱니다.
