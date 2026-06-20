# 12. Admin 리스트 엑셀 다운로드

> 모든 Admin 목록 화면에서 **현재 필터·정렬·검색이 그대로 반영된 .xlsx 파일**을 내려받는 기능.
> 페이지네이션을 무시하고 해당 조건의 전체 데이터를 한 번에 다운로드한다.
>
> 선행: [02-admin-auth.md](02)(스코프·권한), [10-audit.md](10)(감사 export), [00-overview.md](00)(UTC/KST).

---

## 0. 한눈에 보기

| 화면 | export URL | 컬럼 | 권한 |
|---|---|---|---|
| 서비스 목록 | `GET /admin/services/export.xlsx` | 서비스명·담당자 이메일·허용 IP·상태 | `require_admin` |
| 관리자 목록 | `GET /admin/users/export.xlsx` | 이메일·역할·주 서비스·상태 | `require_admin` |
| 요금제 목록 | `GET /admin/plans/export.xlsx` | 서비스·요금제·결제주기·정가·첫 결제·정기 결제·상태 | `require_any`(스코프) |
| 구독 목록 | `GET /admin/subscriptions/export.xlsx` | 서비스·사용자·요금제·상태·만료일·다음 결제 | `require_any`(스코프) |
| 결제 목록 | `GET /admin/payments/export.xlsx` | 주문번호·서비스·종류·사용자·유형·금액·상태·실패코드·요청시각 | `require_any`(스코프) |
| 정산(전체 모드) | `GET /admin/settlement/export.xlsx` | 서비스·건수·구독매출·일반매출·합계 | `require_any`(스코프) |
| 정산(서비스별 모드) | `GET /admin/settlement/export.xlsx?service_id=…` | 승인시각·사용자·주문번호·유형·종류·금액 | `require_any`(스코프) |
| 감사로그 | `GET /admin/audit/export.xlsx` | 시각·행위자·활동·대상·상세·IP | `require_admin` |
| 서비스 상세 > 구독 | `GET /admin/services/{id}/subs.xlsx` | 사용자·요금제·상태·만료일·다음 결제 | `require_admin` |
| 서비스 상세 > 일반결제 | `GET /admin/services/{id}/oneoff.xlsx` | 승인시각·사용자·주문번호·금액·상태 | `require_admin` |
| 서비스 상세 > 요금제 | `GET /admin/services/{id}/plans.xlsx` | 요금제·결제주기·정가·첫 결제·정기 결제·상태 | `require_admin` |

관련 파일:
- **공용 유틸**: `app/admin/export.py`
- **라우트**: `app/admin/routes/services.py`, `users.py`, `plans.py`, `subscriptions.py`, `settlement.py`, `audit.py`
- **버튼 매크로**: `app/admin/templates/_list.html`
- **서비스 상세 partial**: `app/admin/templates/services/_subs_table.html`, `_oneoff_table.html`, `_plans_table.html`

---

## 1. 공용 유틸 — `app/admin/export.py`

### 1-1. 상수 및 수식 주입 방어 — `XLSX_MEDIA`, `xlsx_safe`

```python
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_FORMULA_PREFIXES = ("=", "+", "-", "@")

def xlsx_safe(value):
    """=, +, -, @ 로 시작하는 문자열 셀에 ' 프리픽스를 붙여 텍스트 강제."""
    if isinstance(value, str) and value[:1] in _FORMULA_PREFIXES:
        return f"'{value}"
    return value
```

엑셀은 `=HYPERLINK(...)`, `+cmd`, `-cmd`, `@SUM(...)` 같은 값을 수식으로 실행한다. 서비스명·주문번호·상세 등 **외부 유래 문자열**이 셀에 들어갈 때 이 함수를 거쳐야 한다. 숫자·None은 그대로 통과.

### 1-2. 응답 생성 — `xlsx_response`

```python
def xlsx_response(filename_prefix: str, header: list[str],
                  rows: Iterable[list], *, sheet_title: str = "Sheet1") -> Response:
```

동작 흐름:
1. `Workbook(write_only=True)` — 행을 스트리밍으로 기록해 메모리 효율(대량 데이터도 안전).
2. `ws.append(list(header))` — 첫 번째 행은 헤더.
3. `rows`를 순회하며 `ws.append([xlsx_safe(c) for c in row])` — 각 셀 수식 방어 적용.
4. `BytesIO`에 저장 후 `buf.getvalue()` 반환.
5. **파일명** `{prefix}-{YYYYmmdd-HHMM(KST)}.xlsx` — `kst_format(utcnow(), '%Y%m%d-%H%M')`.
6. **Content-Disposition** — ASCII 폴백(`?`→`_`) + **RFC 5987** `filename*=UTF-8''...` 방식 한글 파일명 지원.

```python
filename = f"{filename_prefix}-{kst_format(utcnow(), '%Y%m%d-%H%M')}.xlsx"
ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
cd = (f"attachment; filename=\"{ascii_fallback}\"; "
      f"filename*=UTF-8''{quote(filename)}")
```

- **시각 표시 규칙**: 각 행의 날짜/시각 셀은 라우트에서 `kst_format(...)` 문자열로 변환해 넘긴다. 엑셀 셀은 날짜 타입이 아닌 **KST 문자열**로 들어간다.
- `sheet_title` 파라미터로 시트 이름을 한글로 지정(기본 "Sheet1", 실사용에서는 "서비스"/"구독"/"요금제" 등).

---

## 2. 쿼리 공유 — 필터 드리프트 방지

목록 라우트와 export 라우트는 **같은 쿼리 빌더 함수**를 공유한다. 화면에서 보이는 것과 다운로드 파일의 내용이 일치하는 핵심 장치.

| 모듈 | 쿼리 빌더 | 사용처 |
|---|---|---|
| `services.py` | `_build_services_query(pp)` | `services_list` + `services_export` |
| `users.py` | `_build_users_query(pp)` | `users_list` + `users_export` |
| `plans.py` | `_build_plans_query(pp, ctx)` | `plans_list` + `plans_export` |
| `subscriptions.py` | `_build_subscriptions_query(pp, ctx)` | `subscriptions_list` + `subscriptions_export` |
| `subscriptions.py` | `_build_payments_query(pp, ctx)` | `payments_list` + `payments_export` |
| `settlement.py` | `_settlement_context(...)` 공통 헬퍼 | `settlement_view` + `settlement_export` |
| `audit.py` | `_build_audit_query(pp)` | `audit_list` + `audit_export` |

export 라우트는 `paginate()`를 **호출하지 않는다**. 대신 `db.scalars(items_q).all()` 또는 `db.execute(items_q).all()`로 전량을 조회한다.

---

## 3. 각 export 엔드포인트 상세

### 3-1. 서비스 목록 — `GET /admin/services/export.xlsx`

```python
pp = PageParams.from_request(request, filter_keys=("status",))
items_q = _build_services_query(pp).order_by(pp.order_by(_SVC_SORT))
rows = [[s.name, s.manager_email or "-",
         ", ".join(s.allowed_ips or []) or "-", s.status] for s in services]
return xlsx_response("services", ["서비스명", "담당자 이메일", "허용 IP", "상태"], rows, sheet_title="서비스")
```

필터: `status`(ACTIVE/INACTIVE). 검색: 서비스명·담당자 이메일 부분일치.

### 3-2. 관리자 목록 — `GET /admin/users/export.xlsx`

```python
items_q = _build_users_query(pp).order_by(pp.order_by(_USER_SORT))
rows = [[u.email, u.role, (svc.name if svc else "-"), u.status]
        for u, svc in (await db.execute(items_q)).all()]
return xlsx_response("users", ["이메일", "역할", "주 서비스", "상태"], rows, sheet_title="관리자")
```

`_build_users_query`는 `User LEFT JOIN Service`로 주 서비스명을 함께 조회한다. 필터: `role`, `status`.
삭제된(`status=DELETED`) 계정은 목록·export 모두 제외(`where status != DELETED`).

### 3-3. 요금제 목록 — `GET /admin/plans/export.xlsx`

```python
for plan, svc in (await db.execute(items_q)).all():
    cycle = plan.billing_cycle + (f" {plan.cycle_days}일" if plan.cycle_days else "")
    rows.append([svc.name, plan.name, cycle, plan.price,
                 plan_first_amount(plan), plan_recurring_amount(plan), plan.status])
return xlsx_response("plans", ["서비스", "요금제", "결제주기", "정가", "첫 결제", "정기 결제", "상태"], ...)
```

- `plan_first_amount`, `plan_recurring_amount` — `services/billing_math.py`의 계산 함수(문서 03).
- **스코프**: `_build_plans_query`가 `ctx.service_ids`가 있으면 해당 서비스만(`require_any`). SYSTEM_ADMIN은 전체.
- 필터: `status`, `service_id`, `billing_cycle`, `plan_name`.

### 3-4. 구독 목록 — `GET /admin/subscriptions/export.xlsx`

```python
rows = [[svc.name, sub.external_user_id, plan.name, sub.status,
         kst_format(sub.current_period_end, "%Y-%m-%d"),
         kst_format(sub.next_billing_at, "%Y-%m-%d %H:%M")]
        for sub, plan, svc in (await db.execute(items_q)).all()]
return xlsx_response("subscriptions",
                     ["서비스", "사용자", "요금제", "상태", "만료일", "다음 결제"], ...)
```

- `Subscription JOIN Plan JOIN Service` 3-way join. 날짜 셀은 `kst_format`으로 KST 문자열.
- 필터: `status`, `service_id`, `plan_name`, `from`/`to`(생성일 기간).

### 3-5. 결제 목록 — `GET /admin/payments/export.xlsx`

```python
kind_ko = "구독" if p.kind == "SUBSCRIPTION" else "일반"
rows.append([p.order_id, svc.name, kind_ko, p.external_user_id or "-",
             p.payment_type, p.amount, p.status, p.failure_code or "-",
             kst_format(p.requested_at, "%Y-%m-%d %H:%M")])
return xlsx_response("payments",
                     ["주문번호", "서비스", "종류", "사용자", "유형", "금액",
                      "상태", "실패코드", "요청시각"], ...)
```

- `Payment LEFT JOIN Subscription LEFT JOIN Plan JOIN Service` 구조.
- `kind` 값을 한글로 변환("구독"/"일반") 후 셀에 넣는다.
- 필터: `status`, `kind`, `service_id`, `plan_name`, `from`/`to`(요청시각 기간).

### 3-6. 정산 — `GET /admin/settlement/export.xlsx`

정산 export는 **현재 모드**(service_id 유무)에 따라 컬럼과 데이터가 달라진다.

**전체 모드**(service_id 없음) — 서비스별 합계:
```python
_c, _a, summary = await settlement_summary(db, scope, start, end, plan_name=plan_name)
rows = [[r.service_name, r.count, r.sub_amount, r.one_off_amount, r.amount]
        for r in summary]
return xlsx_response("settlement", ["서비스", "건수", "구독매출", "일반매출", "합계"], ...)
```

**서비스별 모드**(service_id 지정) — 결제 건별:
```python
for p, _sub in (await db.execute(base.order_by(...))).all():
    kind_ko = "구독" if p.kind == PaymentKind.SUBSCRIPTION else "일반"
    rows.append([kst_format(p.approved_at, "%Y-%m-%d %H:%M"),
                 p.external_user_id or "-", p.order_id, p.payment_type, kind_ko, p.amount])
return xlsx_response(f"settlement-{selected.name}",
                     ["승인시각", "사용자", "주문번호", "유형", "종류", "금액"], ...)
```

- 파일명 prefix가 전체 모드는 `"settlement"`, 서비스별 모드는 `f"settlement-{서비스명}"`.
- 정산의 **스코프 강제**: `_settlement_context`에서 `scope is not None and sid not in scope`이면 `raise NotFoundError` → 404. 매니저가 담당 외 서비스의 export URL을 직접 호출해도 차단된다.
- 기간 기본값: `from`/`to`가 쿼리스트링에 없으면 이번 달 1일~오늘로 자동 설정.

### 3-7. 감사로그 — `GET /admin/audit/export.xlsx`

`_build_audit_query(pp)`와 `_build_rows(db, logs)`를 목록 라우트와 공유한다. 상세는 [10-audit.md](10-audit.md#4-엑셀-다운로드--get-adminauditexportxlsx-audit_export) 참고.

### 3-8. 서비스 상세 > 구독 — `GET /admin/services/{id}/subs.xlsx`

서비스 상세 페이지의 구독 탭에 있는 엑셀 버튼. 서비스 ID를 경로에 고정하고, 구독 탭의 필터(`status`, `q`)를 쿼리스트링으로 유지한다.

```python
rows = [[sub.external_user_id, plan.name, sub.status,
         kst_format(sub.current_period_end, "%Y-%m-%d"),
         kst_format(sub.next_billing_at, "%Y-%m-%d %H:%M")]
        for sub, plan in (await db.execute(base.order_by(...))).all()]
return xlsx_response(f"{service.name}-subs",
                     ["사용자", "요금제", "상태", "만료일", "다음 결제"], ...)
```

파일명: `{서비스명}-subs-YYYYmmdd-HHMM.xlsx`.

### 3-9. 서비스 상세 > 일반결제 — `GET /admin/services/{id}/oneoff.xlsx`

`Payment.kind == ONE_OFF AND service_id == service_id` 조건으로 조회. 정렬은 `requested_at.desc()` 고정(필터 없음).

```python
rows = [[kst_format(p.approved_at, "%Y-%m-%d %H:%M") if p.approved_at else "-",
         p.external_user_id or "-", p.order_id, p.amount, p.status]
        for p in (await db.scalars(base)).all()]
return xlsx_response(f"{service.name}-oneoff",
                     ["승인시각", "사용자", "주문번호", "금액", "상태"], ...)
```

### 3-10. 서비스 상세 > 요금제 — `GET /admin/services/{id}/plans.xlsx`

요금제 목록(전역 `/admin/plans/export.xlsx`)과 달리 서비스를 고정하고 "서비스" 컬럼이 없다.

```python
cycle = plan.billing_cycle + (f" {plan.cycle_days}일" if plan.cycle_days else "")
rows.append([plan.name, cycle, plan.price, plan_first_amount(plan),
             plan_recurring_amount(plan), plan.status])
return xlsx_response(f"{service.name}-plans",
                     ["요금제", "결제주기", "정가", "첫 결제", "정기 결제", "상태"], ...)
```

---

## 4. UI — export 버튼 표시 방식

### 4-1. `_list.html` toolbar 매크로 — `export_url` 옵션

`_list.html`의 `toolbar` 매크로는 `export_url` 파라미터를 받으면 엑셀 버튼을 렌더한다:

```jinja
{%- if export_url -%}
  <a class="btn btn-sm btn-ghost"
     href="{{ export_url }}{% if pp.query_without('page') %}?{{ pp.query_without('page') }}{% endif %}">
    <span data-lucide="download"></span>엑셀</a>
{%- endif -%}
```

`pp.query_without('page')` — 현재 검색어(`q`)·필터·정렬을 **page 파라미터만 제외하고** 그대로 export URL에 붙인다. 화면에서 보이는 필터 상태 그대로 파일에 담기는 핵심 장치.

이 방식을 쓰는 화면:

```jinja
{# services/_table.html #}
{{ L.toolbar('/admin/services', pp, ..., export_url='/admin/services/export.xlsx') }}

{# plans/_table.html 등 동일 패턴 #}
{{ L.toolbar('/admin/plans', pp, ..., export_url='/admin/plans/export.xlsx') }}
```

### 4-2. 서비스 상세 partial — 직접 버튼

서비스 상세의 3개 섹션(구독·일반결제·요금제)은 toolbar 매크로를 쓰지 않고 `block-head` 안에 직접 링크를 배치한다.

**구독 탭** (`_subs_table.html`):
```jinja
<a class="btn btn-sm btn-ghost"
   href="/admin/services/{{ service.id }}/subs.xlsx{% if spp.query_without('page') %}?{{ spp.query_without('page') }}{% endif %}">
  <span data-lucide="download"></span>엑셀</a>
```
구독 탭의 검색·필터(`spp`)를 그대로 유지한다.

**일반결제 탭** (`_oneoff_table.html`):
```jinja
<a class="btn btn-sm btn-ghost"
   href="/admin/services/{{ service.id }}/oneoff.xlsx">
  <span data-lucide="download"></span>엑셀</a>
```
필터가 없으므로 쿼리스트링 없이 고정 URL.

**요금제 탭** (`_plans_table.html`):
```jinja
<a class="btn btn-sm btn-ghost"
   href="/admin/services/{{ service.id }}/plans.xlsx">
  <span data-lucide="download"></span>엑셀</a>
```

### 4-3. 정산 화면 — 직접 버튼

정산(`settlement/index.html`)은 독자적인 toolbar에 버튼을 배치한다. 현재 기간·서비스·요금제 필터를 직접 조합:

```jinja
<a class="btn btn-sm btn-ghost"
   href="/admin/settlement/export.xlsx?from={{ from_filter }}&to={{ to_filter }}{% if selected %}&service_id={{ selected.id }}{% endif %}{% if plan_filter %}&plan_name={{ plan_filter }}{% endif %}">
  <span data-lucide="download"></span>엑셀</a>
```

---

## 5. 스코프 · 권한 동작

| 역할 | 전역 목록(services/users) | plans/subscriptions/payments/settlement | 서비스 상세 export |
|---|---|---|---|
| `SYSTEM_ADMIN` | 전체 | 전체 | 전체 |
| `SERVICE_MANAGER` | 접근 불가(`require_admin`) | 담당 서비스만 | 접근 가능(담당 외 서비스 상세 자체가 404) |

`require_any` 의존성이 붙은 라우트에서는 `ctx.service_ids`가 None(admin) 또는 담당 서비스 ID 목록(매니저)으로 채워진다. 쿼리 빌더가 `ctx.service_ids`를 받아 `WHERE service_id IN ...`을 추가한다.

정산 export에서 매니저가 담당 외 `service_id`를 직접 요청하면 `_settlement_context`가 404를 발생시킨다.

---

## 6. 테스트

### `tests/unit/test_export.py`

공용 유틸 단위 테스트:

| 테스트 | 검증 |
|---|---|
| `test_xlsx_safe_guards_formula` | `=`·`+`·`-`·`@` 시작 문자열 → `'` 프리픽스. 빈 문자열·한글·숫자는 그대로. |
| `test_xlsx_response_headers_and_content` | `media_type == XLSX_MEDIA`, Content-Disposition 포함, 헤더 행 일치, `=y` 셀이 `'=y`로 저장됨. |
| `test_xlsx_response_korean_filename` | Content-Disposition에 `filename*=UTF-8''` 포함. |

### `tests/e2e/test_list_export.py`

각 export 엔드포인트 e2e:

| 테스트 | 검증 |
|---|---|
| `test_services_export` | 헤더 4컬럼, 생성한 서비스명 포함. |
| `test_users_export` | 헤더 4컬럼, 로그인 계정 이메일 포함. |
| `test_plans_export` | 헤더 7컬럼, 요금제명 포함. |
| `test_subscriptions_export` | 헤더 6컬럼, external_user_id 포함. |
| `test_payments_export_scoped_to_manager` | 매니저는 담당 서비스 결제만 포함(스코프 격리). |
| `test_settlement_export_manager_other_service_404` | 담당 외 service_id → 404. |
| `test_settlement_export_all_mode` | 전체 모드 헤더 5컬럼, 서비스명 포함. |
| `test_settlement_export_service_mode` | 서비스별 모드 헤더 6컬럼, 주문번호 포함. |
| `test_service_detail_exports` | subs/oneoff/plans 3개 엔드포인트 헤더·데이터 검증. |
| `test_service_detail_export_404_for_unknown_service` | 존재하지 않는 서비스 ID → 3 엔드포인트 모두 404. |
| `test_payments_export_reflects_status_filter` | `?status=FAILED` 필터 → 해당 건만 포함. |
| `test_list_pages_show_export_buttons` | 5개 목록 페이지 HTML에 export URL 포함 확인. |

---

## 7. 예외 · 주의

| 상황 | 처리 |
|---|---|
| 수식 주입(`=`, `+`, `-`, `@` 시작 문자열) | `xlsx_safe`가 `'` 프리픽스 추가. 행 전체를 `[xlsx_safe(c) for c in row]`로 처리 |
| 한글 파일명 | RFC 5987 `filename*=UTF-8''` + ASCII 폴백(IE 구버전·일부 환경). `quote(filename)` URL 인코딩 |
| 없는 서비스(상세 export) | `db.get(Service, service_id)` None → `raise NotFoundError` 즉시 404 |
| 매니저의 타 서비스 export | 정산: `_settlement_context`에서 404. 구독/결제/요금제: 쿼리 빌더 `service_ids IN`으로 데이터 0건(빈 파일) |
| 날짜 셀의 시간대 | 라우트에서 `kst_format()` 문자열 변환 후 넣음 → 엑셀 날짜 타입이 아니라 KST 문자열 |
| 대량 데이터 | `Workbook(write_only=True)` + 전체 조회 — 엑셀 스트리밍은 안전하나, DB 결과는 메모리에 전체 로드됨. 수십만 건이면 스트리밍 DB 조회 필요(현재 미도입) |
| `service_id` 필터 UUID 오류 | 각 쿼리 빌더가 `try/except ValueError`로 잘못된 UUID를 무시하고 필터 제거 |

---

## 8. 유지보수 체크리스트

1. **새 목록 화면에 export 추가**: 쿼리 빌더를 목록·export가 공유하는 함수로 추출 → export 라우트에서 같은 빌더에 `db.scalars(...).all()` → `xlsx_response`. 필터를 export에만 빠뜨리는 실수 방지.
2. **컬럼 추가/변경**: 라우트의 `rows` 리스트와 `xlsx_response`의 `header` 리스트를 동시에 수정. 순서 불일치가 가장 흔한 버그.
3. **외부 유래 문자열**: 사용자 입력·서비스 데이터가 셀에 들어갈 때 `xlsx_safe`는 `xlsx_response` 내부에서 자동 적용되므로 개별 호출 불필요. 단, `header`는 제어하는 문자열이라 `xlsx_safe` 불필요.
4. **날짜/시각 셀**: 항상 `kst_format(value, fmt)` 문자열로 변환. `None`이면 `kst_format`이 `"-"` 반환.
5. **파일명 prefix**: 서비스 상세 export는 `f"{service.name}-subs"` 같이 서비스명을 포함해 다운로드 파일을 구분하기 쉽게. 한글 포함 시 RFC 5987로 브라우저 표시됨.
6. **export 버튼 위치**: 공용 toolbar는 `export_url` 파라미터 전달. 서비스 상세·정산은 직접 링크. `pp.query_without('page')` 패턴으로 필터 상태 유지.
7. **스코프 검증**: `require_any` 라우트는 쿼리 빌더가 스코프를 처리하지만, 경로 파라미터(서비스 ID)를 직접 받는 엔드포인트(`service_subs_export` 등)는 해당 서비스가 스코프 내인지 별도 확인이 필요함(현재 `require_admin`이라 SYSTEM_ADMIN만 접근 가능해 문제없음 — 권한 변경 시 주의).
