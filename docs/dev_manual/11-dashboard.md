# 11. 대시보드 — 어드민 메인 화면

> **상호참조**: 정산 → [10. 정산](10-settlement.md) |
> 테이블 구조 → [02. 데이터베이스](02-database.md) |
> 어드민 인증·역할 → [03. 인증과 보안 공통](03-auth-and-security.md)

---

## 1. 한 줄 요약

어드민 콘솔에 로그인하면 가장 먼저 보이는 **메인 화면**입니다.
이번 달 매출·구독 통계, 12개월/30일 차트, 우측 패널(최근 결제·미수 구독·만료 임박)을
**한 번에** 보여줍니다.
역할에 따라 **SYSTEM_ADMIN은 전체**, **SERVICE_MANAGER는 담당 서비스 범위**로만 데이터를 조회합니다.

---

## 2. 언제 실행되나

| 트리거 | 설명 |
|--------|------|
| **어드민 콘솔** `GET /admin` | 로그인 후 첫 화면 또는 상단 메뉴 클릭 시 |

외부 API 호출이나 스케줄러는 없습니다. 순수한 **읽기 전용 화면**입니다.

---

## 3. 요청 진입점

**`GET /admin`**

- 라우트 함수: `app/admin/routes/dashboard.py:25` — `dashboard()`
- 라우터 등록: `app/admin/__init__.py:99` — `router.add_api_route("", dashboard.dashboard, ...)`
  > 주의: 일반 `include_router`가 아닌 `add_api_route`로 직접 등록합니다.
  > 이유: `GET /admin`(끝 슬래시 없음)과 `GET /admin/`을 모두 처리하기 위한 설계입니다.
- 인증 의존성: `require_any` (`app/admin/deps.py:102`) — SYSTEM_ADMIN 또는 SERVICE_MANAGER 허용

---

## 4. 단계별 처리 흐름

```
브라우저
  └── GET /admin
        ↓
  [1] app/admin/deps.py:60  require_user()
        - 세션 쿠키(admin_session) → Redis 세션 조회
        - 사용자 상태 확인(ACTIVE만 허용)
        - 접속 IP 제한 확인(admin_allowed_ips)
        - effective_service_ids() 호출 → AdminContext.service_ids 설정
        ↓
  [2] app/admin/deps.py:102  require_any
        - role ∈ {SYSTEM_ADMIN, SERVICE_MANAGER} 확인
        - 아니면 PermissionDeniedError(403)
        ↓
  [3] app/admin/routes/dashboard.py:34  build_dashboard(db, ctx.service_ids)
        ↓
  [4] app/services/dashboard.py:374  build_dashboard()
        - 하위 헬퍼 함수들을 순서대로 호출해 DashboardData 조립
        ↓
  [5] app/admin/routes/dashboard.py:35  render(request, "dashboard.html", ...)
        - 템플릿: app/admin/templates/dashboard.html
        - 차트 매크로: app/admin/templates/_charts.html
        ↓
  HTML 응답 (전체 페이지)
```

### 4-1. service_ids 스코프 설정

`app/services/accounts.py:33` `effective_service_ids()`:

```
SYSTEM_ADMIN  → None          (전체 데이터 조회)
SERVICE_MANAGER → [uuid, ...]  (User.service_id + UserService 테이블의 추가 서비스 합집합)
```

이 값이 `AdminContext.service_ids`에 저장되어 `build_dashboard(db, ctx.service_ids)`로 전달됩니다.
`_scoped()` 헬퍼(`app/services/dashboard.py:76`)가 모든 쿼리에 `WHERE service_id IN (...)` 조건을 덧붙입니다.

### 4-2. build_dashboard 내부 흐름

`app/services/dashboard.py:374`:

```
build_dashboard(db, scope)
  │
  ├─ [A] 상태별 구독 수 집계 (GROUP BY status) — 도넛 차트용
  │
  ├─ [C] _revenue_cards()     — 매출 카드 4개 (총/구독/일반/환불)
  ├─ [D] _sub_flow()          — 도넛 옆 흐름 지표 4개 (신규/취소/만료/미결제)
  ├─ [E] _series_12m()        — 12개월 구독 수·일반매출 시리즈
  ├─ [F] _daily_trend()       — 30일 일별 추이 (전체/신규/취소/만료)
  ├─ [G] status_breakdown     — 도넛 데이터 조립 (상태별 색상·링크 포함)
  │
  ├─ [H] scope=None일 때만 (SYSTEM_ADMIN 전용)
  │       ├─ _service_revenue()  — 서비스별 이번달 매출 테이블
  │       └─ _service_subs()     — 서비스별 구독 테이블
  │
  └─ [I] _rails()             — 우측 패널 4개 (요청 015)
          ├─ 최근 구독 8건 (Subscription · created_at DESC)            ← 신규
          ├─ 최근 결제 8건 (실제 Payment + 트라이얼/0원 첫결제 구독을 0원으로 합쳐 시간순)
          ├─ 미수/정지 구독 5건 (PAST_DUE, SUSPENDED)
          └─ 만료 임박 5건 (7일 이내, current_period_end 기준)
```

> **최근 결제(요청 015 1.1.2)**: 트라이얼·0원 첫결제 구독은 Payment 레코드가 없으므로(`subscriptions.py` `if amount > 0`), `_rails()`가 "Payment 없는 구독"을 0원으로 합쳐 보여준다. 표현 통일을 위해 `recent`는 dict 목록(`{sub_id, external_user_id, amount, status, status_ko, when}`)이며, 체험은 status='TRIAL'(체험), 0원 첫결제는 'DONE'(완료)로 표시한다. 최근 구독 패널은 `recent_subs`(요청 015 1.1.1).

---

## 5. 어떤 지표를 보여주나

### 5-1. 매출 카드 (이번 달)

`app/services/dashboard.py:141` `_revenue_cards()`:

| 카드 이름 | 집계 기준 | 클릭 이동 |
|-----------|----------|----------|
| 총매출 | `Payment.status=DONE` · `approved_at` ∈ [월초, 현재] | `/admin/payments?status=DONE&...` |
| 구독매출 | 위 + `kind=SUBSCRIPTION` | `/admin/payments?status=DONE&kind=SUBSCRIPTION&...` |
| 일반매출 | 위 + `kind=ONE_OFF`, **순매출 = `amount − canceled_amount`**(부분취소 반영) | `/admin/payments?status=DONE&kind=ONE_OFF&...` |
| 환불금액 | `status ∈ {DONE, CANCELED}`의 환불액 합산 — DONE은 `coalesce(canceled_amount,0)`(어드민 부분취소), CANCELED는 `coalesce(canceled_amount, amount)` | `/admin/payments?status=CANCELED&...` |

> **환불 집계 포인트**: 어드민 부분취소는 `status=DONE`을 유지하므로, 매출식(`_revenue_expr`)·환불식(`_refund_between`) 모두 DONE에서도 `canceled_amount`를 차감/합산합니다(`app/services/dashboard.py`). 즉 일반매출은 환불액을 뺀 순매출, 환불금액은 전액·부분 환불 합계입니다.
> 환불금액이 0원이면 `up=True`(긍정 색상)로 표시합니다.

### 5-2. 구독 도넛 + 흐름 지표 (이번 달)

도넛(`status_breakdown`): 전체 상태별 구독 수 비율 — **EXPIRED(만료)도 포함**합니다(`app/services/dashboard.py:392-409`).

| 상태 | 한글 | 색상 CSS 변수 (요청 015로 교체) |
|------|------|-------------|
| TRIAL | 체험 | `--accent-purple` |
| ACTIVE | 활성 | `--accent-blue` |
| PAST_DUE | 미수 | `--accent-yellow` |
| SUSPENDED | 정지 | `--accent-orange` |
| CANCELED | 취소 | `--accent-cyan` |
| EXTENDED | 연장처리 | `--accent-mint` |
| EXPIRED | 만료 | `--accent-red` |

도넛 옆 흐름 지표(`sub_flow`): `app/services/dashboard.py:166` `_sub_flow()`:

| 지표 | 집계 기준 |
|------|----------|
| 신규 구독 | `Subscription.created_at` ∈ [월초, 현재] |
| 구독 취소 | `AuditLog.action` ∈ `{subscription.cancel, subscription.force_cancel, subscription.suspended}` |
| 구독 만료 | `AuditLog.action = subscription.expired` |
| 미결제 | `Payment.status=FAILED` · `requested_at` ∈ [월초, 현재] |

### 5-3. 12개월 차트

`app/services/dashboard.py:243` `_series_12m()`:

- **최근 12개월 구독** (막대 차트): `subs_months` — `{label, total(전체), new(신규)}`
  - 색상(요청 015): 전체구독=`--accent-blue`, 신규구독=`--accent-green` (`dashboard.html`)
  - `total`: 해당 월 말 시점 기준 열린 구독 수
  - `new`: 해당 월에 `created_at`이 속하는 구독 수
- **최근 12개월 일반매출** (영역 차트): `one_off_months` — `{label, value}`
  - `value`: 해당 월의 `ONE_OFF` DONE 결제 합산

> **DB 집계 전환(감사 Phase 3 — 성능 H3)**: 버킷별 `count(*)/sum() FILTER (WHERE ...)`
> 컬럼을 가진 단일 쿼리로 DB가 테이블을 1회만 스캔해 집계합니다(`_open_new_counts`,
> `_oneoff_sums`, `_audit_counts`). 과거에는 구독 전체를 메모리에 적재 후 Python에서
> 42×N(12개월+30일) 루프를 돌아, 구독 증가 시 이벤트 루프를 점유했습니다.

### 5-4. 최근 30일 추이 (멀티라인 차트)

`app/services/dashboard.py:262` `_daily_trend()`:
`daily_trend` — `{label(M/D), total, new, canceled, expired}` 30개
색상(요청 015): 전체=`--accent-blue`, 신규=`--accent-green`, 취소=`--accent-orange`, 만료=`--accent-red`.

- `total`: 해당 날 말 시점 열린 구독 수 스냅샷
- `canceled`: 그날 취소 감사 이벤트 건수
- `expired`: 그날 만료 감사 이벤트 건수

### 5-5. 서비스별 테이블 (SYSTEM_ADMIN 전용)

`app/services/dashboard.py:411` — `scope=None`일 때만 채워집니다:

- **서비스별 매출** (`service_revenue`): `{id, name, total, sub, one_off, refund}` — 이번달
- **서비스별 구독** (`service_subs`): `{id, name, open, new, canceled, expired, revenue}` — 이번달

SERVICE_MANAGER로 로그인하면 이 두 테이블은 화면에서 완전히 숨겨집니다(`dashboard.html:36,85` `{% if is_admin and ... %}`).

### 5-6. 우측 패널

`app/services/dashboard.py:347` `_rails()`:

| 패널 | 조회 조건 | 건수 |
|------|----------|------|
| 최근 결제 | `Payment` JOIN `Subscription` · `requested_at DESC` | 최대 8건 |
| 미수 구독 | `status IN (PAST_DUE, SUSPENDED)` · `next_billing_at ASC` | 최대 5건 |
| 만료 임박 | `current_period_end ∈ [now, now+7일)` · 열린 상태 | 최대 5건 |

---

## 6. 사용하는 DB 테이블·컬럼

**읽기만 합니다. 쓰기 없음.**

| 테이블 | 읽는 컬럼 | 용도 |
|--------|----------|------|
| `subscriptions` | `status`, `created_at`, `current_period_end`, `next_billing_at`, `service_id`, `external_user_id` | 구독 수·상태·추이 집계 |
| `payments` | `status`, `kind`, `amount`, `canceled_amount`, `approved_at`, `requested_at`, `service_id`, `subscription_id` | 매출·환불·최근 결제 조회 |
| `services` | `id`, `name` | 서비스별 집계 표(SYSTEM_ADMIN 전용) |
| `audit_logs` | `action`, `target_id`, `created_at` | 취소·만료 건수 집계 |

---

## 7. 예외·엣지 케이스

### 7-1. 데이터가 없을 때

- **구독 0건**: `status_breakdown`은 `[{"label": "데이터 없음", "value": 1, "color": "var(--black-10)", "href": None}]`으로 채워져 도넛이 빈 화면 대신 "데이터 없음"을 표시합니다 (`app/services/dashboard.py:408-409`).
- **최근 결제 0건**: 템플릿 `{% else %}` 블록이 "결제 내역이 없습니다"를 출력합니다 (`dashboard.html:126-128`).
- **미수/만료 임박 0건**: 동일하게 빈 메시지를 출력합니다 (`dashboard.html:140-142`, `152-153`).

### 7-2. SERVICE_MANAGER 스코프

담당 서비스가 한 개도 없는 계정 (`service_ids = []`):
- `_scoped(query, [], col)` → `WHERE service_id IN ()` 조건 → 전 쿼리 결과 0 반환
- 카드에 "0원", 차트에 빈 데이터가 표시됩니다 (오류는 발생하지 않음)

### 7-3. 기간 기본값

- 매출 카드·흐름 지표: `month_start = now.replace(day=1, ...)` — 이번 달 1일 00:00:00 UTC (`app/services/dashboard.py:390`)
- 12개월 시리즈: 현재 월 포함 과거 12개월
- 30일 추이: `today - 29일` ~ `today`

반개구간 `[start, end)` 형태를 사용합니다. `end = now + relativedelta(seconds=1)`로 현재 시각을 포함시킵니다 (`app/services/dashboard.py:148`).

### 7-4. 취소 집계의 서비스 스코프

감사 로그(`audit_logs`)에는 `service_id`가 없으므로, 스코프 필터는 `target_id IN (SELECT CAST(id AS VARCHAR) FROM subscriptions WHERE service_id IN (...))` 서브쿼리로 처리합니다 (`app/services/dashboard.py:101-104`).

### 7-5. 시계 표시

상단 우측에 현재 KST 시각을 표시합니다 (`dashboard.html:10-23`).
- 서버 초기 렌더: `{{ now|kst("%Y-%m-%d %H:%M:%S") }}`
- 이후 매초 `setInterval`로 브라우저 로컬 시각으로 갱신 (`Asia/Seoul` 타임존)

---

## 8. 관련 테스트

### 8-1. e2e 테스트 — 화면 확인

`tests/e2e/test_dashboard_page.py`:

| 테스트 함수 | 검증 내용 |
|------------|----------|
| `test_dashboard_revenue_section` (`:28`) | 매출 카드 4개·서비스별 매출 테이블(admin) 렌더 |
| `test_dashboard_subscription_section` (`:39`) | 도넛·흐름 지표·서비스별 구독 테이블 렌더 |
| `test_dashboard_twelve_month_charts` (`:58`) | 12개월 차트 2개 렌더 |
| `test_dashboard_manager_scope_no_service_tables` (`:67`) | SERVICE_MANAGER는 서비스별 테이블 미표시 |
| `test_dashboard_shows_live_clock` (`:76`) | 시계 엘리먼트(`dash-clock`)·KST 갱신 확인 |

### 8-2. 통합 테스트 — 집계 로직 검증

`tests/integration/test_dashboard.py`:

| 테스트 함수 | 검증 내용 |
|------------|----------|
| `test_revenue_cards_total_sub_oneoff_refund` (`:49`) | 총/구독/일반/환불 금액 정확성 |
| `test_sub_cards_counts_and_expired_from_audit` (`:63`) | 취소·만료 건수 감사 기반 집계 |
| `test_status_donut_includes_expired` (`:85`) | 도넛에 EXPIRED 포함 여부 |
| `test_twelve_month_series_subs_and_one_off` (`:97`) | 12개월 시리즈 12개 항목 |
| `test_daily_trend_30_days` (`:110`) | 30일 트렌드 30개 항목 |
| `test_sub_cards_cancel_scoped_to_service` (`:121`) | 서비스 스코프별 취소 건수 격리 |
| `test_series_buckets_multi_period` (`:142`) | 멀티 기간 버킷(이번달/지난달 신규) 정확성 |
| `test_service_revenue_and_subs_admin_only` (`:180`) | 서비스별 테이블은 admin 전용·스코프 카드 격리 |

테스트 실행:
```bash
# e2e 대시보드 테스트만
pytest tests/e2e/test_dashboard_page.py -v

# 통합 집계 테스트만
pytest tests/integration/test_dashboard.py -v
```

---

## 9. 유지보수 팁

### 9-1. 새 지표 카드를 추가하려면

1. `app/services/dashboard.py` — `DashboardData` 데이터클래스에 새 필드 추가
2. 같은 파일에 집계 함수 작성 후 `build_dashboard()`에서 호출
3. `app/admin/templates/dashboard.html` — 템플릿에 렌더 코드 추가
4. `tests/integration/test_dashboard.py` — 집계 값 검증 테스트 추가

### 9-2. 차트 타입을 바꾸거나 새 차트를 추가하려면

차트 매크로는 `app/admin/templates/_charts.html`에 모여 있습니다:

| 매크로 | 차트 종류 | 사용 위치 |
|--------|----------|----------|
| `area(series)` | 영역 차트 (단일 값) | 일반매출 12개월 |
| `bars(series, key_a, key_b, ...)` | 그룹 막대 | 구독 12개월 (전체/신규) |
| `donut(items, extra)` | 도넛 + 범례 | 구독 상태 비율 |
| `multiline(series, lines)` | 멀티라인 | 30일 추이 |
| `hbar(items)` | 수평 막대 랭킹 | (현재 대시보드에서 미사용) |

모든 차트는 **서버 사이드 SVG 렌더**입니다. 외부 JavaScript 라이브러리 없이 동작합니다.

### 9-3. 기간 필터를 추가하려면

현재는 항상 "이번 달"과 "최근 30일"이 고정입니다. 기간 필터 UI를 추가하려면:
- 라우트(`dashboard.py`)에서 쿼리 파라미터(`from`, `to`) 수신
- `month_start`를 파라미터 기반으로 계산해 `build_dashboard`에 전달
- `build_dashboard` 시그니처에 `month_start` 인자 추가 필요

### 9-4. is_admin 플래그

`app/admin/routes/dashboard.py:36`:
```python
is_admin=ctx.user.role == UserRole.SYSTEM_ADMIN
```
템플릿에서 `{% if is_admin and ... %}`로 서비스별 테이블 두 곳을 조건부 표시합니다.
새로운 "SYSTEM_ADMIN 전용" 섹션을 추가할 때 동일한 플래그를 사용하면 됩니다.

### 9-5. 성능 주의사항

- 12개월/30일 시리즈는 **DB 사이드 FILTER 집계**(테이블 1회 스캔)로 동작합니다
  (감사 Phase 3 — 성능 H3에서 전환; 구독 전체 메모리 적재 제거).
- `subscriptions(service_id)`, `payments(service_id, approved_at)`,
  `audit_logs(created_at)` 등 감사 Phase 3에서 추가한 인덱스가 집계 쿼리를 지원합니다.
- 서비스별 테이블(`_service_revenue`/`_service_subs`)은 서비스당 상관 서브쿼리를
  사용합니다 — 사내 서비스 수가 적은 동안은 충분하며, 수십 개 이상으로 늘어나면
  GROUP BY 조인으로 전환을 검토하세요.

### 9-6. 시간대 처리

- 집계 기준 시각은 항상 **UTC** (`app/core/clock.py:15` `utcnow()`).
- 화면 표시는 `{{ dt|kst(...) }}` 필터로 KST 변환 (`app/core/clock.py:24` `kst_format()`).
- 상단 시계는 JavaScript `toLocaleString('sv-SE', {timeZone: 'Asia/Seoul'})`로 매초 갱신.

### 9-7. 흔한 디버깅 포인트

| 증상 | 확인 위치 |
|------|----------|
| 매출이 0으로 표시됨 | `Payment.approved_at`이 UTC로 저장됐는지 확인. `requested_at`이 아닌 `approved_at` 기준임에 주의 |
| 환불이 집계 안 됨 | 환불은 `requested_at` 기준(`app/services/dashboard.py:128`). `approved_at`이 NULL일 수 있음 |
| 취소 건수 이상 | 감사 액션 이름 확인 — `subscription.cancel`(사용자), `subscription.force_cancel`(강제), `subscription.suspended`(결제 실패) 세 가지가 모두 포함됨 |
| SERVICE_MANAGER가 전체 데이터 봄 | `effective_service_ids()` 반환값 확인 — SYSTEM_ADMIN이면 None 반환 |
| 도넛이 "데이터 없음" 표시 | 구독이 실제로 없거나 스코프 내 데이터가 없는 정상 케이스 |
