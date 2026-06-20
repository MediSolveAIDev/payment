# 09. 대시보드 · 정산 집계

> 운영자가 보는 **읽기 전용 집계 화면** 둘. 대시보드는 한눈에 보는 현황,
> 정산은 기간별 매출 정산이다. 둘 다 데이터를 **바꾸지 않고 조회·합산만** 한다.
> 모든 집계는 스코프(`service_ids`)로 제한된다(문서 02).
>
> 선행: [02-admin-auth.md](02)(스코프), [03-plans.md](03)(금액), [00-overview.md](00)(UTC/KST).

---

## 0. 한눈에 보기

| 화면 | URL | 라우트 | 서비스 계층 | 권한 |
|---|---|---|---|---|
| 대시보드 | `GET /admin` | `dashboard` | `services/dashboard.build_dashboard` | `require_any`(스코프) |
| 정산 | `GET /admin/settlement` | `settlement_view` | `services/settlement.settlement_summary` | `require_any`(스코프) |
| 결제 이력 | `GET /admin/payments` | `payments_list` | 라우트 직접 | `require_any`(스코프) |
| 결제 상세 | `GET /admin/payments/{id}` | `payment_detail` | 라우트 직접 | `require_any`(스코프) |

- **읽기 전용**: 전부 SQL 집계/조회 쿼리만. 상태를 바꾸거나 결제하지 않는다.
- **스코프**: `SYSTEM_ADMIN`은 전체(`scope=None`), `SERVICE_MANAGER`는 담당 서비스만.
- **시간**: 저장·집계·필터는 UTC, 화면 표시만 KST(`kst` 필터, 문서 00). 대시보드 상단엔 실시간 시계.

관련 파일:
- `app/services/dashboard.py`, `app/services/settlement.py`
- `app/admin/routes/dashboard.py`, `app/admin/routes/settlement.py`, `app/admin/routes/subscriptions.py`
- 템플릿: `app/admin/templates/dashboard.html`, `settlement/index.html`, `payments/list.html`, `payments/detail.html`, `_charts.html`

---

## 1. 집계의 공통 도구 (`dashboard.py` 상단)

### 1-1. 스코프 적용 — `_scoped`

```python
def _scoped(query, scope: list[uuid.UUID] | None, col):
    return query.where(col.in_(scope)) if scope is not None else query
```

모든 집계 쿼리를 이걸로 감싼다. `scope`가 None(시스템관리자)이면 제한 없음, 리스트면
`col IN scope`. 이 한 줄이 SERVICE_MANAGER가 남의 서비스 수치를 못 보게 하는 공통 장치다.

### 1-2. "열린 구독" 조건 — `_open_subs_cond(at)`

```python
# _OPEN_STATUSES = (TRIAL, ACTIVE, PAST_DUE, SUSPENDED)
status IN _OPEN_STATUSES
  OR (status == CANCELED AND current_period_end > at)
```

어떤 시점 `at`에 "열려 있는" 구독. CANCELED는 기간이 남았을 때만 열린 것으로 본다.
EXPIRED는 항상 제외. 12개월 시계열, 일별 추이, 서비스별 집계가 공유한다.

### 1-3. 작은 집계 헬퍼들

- `_count(db, scope, *where)` — 조건에 맞는 **구독 수**.
- `_revenue_between(scope, start, end, kind=None)` — **DONE 결제의 `approved_at` 기준** 금액 합계. `kind` 지정 시 구독/일반 각각 합산.
- `_refund_between(scope, start, end)` — **CANCELED 결제의 `requested_at` 기준** 환불액 합계.
  부분환불을 정확히 반영하기 위해 `coalesce(canceled_amount, amount)`를 사용한다.
  `canceled_amount`가 있으면 수수료 공제 후 실제 환불액으로, 없으면 `amount` 전체로 집계.
  ```python
  # dashboard.py _refund_between
  q = select(func.coalesce(func.sum(
      func.coalesce(Payment.canceled_amount, Payment.amount)), 0)).where(
      Payment.status == PaymentStatus.CANCELED,
      Payment.requested_at >= start, Payment.requested_at < end)
  ```
- `_payment_count_between(scope, status, start, end)` — **`requested_at` 기준** 결제 건수(주로 FAILED 집계에 사용).
- `_audit_count(db, scope, actions, start, end)` — 감사로그 기반 액션 건수. 스코프는 target 구독의 서비스로 제한.

> ★ 중요한 구분: **매출은 `approved_at`(승인일)**, **환불·미결제 건수는 `requested_at`(요청일)** 기준이다.
> "돈이 실제 승인된 날"과 "시도한 날"이 다를 수 있어 의도적으로 다른 컬럼을 쓴다.

### 1-4. 취소가 감사로그 기반인 이유

구독 테이블만 보면 "왜 취소됐는지"를 구분할 수 없다. 그래서 감사 액션으로 나눈다:

- **사용자 취소** — `subscription.cancel`, `subscription.force_cancel`
- **결제 만료(정지)** — `subscription.suspended`
- **만료** — `subscription.expired`

`_audit_count`의 스코프는 `target_id`가 가리키는 구독의 서비스로 제한한다(서브쿼리 방식).

---

## 2. 대시보드 — `build_dashboard` (`dashboard.py`)

### 2-1. 라우트와 진입점

```python
# app/admin/routes/dashboard.py (요약)
data = await build_dashboard(db, ctx.service_ids)   # 스코프 전달
return render("dashboard.html", d=data, now=utcnow(),
              is_admin=(role == SYSTEM_ADMIN))
```

`build_dashboard`는 이번 달 기준(`month_start`=이번 달 1일 00:00:00 UTC)으로 다섯 종류의
데이터를 채운다. 반환 타입은 `DashboardData` 데이터클래스.

### 2-2. `DashboardData` 필드 목록

```python
@dataclass
class DashboardData:
    revenue_cards: list[StatCard]       # ① 매출 카드(총/구독/일반/환불)
    service_revenue: list[dict]         # ① 서비스별 매출표 (admin 전용)
    subs_months: list[dict]             # ② 12개월 구독 bars
    one_off_months: list[dict]          # ② 12개월 일반매출 area
    sub_flow: list[dict]                # ③ 도넛 옆 흐름 지표(신규/취소/만료/미결제)
    status_breakdown: list[dict]        # ③ 구독 상태 도넛(만료 포함)
    daily_trend: list[dict]             # ③ 30일 추이 멀티라인
    service_subs: list[dict]            # ③ 서비스별 구독표 (admin 전용)
    recent: list                        # 우측 레일: 최근 결제 8건
    past_due: list                      # 우측 레일: 미수 구독 5건
    expiring: list                      # 우측 레일: 만료 임박 5건
```

---

### 2-3. ① 매출 섹션

**`_revenue_cards`** — 이번 달 4개 통계 카드:

| 카드 | 집계 함수 | 기준 컬럼 | href |
|---|---|---|---|
| 총매출 | `_revenue_between` (kind 없음) | `approved_at` | `/admin/payments?status=DONE&from=..&to=..` |
| 구독매출 | `_revenue_between(kind=SUBSCRIPTION)` | `approved_at` | `/admin/payments?status=DONE&kind=SUBSCRIPTION&...` |
| 일반매출 | `_revenue_between(kind=ONE_OFF)` | `approved_at` | `/admin/payments?status=DONE&kind=ONE_OFF&...` |
| 환불금액 | `_refund_between` (CANCELED 합) | `requested_at` | `/admin/payments?status=CANCELED&...` |

카드는 `StatCard(label, value, delta, up, tint, href)`로 표현. `href`가 있으면 클릭 시
결제 이력 목록(필터 적용)으로 이동.

**서비스별 매출표** (`service_revenue`) — **SYSTEM_ADMIN 전용**(`scope is None`일 때만 생성).
상관 서브쿼리로 서비스마다 총/구독/일반/환불 금액을 한 쿼리로 집계. 행 클릭 → 서비스 상세.

```python
# service_revenue 행 구조
{"id": uuid, "name": str, "total": int, "sub": int, "one_off": int, "refund": int}
# refund는 coalesce(canceled_amount, amount) 합계 — 부분환불 반영
```

템플릿 조건: `{% if is_admin and d.service_revenue %}`.

---

### 2-4. ② 12개월 차트 섹션

**`_series_12m`** — 12개월 전부터 이번 달까지 루프하며 두 시리즈를 만든다:

- **`subs_months`** — `bars` 차트용. 각 달 말(= `min(달말, now)`) 시점의 열린 구독 전체 수(`total`)와 신규 구독 수(`new`). 키 이름은 `total`/`new`이며, `bars` 매크로 호출 시 `key_a='total', key_b='new'`로 전달한다.
  - 진행 중인 이번 달은 `now` 기준.
  - 현재 상태 기반 **근사** — 이미 EXPIRED된 구독은 과거 달에서도 빠진다.
- **`one_off_months`** — `area` 차트용. 각 달 `approved_at` 기준 일반(ONE_OFF) 매출 합.

```python
# subs_months 행 구조: total=전체구독수, new=신규구독수
{"label": "6월", "total": 전체구독수, "new": 신규구독수}

# one_off_months 행 구조
{"label": "6월", "value": 일반매출_원}
```

템플릿에서 `bars` 매크로 호출 시 label을 명시적으로 덮어씀:
```jinja
{{ charts.bars(d.subs_months, label_a='전체구독', label_b='신규구독',
               color_a='var(--accent-indigo)', color_b='var(--accent-mint)',
               key_a='total', key_b='new') }}
```

---

### 2-5. ③ 구독 섹션

**상태 도넛 `status_breakdown`**

`GROUP BY status`로 전체 상태를 한 번에 가져와 도넛 데이터로 변환.
EXPIRED 포함 6개 상태 모두 집계(도넛에 만료 포함, 요청 011).

```python
_STATUS_COLOR = {
    "TRIAL":     "var(--accent-cyan)",
    "ACTIVE":    "var(--accent-mint)",
    "PAST_DUE":  "var(--accent-orange)",
    "SUSPENDED": "var(--accent-red)",
    "CANCELED":  "var(--black-20)",
    "EXPIRED":   "var(--accent-indigo)",
}
# 각 항목에 href도 포함 → 클릭 시 /admin/subscriptions?status=<상태>
{"label": "활성", "value": 12, "color": "var(--accent-mint)", "href": "/admin/subscriptions?status=ACTIVE"}
```

데이터가 없으면 `{"label": "데이터 없음", "value": 1, "color": "var(--black-10)", "href": None}` 하나만.

**흐름 지표 `sub_flow`** — 도넛 옆에 가로 구분선 아래에 표시, 이번 달 기준:

| 항목 | 집계 방법 | href |
|---|---|---|
| 신규 구독 | `Subscription.created_at` 이번 달 내 count | `/admin/subscriptions?sort=created_at&dir=desc` |
| 구독 취소 | 감사(`subscription.cancel`+`force_cancel`+`suspended`) count | `/admin/subscriptions?status=CANCELED` |
| 구독 만료 | 감사(`subscription.expired`) count | `/admin/subscriptions?status=EXPIRED` |
| 미결제 | `Payment.status=FAILED` `requested_at` 기준 건수 | `/admin/payments?status=FAILED&from=..&to=..` |

각 항목 클릭 → 상세 목록 이동.

**30일 추이 `daily_trend`** — `_daily_trend` — 오늘 기준 30일치 일별 루프:

```python
# 각 row 구조
{"label": "6/9", "total": 열린구독전체, "new": 신규, "canceled": 취소+정지, "expired": 만료}
```

`multiline` 차트에 4개 시리즈로 렌더:
```jinja
{{ charts.multiline(d.daily_trend,
    [('total','전체구독','var(--accent-indigo)'), ('new','신규','var(--accent-mint)'),
     ('canceled','취소','var(--accent-orange)'), ('expired','만료','var(--accent-red)')]) }}
```

**서비스별 구독표 `service_subs`** — **SYSTEM_ADMIN 전용**. 상관 서브쿼리로 서비스마다:

```python
{"id": uuid, "name": str, "open": 현재열린, "new": 신규, "canceled": 취소+정지, "expired": 만료, "revenue": 구독매출}
```

---

### 2-6. 우측 레일 — `_rails`

`DashboardData.recent`, `past_due`, `expiring`을 채운다:

- **최근 결제** — `Payment JOIN Subscription`, `requested_at` 내림차순 8건. 클릭 → 구독 상세.
- **미수 구독** — `PAST_DUE·SUSPENDED`, `next_billing_at` 오름차순(null 마지막) 5건.
- **만료 임박** — `OPEN_STATUSES + CANCELED` 상태 중 `current_period_end`가 지금~7일 이내 5건.

세 목록 모두 스코프 적용.

---

### 2-7. SVG 차트 매크로 — `_charts.html`

**외부 차트 라이브러리 없이** Jinja 템플릿이 좌표를 계산해 `<svg>`를 서버에서 렌더한다.
JS 없이 즉시 표시된다.

| 매크로 | 데이터 형태 | 대시보드에서 사용처 |
|---|---|---|
| `area(series)` | `[{label, value}]` | 12개월 일반매출 |
| `bars(series, label_a, label_b, color_a, color_b, key_a, key_b)` | `[{label, total, new}]` | 12개월 구독(전체/신규) |
| `donut(items, extra=None)` | `[{label, value, color, href}]` + extra=`[{label, value, href}]` | 구독 상태 도넛 + 흐름 지표 |
| `multiline(series, lines)` | `[{label, k1, k2, ...}]`, `lines=[(key,라벨,색)]` | 30일 추이 |
| `hbar(items)` | `[{name, value}]` | (사용처 없음, 예비) |

`donut`의 `extra` 인자가 흐름 지표(`sub_flow`)를 도넛 아래에 가로 구분선과 함께 붙인다.

---

## 3. 정산 — `settlement_summary` + 라우트

### 3-1. `SettlementRow` 구조

```python
@dataclass
class SettlementRow:
    service_id: uuid.UUID
    service_name: str
    count: int           # DONE 결제 전체 건수
    amount: int          # 합계 금액(KRW)
    sub_amount: int      # 구독 결제 합계
    one_off_amount: int  # 일반(단건) 결제 합계
    sub_count: int       # 구독 결제 건수
    one_off_count: int   # 일반(단건) 결제 건수
```

### 3-2. `settlement_summary` 함수

```python
async def settlement_summary(
    db, scope, start, end,
    plan_name: str | None = None
) -> tuple[int, int, list[SettlementRow]]:
    # (총 건수, 총 금액, 서비스별 집계 — 금액 내림차순)
```

핵심 쿼리 구조:

```sql
SELECT service.id, service.name,
       COUNT(payment.id),
       COALESCE(SUM(amount), 0),
       COALESCE(SUM(CASE WHEN kind='SUBSCRIPTION' THEN amount ELSE 0)), ...
FROM payment
JOIN service ON payment.service_id = service.id
WHERE payment.status = 'DONE'
  [AND payment.approved_at >= start]   -- ★ 승인일 기준
  [AND payment.approved_at < end]      --   반개구간 [start, end)
  [AND payment.service_id IN scope]
  [AND plan.name = plan_name]          -- 요금제 필터 시: JOIN Subscription, Plan 추가
GROUP BY service.id, service.name
ORDER BY 금액 DESC, service.name
```

중요 포인트:
- **정산 기준은 `approved_at`(승인일)** — "그 기간에 실제로 승인된 돈"만 집계. DONE만 합산.
- **반개구간 `[start, end)`** — 경계 중복/누락 없음.
- **요금제 필터**: `plan_name` 지정 시 `Subscription`, `Plan`을 추가 JOIN. 구독 연결이 없는 ONE_OFF 결제는 이때 자동으로 제외된다.
- 반환값의 총 건수·총 금액은 `rows`에서 sum한 값(서버에서 계산).

### 3-3. 라우트 — `settlement_view` (단일 화면, 두 모드)

**공통 컨텍스트 — `_settlement_context`**

```python
async def _settlement_context(request, pp, ctx, db):
    # from/to 둘 다 없으면 이번달 1일~오늘로 기본값 설정
    if "from" not in query_params and "to" not in query_params:
        pp.filters["from"] = now.strftime("%Y-%m-01")
        pp.filters["to"] = now.strftime("%Y-%m-%d")
    start, end = date_range(pp)          # YYYY-MM-DD → UTC 반개구간
    scope = ctx.service_ids
    selected = None
    if raw_sid:                          # service_id 파라미터 있으면
        if scope is not None and sid not in scope:
            raise NotFoundError(...)     # 스코프 밖 → 404
        selected = await db.get(Service, sid)
    return start, end, scope, selected
```

**두 모드**:

**[전체 모드]** — `service_id` 없음:
- `sum_scope = scope`(전체 또는 담당)로 `settlement_summary` 호출
- 상단: 전체 정산금액(stat 카드) + 구독 정산금액·일반결제 정산금액(각각 건수 포함)
- 본문: 서비스별 집계 표(서비스명·건수·구독금액·일반금액·합계·상세보기 버튼)
- 상세보기 클릭 → 같은 URL에 `service_id`를 추가(기간 파라미터 유지)

**[서비스별 모드]** — `service_id` 지정:
- `sum_scope = [selected.id]`로 `settlement_summary` 호출
- 상단: 선택 서비스 정산금액 + 구독/일반 분리 금액(건수)
- 본문: 결제 건별 목록(승인시각·사용자·주문번호·유형·종류·금액·상세보기 버튼)
  - 구독 결제(`sub`가 있음) → 상세보기: `/admin/subscriptions/{sub.id}`
  - 일반 결제(`sub`가 None) → 상세보기: `/admin/payments/{p.id}`
- 페이지네이션: `approved_at`·`amount` 정렬 지원

**공통 필터**:
- **월 선택** `<input type="month" data-settle-month>` — JS로 그 달 1일~말일을 `from`/`to`에 채워 `requestSubmit()`. 서버는 `from`/`to`만 받는다.
- **요금제 필터** `plan_name` — `plan_name_options(db, scope, raw_sid)`로 선택 범위 내 요금제 목록 제공.
- **엑셀 다운로드** `/admin/settlement/export.xlsx` — 전체 모드: 서비스별 합계(서비스·건수·구독매출·일반매출·합계), 서비스별 모드: 결제 건별(승인시각·사용자·주문번호·유형·종류·금액).

---

## 4. 결제 이력 목록/상세

### 4-1. `_build_payments_query` — 공통 쿼리 빌더

```python
base = (select(Payment, Subscription, Service)
        .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
        .outerjoin(Plan, Subscription.plan_id == Plan.id)
        .join(Service, Payment.service_id == Service.id))
```

**중요**: Subscription을 **outerjoin**(LEFT JOIN). 일반결제(ONE_OFF)는 `subscription_id=None`이라 inner join이면 사라진다. Plan도 마찬가지로 outerjoin.

스코프: `Payment.service_id IN scope`.

지원 필터:

| 파라미터 | 컬럼 | 비고 |
|---|---|---|
| `status` | `Payment.status` | DONE/FAILED/PENDING/CANCELED |
| `kind` | `Payment.kind` | SUBSCRIPTION/ONE_OFF |
| `plan_name` | `Plan.name` | outerjoin이라 지정 시 구독결제만 남음 |
| `service_id` | `Payment.service_id` | 유효 UUID만 적용, 잘못된 형식은 무시 |
| `from`/`to` | `Payment.requested_at` | 결제 목록은 **요청일** 기준 |
| `q` (검색) | `order_id` ILIKE, `external_user_id` ILIKE | — |

> 주의: 결제 목록 날짜 필터는 `requested_at`(요청일) 기준이다. 정산의 `approved_at` 기준과 다르다.

정렬 가능 컬럼: `order_id`, `amount`, `status`, `requested_at`. 기본 정렬: `requested_at` DESC.

### 4-2. `payments_list` — 결제 이력 목록

`_build_payments_query`로 쿼리 빌드 → `paginate`. 템플릿: `payments/list.html`.

툴바 필터 순서: 서비스 → 요금제 → 종류 → 상태 → 기간(from/to).

목록 컬럼: 주문번호(링크)·서비스·종류·사용자·유형·금액·상태·실패코드·요청시각.
주문번호 클릭 → `/admin/payments/{p.id}` 결제 상세.

종류(`kind`)·유형(`payment_type`)·상태(`status`) 모두 색 배지로 표시:
```jinja
<span class="badge badge-{{ p.kind }}">구독/일반</span>
<span class="badge badge-{{ p.payment_type }}">{{ p.payment_type }}</span>
<span class="badge badge-{{ p.status }}">{{ p.status }}</span>
```

### 4-3. `payment_detail` — 결제 상세

```python
@router.get("/payments/{payment_id}")
async def payment_detail(payment_id, request, ctx, db):
    payment = await db.get(Payment, payment_id)
    if payment is None or (scope is not None and payment.service_id not in scope):
        raise NotFoundError(...)
    service = await db.get(Service, payment.service_id)
    sub = await db.get(Subscription, payment.subscription_id) if payment.subscription_id else None
    return render("payments/detail.html", ...)
```

스코프 제한: `payment.service_id not in scope`이면 404. 없는 결제도 404.

표시 필드: 주문번호·종류·결제유형·서비스·사용자·금액·상태·실패코드·실패메시지·요청시각·승인시각·토스결제키·연결구독.
- 연결 구독이 있으면 `/admin/subscriptions/{sub.id}` 링크.
- `payment.raw_response`가 있으면 별도 카드에 JSON 원본 응답 표시.

---

## 5. 두 화면의 핵심 구분

| 항목 | 대시보드 | 정산 | 결제 이력 |
|---|---|---|---|
| 목적 | 현황 한눈에(이번 달 + 추세 + 레일) | 기간별 매출 정산(서비스별) | 결제 건별 조회·상세 |
| 기간 | 이번 달 고정 + 최근 12개월 차트 + 30일 추이 | 사용자가 from/to 선택(기본 이번 달) | 사용자가 from/to 선택(기본 없음) |
| 매출 기준 | `approved_at` | `approved_at` | N/A |
| 날짜 필터 기준 | 내부 고정 | `approved_at` | `requested_at` |
| 환불 기준 | `requested_at` (CANCELED, 금액=`coalesce(canceled_amount, amount)`) | 집계 대상 아님(DONE만) | `requested_at` 필터로 조회 가능 |
| 서비스별 표 | SYSTEM_ADMIN 전용 | 전체 모드(기간 합계) | 서비스 필터로 제한 가능 |
| 데이터 변경 | 없음(조회) | 없음(조회) | 없음(조회) |

공통: `_scoped`/`scope IN`으로 스코프 제한, UTC 집계·KST 표시, 클릭 시 상세로 드릴다운.

---

## 6. 예외 · 주의

| 상황 | 처리 |
|---|---|
| 데이터 없음(신규) | 도넛 "데이터 없음", 금액 0 — 안 깨짐. `COALESCE(SUM, 0)` 전체 적용 |
| SERVICE_MANAGER 접근 | 자기 서비스만 집계, `service_revenue`/`service_subs` 미노출, 타 서비스 정산 404 |
| 잘못된 날짜 파라미터 | `date_range`가 무시(열린 범위). 잘못된 UUID는 filter pop 후 전체로 폴백 |
| 타 서비스 service_id 직접 요청 | 정산·결제 모두 404(`NotFoundError`) |
| 요금제 필터 + 일반결제 | `plan_name` 지정 시 Subscription/Plan JOIN → ONE_OFF 자동 제외(의도된 동작) |
| 과거 월 구독수 스냅샷 | 현재 상태 기반 **근사**(EXPIRED는 과거에서도 빠짐) — 코드 주석 명시 |
| 매출 vs 결제 건수 숫자 불일치 | 기준 컬럼이 다름(approved_at vs requested_at) — 의도된 동작 |

---

## 7. 관련 테스트

- `tests/integration/test_dashboard.py` — 매출 카드(총/구독/일반/환불), 흐름 지표(취소/만료 감사 기반), 도넛 EXPIRED 포함, 12개월 시리즈(전체/신규 bars + 일반매출 area), 30일 추이(30개 항목 + 키 검증), 취소 스코프 제한, 서비스별 매출·구독표(admin 전용·매니저 미노출).
- `tests/integration/test_settlement.py` — 서비스별 그룹·금액 내림차순, 기간 반개구간 경계, 스코프 제한, 열린 범위(start/end None), 구독/일반 분리 금액·건수, 요금제 필터(ONE_OFF 제외).
- `tests/e2e/test_dashboard_page.py` — 매출 카드 4종, 서비스별 매출(admin), 구독 상태 도넛+흐름 지표, 12개월 차트 제목, 만료 상태 링크, 매니저 스코프(서비스별 표 미노출), 실시간 시계.
- `tests/e2e/test_settlement_page.py` — 전체/서비스별 모드, 기본 기간, 스코프 404, 월 선택 input, 구독/일반 분리 카드, 서비스별 모드 일반결제 상세보기(`/admin/payments/{id}`), 요금제 필터.
- `tests/e2e/test_admin_operations.py` — 결제 목록 날짜 필터(`requested_at`), 종류·서비스 필터, 필터 UI 순서, 요금제 필터(ONE_OFF outerjoin 회귀), 종류·유형 배지, 결제 상세(필드·scope 404).

---

## 8. 유지보수 체크리스트

1. **집계 쿼리엔 항상 `_scoped`**(대시보드)·`scope IN`(정산). 빠뜨리면 교차 서비스 수치 노출.
2. **매출=approved_at, 환불·미결제=requested_at** 기준을 섞지 말 것. 새 지표 추가 시 어느 컬럼인지 명확히.
   **환불 집계는 `coalesce(canceled_amount, amount)`** — 수수료 공제 부분환불을 정확히 반영한다.
   `_refund_between`과 `_service_revenue`의 refund 서브쿼리 둘 다 동일 규칙을 따른다.
   웹훅으로 들어온 외부 전액취소는 `canceled_amount = payment.amount`로 기록되므로
   `coalesce` 결과가 `amount`와 동일해 집계에 영향 없다.
3. **취소·만료 구분은 감사로그 기반**. 구독 테이블엔 취소 사유가 없으므로 `_audit_count`를 사용.
4. **서비스별 매출·구독표는 SYSTEM_ADMIN 전용**(`scope is None`). 매니저에게 노출하지 말 것.
5. **결제 목록 `_build_payments_query`는 Subscription/Plan을 outerjoin**. inner join으로 바꾸면 일반결제(ONE_OFF)가 사라진다.
6. **새 카드/차트 추가**: `dashboard.py`에 집계 헬퍼 추가 → `DashboardData`에 필드 → 템플릿 → `test_dashboard.py`. 0 나누기·빈 데이터 방어 필수(`COALESCE`).
7. **금액 표시는 정수 KRW**(문서 03). 통화/소수 도입 시 billing_math와 함께 검토.
8. **성능**: 12개월·30일 시계열은 월/일별 루프로 다수 쿼리를 실행(사내 도구 규모에서 수용 가능). 데이터 급증 시 윈도우 함수 1쿼리 또는 Redis 캐시(60초 TTL) 도입 고려.
