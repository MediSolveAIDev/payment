# 10. 정산(어드민 화면)

> **상호참조**:
> 결제 모델·흐름 → [07. 단건(일반) 결제 + 취소](07-one-off-payment.md) |
> 테이블 구조 → [02. 데이터베이스](02-database.md) |
> 대시보드 → [11. 대시보드](11-dashboard.md)

---

## 1. 한 줄 요약

기간·서비스·요금제로 필터링해 **승인(DONE)+취소(CANCELED) 결제**(approved_at 기준)를 서비스별로 합산하고, 총매출/환불/순매출과 구독·일반 매출을 분리 표시하는 **조회 전용** 어드민 화면입니다. 취소(환불) 내역도 반영되어 **순매출 = 총매출 − 환불**로 보여줍니다. DB에는 아무것도 쓰지 않으며, 엑셀 다운로드도 제공합니다.

---

## 2. 언제 실행되나

| 트리거 | 설명 |
|--------|------|
| 어드민 콘솔 `GET /admin/settlement` | 정산 요약/건별 목록 화면 조회 |
| 어드민 콘솔 `GET /admin/settlement/export.xlsx` | 엑셀 다운로드 |

두 엔드포인트 모두 **SYSTEM_ADMIN 또는 SERVICE_MANAGER** 로그인 사용자만 접근할 수 있습니다.
- `SYSTEM_ADMIN`: 모든 서비스 조회 가능
- `SERVICE_MANAGER`: 자신이 담당하는 서비스만 조회 가능

---

## 3. 요청 진입점

| 역할 | 파일:줄 | HTTP |
|------|---------|------|
| 화면 조회 | `app/admin/routes/settlement.py:98-157` | `GET /admin/settlement` |
| 엑셀 다운로드 | `app/admin/routes/settlement.py:160-200` | `GET /admin/settlement/export.xlsx` |

라우터는 `app/admin/__init__.py:82,93` 에서 `router.include_router(settlement.router)` 로 등록됩니다.

좌측 사이드바 내비게이션 링크는 `app/admin/templates/base.html:33` 에 있습니다:
```
{{ nav('/admin/settlement', 'calculator', '정산') }}
```

---

## 4. 단계별 처리 흐름

### 4-1. 공통 전처리: `_settlement_context`

`app/admin/routes/settlement.py:59-95`

화면 조회와 엑셀 다운로드 모두 이 함수를 먼저 거칩니다. 순서대로:

1. **기간 기본값 설정** (59-80줄): `from`/`to` 파라미터가 모두 없으면 당월 1일~오늘을 자동으로 설정합니다. 사용자가 파라미터 없이 `/admin/settlement`에 들어오면 "이번달"이 자동으로 선택됩니다.

2. **`date_range` 호출** (`app/admin/pagination.py:128-144`): `YYYY-MM-DD` 문자열을 UTC `datetime`으로 파싱하고, `end`는 **익일 0시**로 올려 반개구간 `[start, end)`을 만듭니다. 예를 들어 `to=2026-05-31`이면 실제 조건은 `approved_at < 2026-06-01 00:00:00 UTC`입니다.

3. **스코프 결정** (81줄): `ctx.service_ids` — SYSTEM_ADMIN이면 `None`(전체), SERVICE_MANAGER이면 담당 서비스 UUID 목록.

4. **선택 서비스 판정** (83-94줄): `service_id` 파라미터가 있으면 UUID 파싱 후, SERVICE_MANAGER인 경우 스코프에 포함되는지 검사합니다. 담당하지 않는 서비스 ID를 지정하면 403 대신 **404**를 반환합니다(서비스 존재 여부 미노출).

### 4-2. 전체 모드 (service_id 미지정)

`app/admin/routes/settlement.py:116-157`

```
요청
 └─ _settlement_context()          # 기간·스코프·selected 판정
 └─ settlement_summary()           # 스코프 내 서비스별 집계
 └─ sub_total / one_off_total 계산 # 파이썬에서 rows 순회 합산
 └─ build_service_options()        # 서비스 드롭다운 옵션 빌드
 └─ plan_name_options()            # 요금제 드롭다운 옵션 빌드
 └─ render("settlement/index.html")
```

- `settlement_summary` 반환값: `(총 건수, 총 금액, List[SettlementRow])` — `app/services/settlement.py:25-58`
- `sub_total = sum(r.sub_amount for r in rows)` 등 4개 소계는 파이썬 레벨에서 집계합니다 (`settlement.py:130-133`).
- 서비스 드롭다운 옵션은 **"전체 서비스" 항목 없음** (`include_all=False`, `settlement.py:144`). 정산 화면은 서비스를 명시적으로 선택해야 건별 보기로 진입할 수 있기 때문입니다.

### 4-3. 서비스별 모드 (service_id 지정)

`app/admin/routes/settlement.py:135-141`

전체 모드와 동일한 흐름에 더해, 선택 서비스의 **결제 건별 페이지네이션 목록**을 추가로 조회합니다:

```python
base = _settlement_payment_query(selected, plan_name, start, end)
count_q = select(func.count()).select_from(base.order_by(None).subquery())
items_q = base.order_by(pp.order_by(_SETTLE_SORT))
pay_page = await paginate(db, items_q, count_q, pp)
```

`pay_page`가 None이 아니면 템플릿이 건별 테이블을 렌더합니다(`settlement/index.html:86-113`).

### 4-4. `_settlement_payment_query` — 건별 쿼리

`app/admin/routes/settlement.py:35-56`

서비스별 모드에서 사용하는 결제 건별 base 쿼리입니다:

```python
base = (select(Payment, Subscription)
        .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
        .where(Payment.status == PaymentStatus.DONE,
               Payment.service_id == selected.id))
```

- `Subscription`은 `outerjoin`이라 단건 결제(subscription_id=NULL)도 포함됩니다.
- `plan_name` 지정 시: `Subscription → Plan` INNER JOIN 후 이름 필터 (49-51줄). 이 경우 단건 결제는 Plan이 없으므로 자동으로 제외됩니다.

### 4-5. 엑셀 다운로드

`app/admin/routes/settlement.py:160-200`

동일한 `_settlement_context` → 두 가지 출력 형식:

| 모드 | 컬럼 | 파일명 |
|------|------|--------|
| 서비스별 (selected 있음) | 승인시각·사용자·주문번호·유형·종류·상태·총매출·환불·순매출 | `settlement-{서비스명}-{날짜}.xlsx` |
| 전체 (selected 없음) | 서비스·건수·구독매출·일반매출·총매출·환불·순매출 | `settlement-{날짜}.xlsx` |

전체 모드에서는 페이지네이션 없이 `settlement_summary`를 재호출해 전체 행을 씁니다(`settlement.py:195-200`).

엑셀 생성 함수는 `app/admin/export.py:23-41`의 `xlsx_response`이며, 파일명 인코딩은 RFC 5987 방식(`filename*=UTF-8''...`)을 사용합니다.

---

## 5. 집계 로직 상세: `settlement_summary`

`app/services/settlement.py:25-58`

### 정산 대상 조건

정산에 포함되는 결제 행의 조건은 다음 세 가지를 **모두** 만족해야 합니다:

1. `Payment.status IN (DONE, CANCELED)` — 승인 완료 + **취소(환불) 결제 포함** (취소내역도 정산에 반영)
2. `Payment.approved_at` 가 `[start, end)` 범위 — 취소 시각이 아닌 **승인 시각** 기준(취소 건도 원래 승인 시점이 속한 기간에 집계됨)
3. 스코프 내 서비스(`scope is not None`이면 `service_id IN (...)`)

실패(`FAILED`)·대기(`PENDING`) 결제는 제외됩니다. **취소(`CANCELED`)는 포함**되며, 매출은 다음과 같이 분해됩니다:

- **총매출(`amount`)** = 승인된 원금 합(DONE + CANCELED) — 일단 청구된 금액
- **환불(`refund_amount`)** = 돌려준 금액 합(`coalesce(canceled_amount, 0)`) — **전액취소(CANCELED)와 어드민 부분취소(status=DONE이지만 `canceled_amount>0`) 모두 포함**
- **순매출(`net_amount`)** = 총매출 − 환불 = 서비스가 실제 보유하는 금액

> 취소는 단건(ONE_OFF) 결제에만 발생한다. 예) 외부 사용자 취소(10% 수수료): 10,000원 → 총매출 10,000 · 환불 9,000 · 순매출 1,000. 예) 어드민 부분취소: 10,000원 중 3,000원 환불 → status=DONE, 총매출 10,000 · 환불 3,000 · 순매출 7,000.

### SQL 집계 구조 (서비스별 GROUP BY)

```python
q = (select(Service.id, Service.name,
            func.count(Payment.id),          # 전체 건수
            amount_sum,                       # 합계 금액
            sub_sum,                          # 구독 결제 합계 (CASE WHEN kind=SUBSCRIPTION)
            oo_sum,                           # 일반 결제 합계 (CASE WHEN kind=ONE_OFF)
            sub_cnt,                          # 구독 건수
            oo_cnt)                           # 일반 건수
     .select_from(Payment)
     .join(Service, Payment.service_id == Service.id)
     .where(Payment.status.in_((PaymentStatus.DONE, PaymentStatus.CANCELED)))
     .group_by(Service.id, Service.name)
     .order_by(amount_sum.desc(), Service.name))
# refund_sum = SUM(COALESCE(canceled_amount, 0)) — 환불 합계도 함께 집계
```

`CASE WHEN` 패턴으로 단일 쿼리에서 구독/일반을 분리 집계합니다(`settlement.py:31-38`):
```python
sub_sum = func.coalesce(func.sum(case(
    (Payment.kind == PaymentKind.SUBSCRIPTION, Payment.amount), else_=0)), 0)
```

결과는 **금액 내림차순** 정렬(`amount_sum.desc()`)이고, 금액이 같으면 서비스 이름 알파벳순입니다.

### `PaymentKind` 열거형

`app/models/enums.py:111-115`

| 값 | 의미 |
|----|------|
| `SUBSCRIPTION` | 구독에 묶인 정기(자동) 결제 |
| `ONE_OFF` | 구독과 무관한 단건 즉시 결제 |

`Payment.kind` 필드의 기본값은 `SUBSCRIPTION`이며(`payment.py:32`), 단건 결제 생성 시 명시적으로 `ONE_OFF`를 세팅합니다.

### `SettlementRow` 데이터 클래스

`app/services/settlement.py:13-22`

```python
@dataclass
class SettlementRow:
    service_id: uuid.UUID
    service_name: str
    count: int          # 정산 대상 건수 (DONE + CANCELED, 구독 + 일반)
    amount: int         # 총매출(KRW) — 승인 원금 합(DONE + CANCELED)
    sub_amount: int     # 구독 결제 합계(총매출 기준)
    one_off_amount: int # 일반(단건) 결제 합계(총매출 기준)
    sub_count: int = 0  # 구독 결제 건수
    one_off_count: int = 0  # 일반(단건) 결제 건수
    refund_amount: int = 0  # 환불 합계(취소 결제의 canceled_amount 합)

    @property
    def net_amount(self) -> int:   # 순매출 = 총매출 − 환불
        return self.amount - self.refund_amount
```

화면에서 `sub_total`/`one_off_total`은 이 rows를 파이썬 레벨에서 다시 합산합니다(`settlement.py:130-133`).

---

## 6. 사용하는 DB 테이블·컬럼

정산 화면은 **쓰기가 전혀 없는 조회 전용**입니다.

| 테이블 | 읽는 컬럼 | 목적 |
|--------|-----------|------|
| `payments` | `id`, `service_id`, `subscription_id`, `external_user_id`, `order_id`, `payment_type`, `kind`, `amount`, `status`, `approved_at` | 집계 및 건별 목록 |
| `services` | `id`, `name` | 서비스 이름 표시, 드롭다운 옵션 |
| `subscriptions` | `id`, `plan_id`, `service_id`, `external_user_id` | 건별 목록에서 구독 연결(OUTER JOIN) |
| `plans` | `id`, `name`, `service_id` | 요금제 이름 필터, 드롭다운 옵션 |

---

## 7. 예외·엣지 케이스

| 상황 | 동작 |
|------|------|
| 기간 내 결제 없음 | 빈 rows 반환, 템플릿에서 "기간 내 정산 대상이 없습니다" 표시(`index.html:81, 108`) |
| from/to 파라미터 모두 없음 | 당월 1일~오늘을 기본값으로 자동 설정(`settlement.py:76-79`) |
| from만 있고 to 없음 (또는 반대) | 한쪽 방향만 무제한. `date_range`에서 `None`으로 처리(`pagination.py:128-144`) |
| `to` 날짜 경계 | `end`는 **익일 0시**로 올려 반개구간 적용 — `to=2026-05-31`이면 `approved_at < 2026-06-01` |
| SERVICE_MANAGER가 타 서비스 service_id 지정 | **404** 반환(`settlement.py:90-91`). 서비스 존재 여부를 노출하지 않기 위해 403 대신 404 사용 |
| 잘못된 UUID 형식의 service_id | UUID 파싱 실패 시 `service_id` 필터를 조용히 제거 후 전체 모드로 처리(`settlement.py:87-88`) |
| plan_name 필터 + 전체 모드 | `settlement_summary`의 plan_name 파라미터로 전달되어 구독 결제만 집계(`services/settlement.py:46-49`) |
| plan_name 필터 + 서비스별 모드 | `_settlement_payment_query`에서 Subscription→Plan INNER JOIN으로 일반 결제 자동 제외(`settlement.py:49-51`) |
| 대용량 엑셀 export | 페이지네이션 없이 전체 rows를 write-only 워크북으로 처리(`export.py:29-35`). 행 수 제한은 없으나 openpyxl의 write_only 모드를 사용해 메모리 효율을 높임 |
| 엑셀 파일명에 한글/특수문자 | RFC 5987 인코딩(`filename*=UTF-8''...`) + ASCII fallback 병행(`export.py:37-39`) |
| 수식 주입(CSV injection) | `xlsx_safe` 함수로 `=`, `+`, `-`, `@` 시작 문자열에 `'` 프리픽스 추가(`export.py:16-19`) |

---

## 8. 관련 테스트

### e2e 테스트 — `tests/e2e/test_settlement_page.py`

| 테스트 함수 | 검증 항목 |
|-------------|-----------|
| `test_settlement_all_mode_lists_services` | 전체 모드에서 모든 서비스 행 표시, 합계 금액, 상세보기 링크에 기간 파라미터 유지 |
| `test_settlement_service_mode_lists_payments` | 서비스별 모드에서 해당 서비스 결제만 표시, 타 서비스 결제 제외, 구독 상세보기 링크 |
| `test_settlement_default_period_renders` | 파라미터 없으면 이번달 1일~오늘 기본값으로 렌더 |
| `test_settlement_manager_scope` | SERVICE_MANAGER는 담당 서비스만 보임, 타 서비스 service_id 직접 요청 시 404 |
| `test_settlement_nav_menu` | 사이드바 네비에 '정산' 링크 존재 |
| `test_settlement_month_picker_renders` | `type="month"` 입력 필드 렌더, JS 훅 `data-settle-month` 속성 |
| `test_settlement_shows_split_and_oneoff_detail` | 구독/일반 분리 금액 표시, 일반결제 상세보기 → 결제상세 링크, 요금제 select 노출 |
| `test_settlement_all_mode_shows_sub_and_one_off_columns` | 전체 모드 표에 `<th>구독</th>`, `<th>일반</th>` 컬럼 헤더 표시 |
| `test_settlement_service_mode_plan_filter_excludes_oneoff` | 요금제 필터 시 구독 결제만 포함, 일반 결제 제외 |

### 통합 테스트 — `tests/integration/test_settlement.py`

| 테스트 함수 | 검증 항목 |
|-------------|-----------|
| `test_summary_groups_by_service_amount_desc` | 서비스별 GROUP BY, 금액 내림차순 정렬, FAILED/기간 밖 결제 제외 |
| `test_summary_boundary_half_open` | `[start, end)` 반개구간 — end 정각 결제는 제외 |
| `test_summary_scope_limits_services` | scope 파라미터로 특정 서비스만 집계 |
| `test_summary_open_range` | start/end None이면 FAILED만 제외하고 전 기간 집계 |
| `test_settlement_split_counts_and_plan_filter` | `sub_count`/`one_off_count` 분리 집계, 요금제 필터 시 일반결제 제외 |
| `test_settlement_splits_subscription_and_one_off` | `sub_amount`/`one_off_amount` 분리 합계 검증 |

### 단위 테스트 — `tests/unit/test_export.py`

| 테스트 함수 | 검증 항목 |
|-------------|-----------|
| `test_xlsx_safe_guards_formula` | `=`, `+`, `-`, `@` 시작 문자열에 `'` 프리픽스 추가, 숫자·일반 문자열은 그대로 |
| `test_xlsx_response_headers_and_content` | Content-Disposition 헤더, 셀 값, 수식 방어 |
| `test_xlsx_response_korean_filename` | RFC 5987 인코딩 (`filename*=UTF-8''`) |

### 테스트 실행

```bash
# e2e 테스트만 실행
pytest tests/e2e/test_settlement_page.py -v

# 통합 테스트만 실행
pytest tests/integration/test_settlement.py -v

# 엑셀 단위 테스트
pytest tests/unit/test_export.py -v
```

---

## 9. 유지보수 팁

### 정산 기준(approved_at / DONE)을 바꾸고 싶다면

정산 대상 조건은 두 곳에 있습니다. 둘 다 함께 수정해야 합니다:

- **집계 쿼리**: `app/services/settlement.py:43` — `Payment.status == PaymentStatus.DONE`
- **건별 쿼리**: `app/admin/routes/settlement.py:47` — `Payment.status == PaymentStatus.DONE`

approved_at 대신 다른 시각 컬럼을 쓰고 싶으면:
- `pagination.py:128-144`의 `date_range` 함수 (여기선 컬럼 이름 무관, start/end 값만 생성)
- `settlement.py:51-55`의 `.where(Payment.approved_at >= start)` 조건
- `settlement.py:183-184`의 건별 쿼리도 동일하게 수정
- 템플릿 안내 문구(`index.html:7`) 도 함께 수정

### 집계 컬럼을 추가하고 싶다면

1. `app/services/settlement.py:13-22` — `SettlementRow` 데이터 클래스에 필드 추가
2. `app/services/settlement.py:39-45` — SELECT 절에 집계 식 추가
3. `app/services/settlement.py:56-57` — `SettlementRow(...)` 생성자 인수 추가
4. `app/admin/routes/settlement.py:126-133` — 라우트에서 소계 계산 추가 (필요시)
5. `app/admin/templates/settlement/index.html` — 전체 모드 테이블 헤더/행(`64-85줄`) 수정

### 엑셀 포맷을 바꾸고 싶다면

- **컬럼 헤더 순서/이름**: `settlement.py:191, 198-199` — `xlsx_response()` 호출 시 `header` 리스트 수정
- **셀 값 포맷(날짜, 금액 등)**: `settlement.py:187-188` — rows append 부분 수정 (이미 KST 문자열로 변환 완료)
- **엑셀 스타일(열 너비, 색상 등)**: `app/admin/export.py:29-35` — write-only 워크북 생성 부분 수정. 단, write-only 모드에서는 셀에 스타일 적용 시 `WriteOnlyCell`을 별도로 사용해야 합니다

### 요약 UI(`.settle-summary`)를 바꾸고 싶다면

`app/admin/templates/settlement/index.html:41-62`

```html
<div class="settle-summary" style="margin-bottom:16px">
  <div class="settle-total">...</div>   {# 전체 합계(강조) #}
  <table class="settle-breakdown">      {# 구독/일반 분리 소계 #}
```

`.settle-summary` 블록은 **전체 모드/서비스별 모드 모두**에서 항상 표시됩니다. 선택 서비스가 있으면 "X 정산 금액", 없으면 "전체 정산 금액"으로 레이블이 바뀝니다(`index.html:45`).

### 서비스별 모드 건별 테이블 컬럼을 바꾸고 싶다면

- **화면**: `index.html:88-113` — `<thead>` 헤더와 `{% for p, sub in pay_page.items %}` 루프 수정
- **정렬 가능 컬럼**: `settlement.py:32` — `_SETTLE_SORT` 딕셔너리 (현재 `approved_at`, `amount` 두 가지)
- **엑셀**: `settlement.py:186-192` — 서비스별 모드 엑셀 rows append 부분도 함께 수정

### 월 선택 피커(month picker)

`index.html:10-17`에 있는 `<input type="month">` 위젯은 **서버에 값을 전송하지 않습니다** (`name` 속성 없음). JavaScript의 `onchange`로 `from`과 `to` hidden input을 채우고 폼을 자동 제출합니다. 서버는 `from`/`to`만 받습니다.

### 시간대 주의

- DB에는 항상 **UTC**로 저장됩니다 (`payment.py:42`)
- 화면 표시는 **KST** (`index.html:99`에서 `{{ p.approved_at|kst(...) }}` 필터 사용)
- 엑셀 export도 KST로 포맷 (`settlement.py:187`: `kst_format(p.approved_at, ...)`)
- `date_range`는 `YYYY-MM-DD`를 **UTC 0시**로 파싱합니다 (`pagination.py:137`). 즉 `from=2026-05-01`은 `2026-05-01 00:00:00 UTC`이며, KST 기준으로는 `2026-04-30 15:00 KST` 이후 결제부터 포함됩니다. 한국 사용자가 기대하는 "5월 1일 KST 0시"와 9시간 차이가 생길 수 있습니다. 필요시 `date_range` 함수를 KST 기준으로 변경할 수 있습니다.

### 스코프 디버깅

"SERVICE_MANAGER가 봐야 할 서비스가 목록에 안 나온다"는 문의가 오면:
- `app/admin/deps.py:77` — `account_service.effective_service_ids(db, user)` 반환값 확인
- `app/admin/filters.py:13-16` — `service_options` 쿼리에서 `scope`가 정상 전달되는지 확인
- `app/services/settlement.py:54-55` — `scope is not None`이면 `IN (...)` 절이 추가됨
