# 02. 대시보드

> **접근 경로**: `GET /admin/` (로그인 후 첫 화면)
> **권한**: SYSTEM_ADMIN, SERVICE_MANAGER 모두 접근 가능
> **쓰기 없음**: 조회 전용 화면입니다.
>
> 기능 내부 처리 흐름(집계 로직·DB 쿼리·테스트)은 [../11-dashboard.md](../11-dashboard.md) 참조.
> 정산 화면은 [07-settlement.md](07-settlement.md) 참조.

---

## 1. 이 화면은 무엇인가 / 접근 경로·권한

어드민 콘솔에 로그인하면 가장 먼저 표시되는 **메인 화면**입니다.
이번 달 매출 요약, 구독 상태 현황, 12개월/30일 추이 차트, 우측 패널(최근 결제·미수 구독·만료 임박)을 한 화면에 보여줍니다.

| 항목 | 값 |
|------|-----|
| URL | `GET /admin/` |
| 라우트 함수 | `app/admin/routes/dashboard.py:25` `dashboard()` |
| 템플릿 | `app/admin/templates/dashboard.html` |
| 인증 의존성 | `require_any` (`app/admin/deps.py:102`) — SYSTEM_ADMIN 또는 SERVICE_MANAGER |

**역할별 접근 범위**:

| 역할 | 조회 범위 | 서비스별 테이블 표시 |
|------|----------|-------------------|
| SYSTEM_ADMIN | 전체 서비스 (`scope=None`) | 표시 (`is_admin=True`) |
| SERVICE_MANAGER | 담당 서비스만 (`scope=[uuid, ...]`) | 숨김 (`is_admin=False`) |

`is_admin` 플래그는 `app/admin/routes/dashboard.py:36`에서 `ctx.user.role == UserRole.SYSTEM_ADMIN`으로 결정됩니다.
스코프 결정은 `app/admin/deps.py:77` `effective_service_ids()` 호출 결과가 `AdminContext.service_ids`에 저장되어 `build_dashboard(db, ctx.service_ids)`로 전달됩니다(`app/admin/routes/dashboard.py:34`).

---

## 2. 화면 구성 — 무엇이 보이나

### 2-1. 상단: 시계

`app/admin/templates/dashboard.html:9-23`

우측 상단에 **현재 KST 시각**이 표시됩니다.
서버 초기 렌더(`{{ now|kst("%Y-%m-%d %H:%M:%S") }}`) 후 JavaScript `setInterval(tick, 1000)`으로 매초 브라우저 로컬 시각(`Asia/Seoul`)으로 갱신됩니다.
요소 ID: `dash-clock`.

---

### 2-2. 매출 섹션 (이번 달)

`app/admin/templates/dashboard.html:26-54`

#### 매출 카드 4개

`d.revenue_cards` — `app/services/dashboard.py:141` `_revenue_cards()` 반환값.
집계 기준: `[이번 달 1일 00:00:00 UTC, 현재]` 반개구간.

| 카드 | 집계 기준 | tint | 클릭 이동 |
|------|----------|------|----------|
| 총매출 | `Payment.status=DONE`, `approved_at` 기준 전체 | 3 | `/admin/payments?status=DONE&from=...&to=...` |
| 구독매출 | 위 + `kind=SUBSCRIPTION` | 1 | `/admin/payments?status=DONE&kind=SUBSCRIPTION&...` |
| 일반매출 | 위 + `kind=ONE_OFF` | 2 | `/admin/payments?status=DONE&kind=ONE_OFF&...` |
| 환불금액 | `Payment.status=CANCELED`, `coalesce(canceled_amount, amount)` 합산, `requested_at` 기준 | 4 | `/admin/payments?status=CANCELED&...` |

> 환불금액이 0원이면 `up=True`(긍정 색상)로 표시됩니다 (`app/services/dashboard.py:161`).
> 부분환불은 `canceled_amount`를 우선 사용합니다 (`app/services/dashboard.py:126-130`).

카드를 클릭하면 해당 기간·조건으로 필터된 결제 목록으로 이동합니다.

#### 서비스별 매출 테이블 (SYSTEM_ADMIN 전용)

`app/admin/templates/dashboard.html:36-54` `{% if is_admin and d.service_revenue %}`

| 컬럼 | 설명 |
|------|------|
| 서비스 | 서비스 이름 (클릭 시 서비스 상세) |
| 총매출 | 이번 달 DONE 결제 합계 |
| 구독 | 이번 달 SUBSCRIPTION DONE 합계 |
| 일반 | 이번 달 ONE_OFF DONE 합계 |
| 환불 | 이번 달 CANCELED `coalesce(canceled_amount, amount)` 합계 |

총매출 내림차순 정렬. 행 클릭 시 `/admin/services/{id}` 이동.
SERVICE_MANAGER로 로그인하면 이 테이블은 완전히 숨겨집니다.

---

### 2-3. 12개월 차트 (2-열 그리드)

`app/admin/templates/dashboard.html:57-68`

#### 최근 12개월 구독 (그룹 막대 차트)

`charts.bars(d.subs_months, label_a='전체구독', label_b='신규구독', color_a='var(--accent-indigo)', color_b='var(--accent-mint)', key_a='total', key_b='new')`

`d.subs_months` — `app/services/dashboard.py:243` `_series_12m()` 반환의 첫 번째 값.
현재 월 포함 과거 12개월, 각 항목: `{label: "N월", total: 전체구독수, new: 신규구독수}`.

- `total`: 해당 월 말 시점 "열린" 구독 수 스냅샷 (Python 버킷 계산, `_open_count_at()` — `app/services/dashboard.py:200`)
- `new`: 해당 월에 `created_at`이 속하는 신규 구독 수

#### 최근 12개월 일반매출 (영역 차트)

`charts.area(d.one_off_months)`

`d.one_off_months` — 같은 `_series_12m()` 반환의 두 번째 값.
각 항목: `{label: "N월", value: ONE_OFF DONE 합산액}`.

---

### 2-4. 구독 섹션 (2-열 그리드)

`app/admin/templates/dashboard.html:71-84`

#### 구독 상태 도넛 + 흐름 지표

`charts.donut(d.status_breakdown, extra=d.sub_flow)`

**도넛** (`d.status_breakdown`): 전체 구독의 상태별 비율 — **EXPIRED(만료) 포함** (`app/services/dashboard.py:392-409`).
데이터 없으면 "데이터 없음" 회색 도넛이 표시됩니다 (`app/services/dashboard.py:408-409`).

| 상태 | 한글 | 색상 CSS 변수 | 클릭 이동 |
|------|------|-------------|----------|
| TRIAL | 체험 | `--accent-purple` | `/admin/subscriptions?status=TRIAL` |
| ACTIVE | 활성 | `--accent-blue` | `/admin/subscriptions?status=ACTIVE` |
| PAST_DUE | 미수 | `--accent-yellow` | `/admin/subscriptions?status=PAST_DUE` |
| SUSPENDED | 정지 | `--accent-orange` | `/admin/subscriptions?status=SUSPENDED` |
| CANCELED | 취소 | `--accent-cyan` | `/admin/subscriptions?status=CANCELED` |
| EXTENDED | 연장처리 | `--accent-mint` | `/admin/subscriptions?status=EXTENDED` |
| EXPIRED | 만료 | `--accent-red` | `/admin/subscriptions?status=EXPIRED` |

도넛 중앙에 전체 구독 수가 표시됩니다. 각 항목을 클릭하면 해당 상태로 필터된 구독 목록으로 이동합니다.

**도넛 옆 흐름 지표** (`d.sub_flow`): 이번 달 구독 변동 현황.
`app/services/dashboard.py:166` `_sub_flow()` 반환값.

| 지표 | 집계 기준 | 클릭 이동 |
|------|----------|----------|
| 신규 구독 | `Subscription.created_at` ∈ [월초, 현재] | `/admin/subscriptions?sort=created_at&dir=desc` |
| 구독 취소 | 감사 액션 `subscription.cancel` + `subscription.force_cancel` + `subscription.suspended` 건수 합산 | `/admin/subscriptions?status=CANCELED` |
| 구독 만료 | 감사 액션 `subscription.expired` 건수 | `/admin/subscriptions?status=EXPIRED` |
| 미결제 | `Payment.status=FAILED` · `requested_at` ∈ [월초, 현재] 건수 | `/admin/payments?status=FAILED&...` |

> 취소 집계는 사용자 직접 취소(`subscription.cancel`, `subscription.force_cancel`)와 결제 실패로 인한 강제 정지(`subscription.suspended`)를 합산합니다.

#### 최근 30일 구독 추이 (멀티라인 차트)

`charts.multiline(d.daily_trend, [('total','전체구독','var(--accent-indigo)'), ('new','신규','var(--accent-mint)'), ('canceled','취소','var(--accent-orange)'), ('expired','만료','var(--accent-red)')])`

`d.daily_trend` — `app/services/dashboard.py:262` `_daily_trend()` 반환값.
30개 항목(`M/D` 레이블), 각 항목: `{label, total, new, canceled, expired}`.
X축 레이블은 5개 간격 + 마지막 날만 표시됩니다(`_charts.html:125`).

#### 서비스별 구독 테이블 (SYSTEM_ADMIN 전용)

`app/admin/templates/dashboard.html:85-104` `{% if is_admin and d.service_subs %}`

| 컬럼 | 설명 |
|------|------|
| 서비스 | 서비스 이름 (클릭 시 서비스 상세) |
| 현재구독 | 현재 시점 "열린" 구독 수 |
| 신규 | 이번 달 신규 생성 구독 수 |
| 취소 | 이번 달 취소 감사 이벤트 건수 |
| 만료 | 이번 달 만료 감사 이벤트 건수 |
| 구독매출 | 이번 달 SUBSCRIPTION DONE 합계 |

현재구독 수 내림차순 정렬. 행 클릭 시 `/admin/services/{id}` 이동.
SERVICE_MANAGER로 로그인하면 이 테이블도 완전히 숨겨집니다.

---

### 2-5. 우측 패널 (rail)

`app/admin/templates/dashboard.html:107-155` `{% block rail %}`

페이지 우측에 항상 표시되는 사이드 패널입니다. `_rails()` 반환값(요청 015로 패널 4개).

#### 최근 구독 (최대 8건) — 요청 015 1.1.1, 레일 최상단

`d.recent_subs` — `Subscription`, `created_at DESC`. 각 항목: 사용자 ID, 상태 뱃지, 생성일(KST). 클릭 시 구독 상세.

#### 최근 결제 (최대 8건) — 트라이얼·0원 포함(요청 015 1.1.2)

`d.recent`(dict 목록) — 실제 `Payment` + **트라이얼/0원 첫결제 구독**(Payment 없음)을 0원으로 합쳐 시간순 정렬한 상위 8건.

각 항목: 사용자 ID, 금액(트라이얼/0원은 `0원`), 상태 뱃지(한글: 완료/실패/대기/체험), 시각(KST). 클릭 시 구독 상세.
아이콘 배경색: DONE=mint, FAILED=red, TRIAL=purple, 그 외=orange.

#### 미수 구독 (최대 5건)

`d.past_due` — `status IN (PAST_DUE, SUSPENDED)`, `next_billing_at ASC`.

각 항목: 사용자 ID, 상태 뱃지, 다음 청구일(KST). 클릭 시 구독 상세.

#### 만료 임박 — 7일 이내 (최대 5건)

`d.expiring` — `current_period_end ∈ [now, now+7일)`, 열린 상태(TRIAL/ACTIVE/PAST_DUE/SUSPENDED/CANCELED 기간 내), `current_period_end ASC`.

각 항목: 사용자 ID, 만료 예정일(KST). 클릭 시 구독 상세.

---

## 3. 할 수 있는 동작

대시보드는 **조회 전용** 화면입니다. 쓰기(POST/PUT/DELETE) 동작이 없습니다.

| 동작 | 결과 |
|------|------|
| 매출 카드 클릭 | 해당 기간·조건으로 필터된 결제 목록으로 이동 |
| 구독 상태 도넛 항목 클릭 | 해당 상태로 필터된 구독 목록으로 이동 |
| 도넛 옆 흐름 지표 클릭 | 해당 조건 구독/결제 목록으로 이동 |
| 서비스별 매출 행 클릭 | 해당 서비스 상세 페이지로 이동 |
| 서비스별 구독 행 클릭 | 해당 서비스 상세 페이지로 이동 |
| 최근 결제 항목 클릭 | 해당 구독 상세 페이지로 이동 |
| 미수 구독 항목 클릭 | 해당 구독 상세 페이지로 이동 |
| 만료 임박 항목 클릭 | 해당 구독 상세 페이지로 이동 |

---

## 4. 개발 참조

### 4-1. 라우트 함수

`app/admin/routes/dashboard.py:25-36`

```python
@router.get("")
async def dashboard(request: Request,
                    ctx: AdminContext = Depends(require_any),
                    db: AsyncSession = Depends(get_db)):
    data = await build_dashboard(db, ctx.service_ids)
    return render(request, "dashboard.html", ctx=ctx, d=data, now=utcnow(),
                  is_admin=ctx.user.role == UserRole.SYSTEM_ADMIN)
```

- `require_any` = `require_role(SYSTEM_ADMIN, SERVICE_MANAGER)` (`app/admin/deps.py:102`)
- `ctx.service_ids`: SYSTEM_ADMIN이면 `None`, SERVICE_MANAGER이면 `[uuid, ...]` (`app/admin/deps.py:57`)
- `is_admin`: 템플릿에서 서비스별 테이블 두 곳의 조건부 렌더에 사용

### 4-2. 스코프 처리 — `_scoped()`

`app/services/dashboard.py:76-77`

```python
def _scoped(query, scope: list[uuid.UUID] | None, col):
    return query.where(col.in_(scope)) if scope is not None else query
```

`scope=None`(SYSTEM_ADMIN)이면 조건을 추가하지 않아 전체 데이터를 조회합니다.
`scope=[...]`이면 `WHERE {col} IN (...)` 조건을 붙여 해당 서비스 데이터만 조회합니다.
`scope=[]`(담당 서비스 없는 SERVICE_MANAGER)이면 `WHERE ... IN ()` → 결과 0으로 처리됩니다.

### 4-3. `build_dashboard()` 집계 순서

`app/services/dashboard.py:374-415`

| 단계 | 함수 | 위치 | 설명 |
|------|------|------|------|
| A | `GROUP BY status` 인라인 쿼리 | `:392-398` | 도넛용 상태별 구독 수 집계 |
| B | `_fetch_sub_states()` | `:400` / `:189` | 구독 전체 1회 조회 — C·E·F가 공유(N+1 제거) |
| C | `_revenue_cards()` | `:401` / `:141` | 매출 카드 4개 |
| D | `_sub_flow()` | `:402` / `:166` | 도넛 옆 흐름 지표 4개 |
| E | `_series_12m()` | `:403` / `:243` | 12개월 구독 수·일반매출 시리즈 |
| F | `_daily_trend()` | `:404` / `:262` | 30일 일별 추이 |
| G | `status_breakdown` 조립 | `:405-409` | 도넛 데이터 (색상·링크 포함) |
| H | `_service_revenue()` / `_service_subs()` | `:411-413` | **scope=None일 때만** (SYSTEM_ADMIN 전용) |
| I | `_rails()` | `:414` / `:347` | 우측 패널 3개 |

> `_fetch_sub_states()`가 `(status, created_at, current_period_end)` 전 행을 Python 메모리에 로드합니다(`app/services/dashboard.py:193` 주석).
> 12개월 시리즈와 30일 추이 모두 이 데이터를 Python 버킷 연산으로 재활용해 N+1 쿼리를 제거합니다.

### 4-4. 차트 매크로 — `_charts.html`

`app/admin/templates/_charts.html` — 서버 사이드 SVG 렌더. 외부 JS 라이브러리 없음.

| 매크로 | 위치 | 차트 종류 | 대시보드 사용처 |
|--------|------|----------|--------------|
| `area(series)` | `:4` | 영역 차트 (단일 값) | 12개월 일반매출 |
| `bars(series, key_a, key_b, ...)` | `:79` | 그룹 막대 | 12개월 구독 (전체/신규) |
| `donut(items, extra)` | `:34` | 도넛 + 범례 + extra 섹션 | 구독 상태 비율 + 흐름 지표 |
| `multiline(series, lines)` | `:103` | 멀티라인 | 30일 추이 (전체/신규/취소/만료) |
| `hbar(items)` | `:137` | 수평 막대 랭킹 | (현재 대시보드에서 미사용) |

**`donut(items, extra)`**: `extra` 파라미터에 `d.sub_flow`를 전달하면 도넛 범례 아래 구분선 이후 흐름 지표(`_charts.html:64-73`)가 렌더됩니다.

### 4-5. `is_admin` 전용 섹션

`app/admin/templates/dashboard.html:36` 서비스별 매출: `{% if is_admin and d.service_revenue %}`
`app/admin/templates/dashboard.html:85` 서비스별 구독: `{% if is_admin and d.service_subs %}`

`d.service_revenue`와 `d.service_subs`는 `build_dashboard`에서 `scope is None`일 때만 채워집니다(`app/services/dashboard.py:411-413`).
`is_admin=False`이거나 리스트가 비어 있으면 두 테이블 모두 DOM에 출력되지 않습니다.

### 4-6. 기간 기본값 (고정)

`app/services/dashboard.py:390`

```python
now = utcnow()
month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
```

- 매출 카드·흐름 지표: `[month_start, now + 1초)` (이번 달 전체)
- 12개월 시리즈: `month_start - 11개월` ~ `now`
- 30일 추이: `today - 29일` ~ `today`

반개구간 `[start, end)`. `end = now + relativedelta(seconds=1)` 으로 `now`를 포함시킵니다(`app/services/dashboard.py:148`).

### 4-7. 기능 상세 문서

집계 로직 전체, DB 테이블 목록, N+1 최적화 설명, 테스트, 유지보수 팁은 [../11-dashboard.md](../11-dashboard.md) 참조.
정산 화면은 [07-settlement.md](07-settlement.md) 참조.

---

## 5. 주의사항 / 자주 하는 실수

| 증상 | 원인 및 확인 위치 |
|------|-----------------|
| 매출 카드가 0원으로 표시됨 | 집계 기준은 `approved_at`(구독매출·일반매출·총매출). `requested_at`과 혼동하지 말 것 (`app/services/dashboard.py:112`) |
| 환불이 집계 안 됨 | 환불 기준일은 `approved_at`이 아닌 `requested_at` (`app/services/dashboard.py:128`). `approved_at`이 NULL인 경우 있음 |
| 취소 건수가 예상과 다름 | 세 가지 감사 액션 합산: `subscription.cancel` + `subscription.force_cancel` + `subscription.suspended` (`app/services/dashboard.py:71,173`) |
| SERVICE_MANAGER가 전체 데이터를 봄 | `effective_service_ids()` 반환값 확인 — SYSTEM_ADMIN은 `None` 반환, SERVICE_MANAGER는 `[uuid, ...]` |
| 도넛이 "데이터 없음"으로 표시됨 | 스코프 내 구독이 없는 정상 케이스 (`app/services/dashboard.py:408-409`). 오류 아님 |
| 서비스별 테이블이 안 보임 | SERVICE_MANAGER로 로그인 시 정상. `is_admin` 플래그(`app/admin/routes/dashboard.py:36`)와 `scope=None` 조건(`app/services/dashboard.py:411`) 확인 |
| 시계가 멈춤 | `id="dash-clock"` 엘리먼트 누락 또는 JavaScript 오류. `dashboard.html:13-22` 확인 |
| 담당 서비스 없는 계정에 오류 발생 | `scope=[]`이면 `WHERE ... IN ()` 처리 → 전 지표 0 반환. 오류 없음 |
