# 서비스 화면 개선 (요청 004) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 요금제 금액정보(첫/정기 결제액) 표시, 허용 IP 라인단위 입력, 서비스 상세 레이아웃 재배치, 서비스 상세에 구독 리스트(필터+페이징) 추가.

**Architecture:** 표시용 금액은 기존 `billing_math.plan_first_amount`/`plan_recurring_amount`를 라우트에서 부여(모델 무변경). 폼 미리보기는 동일 계산식을 미러한 인라인 JS(표시 전용 — 결제액은 항상 서버 계산). 구독 리스트는 기존 `PageParams`/`paginate`/`_list.html` 매크로를 base path만 바꿔 재사용.

**Tech Stack:** FastAPI, Jinja2, vanilla JS, pytest

**스펙:** `docs/superpowers/specs/2026-06-07-service-detail-improvements-design.md`

참고 사실 (구현자가 알아야 할 기존 코드):
- `app/services/billing_math.py`에 `plan_recurring_amount(plan) -> int`(상시 할인가), `plan_first_amount(plan) -> int`(첫 결제액 = 상시 할인가에 첫구독 할인 중첩, FREE→0) 존재. 퍼센트 할인은 `price - (price * v) // 100` (내림).
- `app/admin/templates/_list.html`에 `toolbar(action, pp, placeholder, extra_selects)`, `sort_th(pp, action, col, label)`, `pager(page, action, pp)` 매크로 존재.
- `app/admin/pagination.py`의 `PageParams.from_request(request, sortable=..., default_sort=..., filter_keys=...)` + `paginate(db, items_q, count_q, pp)`.
- 테스트 픽스처: `tests/factories.py`의 `create_service(db, cipher, name=...)` → `(Service, api_key, hmac_secret)`, `create_plan(db, service)`, `create_subscription(db, cipher, service, plan, external_user_id=..., status=...)`, `create_user`. `tests/helpers.py`의 `admin_login`, `get_csrf`.
- pytest 실행: 프로젝트 루트에서 `pytest` (필요 시 `.venv/bin/pytest`).

---

### Task 1: 허용 IP 라인단위 입력

**Files:**
- Modify: `app/admin/routes/services.py:27-28` (`_parse_ips`)
- Modify: `app/admin/templates/services/new.html` (IP input → textarea)
- Modify: `app/admin/templates/services/detail.html:18-25` (IP input → textarea)
- Test: `tests/unit/test_admin_helpers.py` (새 파일), `tests/e2e/test_service_detail_page.py` (새 파일)

- [ ] **Step 1: 실패하는 단위 테스트 작성** — `tests/unit/test_admin_helpers.py` 생성:

```python
from app.admin.routes.services import _parse_ips


def test_parse_ips_newline_separated():
    assert _parse_ips("10.0.0.1\n10.0.0.2") == ["10.0.0.1", "10.0.0.2"]


def test_parse_ips_comma_separated_backward_compat():
    assert _parse_ips("10.0.0.1, 10.0.0.2") == ["10.0.0.1", "10.0.0.2"]


def test_parse_ips_mixed_and_blank_lines():
    assert _parse_ips("10.0.0.1\n\n 10.0.0.2 ,10.0.0.3\n") == [
        "10.0.0.1", "10.0.0.2", "10.0.0.3"]


def test_parse_ips_empty():
    assert _parse_ips("") == []
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/unit/test_admin_helpers.py -v`
Expected: `test_parse_ips_newline_separated`, `test_parse_ips_mixed_and_blank_lines` FAIL (줄바꿈이 구분자가 아니라 IP 문자열에 포함됨)

- [ ] **Step 3: `_parse_ips` 구현** — `app/admin/routes/services.py`:

```python
def _parse_ips(raw: str) -> list[str]:
    """줄바꿈/콤마 구분 IP 목록 파싱(라인단위 입력 + 기존 콤마 호환)."""
    return [ip.strip() for chunk in raw.splitlines()
            for ip in chunk.split(",") if ip.strip()]
```

- [ ] **Step 4: 단위 테스트 통과 확인**

Run: `pytest tests/unit/test_admin_helpers.py -v`
Expected: 4개 전부 PASS

- [ ] **Step 5: 실패하는 e2e 테스트 작성** — `tests/e2e/test_service_detail_page.py` 생성:

```python
"""서비스 상세 화면 개선(요청 004) e2e."""
from sqlalchemy import select

from app.models import Service
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login, get_csrf


async def _admin(client, db, redis_client):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    return await get_csrf(redis_client, sid)


async def test_update_ips_newline_separated(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="ips-newline-svc")
    csrf = await _admin(client, db, redis_client)
    resp = await client.post(f"/admin/services/{svc.id}/ips",
                             data={"csrf_token": csrf,
                                   "allowed_ips": "10.1.1.1\n10.1.1.2"})
    assert resp.status_code == 303
    svc = await db.scalar(select(Service).where(Service.id == svc.id))
    await db.refresh(svc)
    assert svc.allowed_ips == ["10.1.1.1", "10.1.1.2"]


async def test_detail_page_renders_ips_textarea(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="ips-ta-svc",
                                     allowed_ips=["10.2.2.1", "10.2.2.2"])
    await _admin(client, db, redis_client)
    resp = await client.get(f"/admin/services/{svc.id}")
    assert "<textarea" in resp.text
    assert "10.2.2.1\n10.2.2.2" in resp.text
```

- [ ] **Step 6: e2e 실패 확인**

Run: `pytest tests/e2e/test_service_detail_page.py -v`
Expected: `test_update_ips_newline_separated`는 PASS(이미 Step 3 구현), `test_detail_page_renders_ips_textarea` FAIL (textarea 없음)

- [ ] **Step 7: 템플릿 변경**

`app/admin/templates/services/detail.html`의 허용 IP 카드에서:

```html
      <input name="allowed_ips" value="{{ service.allowed_ips | join(', ') }}">
```

을:

```html
      <textarea name="allowed_ips" rows="4" placeholder="10.0.0.1&#10;10.0.0.2"
                style="font-family:inherit">{{ service.allowed_ips | join('\n') }}</textarea>
```

`app/admin/templates/services/new.html`에서:

```html
  <label for="allowed_ips">허용 IP (쉼표 구분)</label>
  <input id="allowed_ips" name="allowed_ips" placeholder="10.0.0.1, 10.0.0.2" required>
```

을:

```html
  <label for="allowed_ips">허용 IP (한 줄에 하나씩)</label>
  <textarea id="allowed_ips" name="allowed_ips" rows="4"
            placeholder="10.0.0.1&#10;10.0.0.2" required
            style="font-family:inherit"></textarea>
```

- [ ] **Step 8: 통과 + 회귀 확인**

Run: `pytest tests/e2e/test_service_detail_page.py tests/e2e/test_admin_services_plans.py tests/unit/test_admin_helpers.py -v`
Expected: 전부 PASS (기존 콤마 입력 테스트 `test_update_ips`, `test_admin_creates_service...`도 콤마 호환으로 통과)

- [ ] **Step 9: 커밋**

```bash
git add app/admin/routes/services.py app/admin/templates/services/new.html app/admin/templates/services/detail.html tests/unit/test_admin_helpers.py tests/e2e/test_service_detail_page.py
git commit -m "feat(admin): 허용 IP 라인단위 입력(textarea), 콤마 호환 유지"
```

---

### Task 2: 요금제 리스트 2곳 — 첫 결제액 컬럼

**Files:**
- Modify: `app/admin/routes/plans.py:15,74-77` (plans_list — first_amount 부여)
- Modify: `app/admin/routes/services.py` (services_detail — first_amount 부여)
- Modify: `app/admin/templates/plans/list.html` (컬럼 추가)
- Modify: `app/admin/templates/services/detail.html:32-47` (컬럼 추가)
- Test: `tests/e2e/test_service_detail_page.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_service_detail_page.py`에 추가:

```python
async def test_plan_tables_show_first_and_recurring_amounts(client, db, redis_client,
                                                            cipher):
    """정가 10,000 / 상시할인 5% → 정기 9,500 / 첫구독 1,000원 할인 → 첫 결제 8,500."""
    svc, _, _ = await create_service(db, cipher, name="amount-col-svc")
    await create_plan(db, svc, name="amount-plan", price=10000,
                      first_payment_type="DISCOUNT_AMOUNT", first_payment_value=1000,
                      recurring_discount_type="DISCOUNT_PERCENT",
                      recurring_discount_value=5)
    await _admin(client, db, redis_client)
    detail = (await client.get(f"/admin/services/{svc.id}")).text
    assert "첫 결제액" in detail and "8,500" in detail and "9,500" in detail
    plans_page = (await client.get("/admin/plans")).text
    assert "첫 결제액" in plans_page and "8,500" in plans_page and "9,500" in plans_page
```

(`create_plan` 팩토리는 `name`/`price`/`first_payment_*`/`recurring_discount_*` kwargs를 모두 지원함 — factories.py:33-37)

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/e2e/test_service_detail_page.py::test_plan_tables_show_first_and_recurring_amounts -v`
Expected: FAIL — "첫 결제액" 미존재

- [ ] **Step 3: 라우트 구현**

`app/admin/routes/plans.py` — import를 `from app.services.billing_math import plan_first_amount, plan_recurring_amount`로 확장하고 `plans_list`의 표시용 루프를:

```python
    for plan, _svc in page.items:  # 표시용 금액(상시 할인 적용가 + 첫 결제액)
        plan.recurring_amount = plan_recurring_amount(plan)
        plan.first_amount = plan_first_amount(plan)
```

`app/admin/routes/services.py` — import에 `plan_first_amount` 추가(`from app.services.billing_math import plan_first_amount, plan_recurring_amount`), `services_detail`의 루프를:

```python
    for p in plans:  # 표시용 금액(상시 할인 적용가 + 첫 결제액)
        p.recurring_amount = plan_recurring_amount(p)
        p.first_amount = plan_first_amount(p)
```

- [ ] **Step 4: 템플릿 구현**

`app/admin/templates/services/detail.html` 요금제 테이블 — thead를:

```html
    <thead><tr><th>이름</th><th>정가</th><th>첫 결제액</th><th>정기 결제액</th><th>주기(반복회차)</th><th>체험</th><th>첫구독 할인</th><th>상태</th><th></th></tr></thead>
```

tbody의 정가 `<td>` 다음(기존 "실제 결제금액" `<td>` 앞)에 첫 결제액 셀 추가:

```html
        <td {% if plan.first_amount == plan.recurring_amount %}class="muted"{% else %}style="font-weight:600"{% endif %}>{{ "{:,}".format(plan.first_amount) }}원</td>
```

빈 행 colspan을 8→9로 변경.

`app/admin/templates/plans/list.html` — thead의 `<th>실제 결제금액</th>`을:

```html
    <th>첫 결제액</th>
    <th>정기 결제액</th>
```

tbody의 정가 `<td>` 다음에 첫 결제액 셀 추가(동일 코드):

```html
      <td {% if plan.first_amount == plan.recurring_amount %}class="muted"{% else %}style="font-weight:600"{% endif %}>{{ "{:,}".format(plan.first_amount) }}원</td>
```

빈 행 colspan을 8→9로 변경.

- [ ] **Step 5: 통과 + 회귀 확인**

Run: `pytest tests/e2e/test_service_detail_page.py tests/e2e/test_admin_services_plans.py tests/e2e/test_service_plans.py -v`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add app/admin/routes/plans.py app/admin/routes/services.py app/admin/templates/plans/list.html app/admin/templates/services/detail.html tests/e2e/test_service_detail_page.py
git commit -m "feat(plans): 요금제 리스트에 첫 결제액 컬럼 추가"
```

---

### Task 3: 요금제 폼 — 실시간 금액 미리보기

**Files:**
- Modify: `app/admin/templates/plans/form.html` (미리보기 박스 + 인라인 JS)
- Test: `tests/e2e/test_service_detail_page.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_service_detail_page.py`에 추가:

```python
async def test_plan_form_has_amount_preview(client, db, redis_client, cipher):
    """금액 미리보기 박스 + 수정 폼에서 저장값 기반 계산 검증(JS 미러의 서버측 앵커)."""
    svc, _, _ = await create_service(db, cipher, name="preview-svc")
    plan = await create_plan(db, svc, name="preview-plan", price=10000)
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc.id)
    await admin_login(client, mgr.email, pw)
    new_form = (await client.get("/admin/plans/new")).text
    assert 'id="amount-preview"' in new_form
    assert 'id="amt-first"' in new_form and 'id="amt-next"' in new_form
    edit_form = (await client.get(f"/admin/plans/{plan.id}/edit")).text
    assert 'id="amount-preview"' in edit_form
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/e2e/test_service_detail_page.py::test_plan_form_has_amount_preview -v`
Expected: FAIL — `amount-preview` 미존재

- [ ] **Step 3: 구현** — `app/admin/templates/plans/form.html`의 `<div class="actions">...저장...</div>` 바로 위에 추가:

```html
  <div id="amount-preview" style="margin-top:18px;padding:12px 14px;border:1px solid var(--border);border-radius:8px;font-size:13px">
    <div class="kv"><span class="muted">첫 결제 금액</span><span id="amt-first" style="font-weight:600">—</span></div>
    <div class="kv"><span class="muted">다음 회차부터</span><span id="amt-next" style="font-weight:600">—</span></div>
  </div>
```

같은 파일 `{% endblock %}` 직전(폼/card 닫힌 뒤)에 인라인 스크립트 추가:

```html
<script>
// 금액 미리보기 — billing_math.py 미러(표시 전용, 실제 결제액은 서버가 계산)
(function () {
  "use strict";
  function num(id) {
    var el = document.getElementById(id);
    if (!el) return null;
    var v = parseInt(el.value, 10);
    return isNaN(v) ? null : v;
  }
  // compute_recurring_amount/compute_first_amount의 할인 공통식
  function applyDiscount(price, type, val) {
    if (type === "DISCOUNT_AMOUNT") return Math.max(0, price - (val || 0));
    if (type === "DISCOUNT_PERCENT") {
      if (val === null || val < 0 || val > 100) return null; // 서버는 거부 — 표시 보류
      return price - Math.floor(price * (val || 0) / 100);
    }
    return price; // NONE
  }
  function fmt(v) { return v === null ? "—" : v.toLocaleString("ko-KR") + "원"; }
  function recalc() {
    var first = document.getElementById("amt-first");
    var next = document.getElementById("amt-next");
    var price = num("price");
    if (price === null || price <= 0) { first.textContent = "—"; next.textContent = "—"; return; }
    var rec = applyDiscount(price, document.getElementById("recurring_discount_type").value,
                            num("recurring_discount_value"));
    var ft = document.getElementById("first_payment_type").value;
    var fa = rec === null ? null : (ft === "FREE" ? 0 : applyDiscount(rec, ft, num("first_payment_value")));
    first.textContent = fmt(fa);
    next.textContent = fmt(rec);
  }
  ["price", "first_payment_type", "first_payment_value",
   "recurring_discount_type", "recurring_discount_value"].forEach(function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", recalc);
    el.addEventListener("change", recalc);
  });
  recalc(); // 수정 폼: 저장된 값으로 초기 표시
})();
</script>
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/e2e/test_service_detail_page.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add app/admin/templates/plans/form.html tests/e2e/test_service_detail_page.py
git commit -m "feat(plans): 요금제 폼에 첫/정기 결제액 실시간 미리보기"
```

---

### Task 4: 서비스 상세 레이아웃 재배치

**Files:**
- Modify: `app/admin/templates/services/detail.html` (구조 재배치 — 라우트 변경 없음)
- Test: `tests/e2e/test_service_detail_page.py`

승인된 레이아웃: 상단 2열 그리드 — 좌: 개요(담당자/요금제/구독 + 상태/키/삭제 버튼 통합), 우: 허용 IP 카드 + 관리자 할당 카드 세로 배치. 그 아래 요금제 관리. (구독 리스트는 Task 5에서 맨 아래 추가.)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_service_detail_page.py`에 추가:

```python
async def test_detail_overview_contains_key_status_buttons(client, db, redis_client,
                                                           cipher):
    """키/상태관리 버튼들이 개요 카드 안에 있고, 별도 '키 / 상태 관리' 카드는 없다."""
    svc, _, _ = await create_service(db, cipher, name="layout-svc")
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "키 / 상태 관리" not in html  # 별도 카드 제거
    # 개요 h2가 버튼들보다 먼저, 요금제 h2보다 버튼들이 먼저 나오는지(개요 영역 포함 여부의 근사 검증)
    assert html.index("개요") < html.index("키 재발급") < html.index("요금제 관리")
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/e2e/test_service_detail_page.py::test_detail_overview_contains_key_status_buttons -v`
Expected: FAIL — "키 / 상태 관리" 카드가 아직 존재하고 버튼이 요금제 테이블 뒤에 있음

- [ ] **Step 3: 템플릿 재배치** — `services/detail.html`의 `{% block content %}` 구조를 다음으로 변경 (page-head/error는 유지, 요금제 테이블 내용과 관리자 할당/허용IP 폼 내용은 기존 마크업 그대로 이동):

```html
<div class="grid grid-2">
  <div class="card">
    <div class="block-head"><h2 style="margin:0">개요</h2></div>
    <div class="kv"><span class="muted">담당자</span><span>{{ service.manager_email }}</span></div>
    <div class="kv"><span class="muted">요금제</span><span>{{ plan_count }}개</span></div>
    <div class="kv"><span class="muted">구독</span><span>{{ sub_count }}건</span></div>
    <div class="actions" style="margin-top:16px">
      <form method="post" action="/admin/services/{{ service.id }}/status">
        <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
        <input type="hidden" name="status" value="{{ 'INACTIVE' if service.status == 'ACTIVE' else 'ACTIVE' }}">
        <button class="btn btn-ghost" type="submit">{{ '비활성화' if service.status == 'ACTIVE' else '활성화' }}</button>
      </form>
      <form method="post" action="/admin/services/{{ service.id }}/rotate-keys"
            data-confirm="기존 키는 즉시 무효화되고 새 키가 발급됩니다."
            data-confirm-title="키를 재발급할까요?" data-confirm-ok="재발급">
        <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
        <button class="btn btn-ghost" type="submit"><span data-lucide="key-round"></span>키 재발급</button>
      </form>
      <form method="post" action="/admin/services/{{ service.id }}/delete"
            data-confirm="구독 이력이 있는 서비스는 삭제할 수 없습니다. 정말 삭제할까요?"
            data-confirm-title="서비스를 삭제할까요?" data-confirm-ok="삭제">
        <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
        <button class="btn btn-danger" type="submit"><span data-lucide="trash-2"></span>삭제</button>
      </form>
    </div>
  </div>
  <div style="display:grid;gap:16px;align-content:start">
    <div class="card">
      <div class="block-head"><h2 style="margin:0">허용 IP</h2></div>
      <!-- 기존 IP 폼(Task 1의 textarea 버전) 그대로 -->
    </div>
    <div class="card">
      <div class="block-head"><h2 style="margin:0">관리자 할당</h2></div>
      <!-- 기존 관리자 할당 마크업 그대로 -->
    </div>
  </div>
</div>
<!-- 요금제 관리 카드: 기존 그대로 -->
```

기존 하단의 "관리자 할당" 카드와 "키 / 상태 관리" 카드는 삭제(내용은 위로 이동).

- [ ] **Step 4: 통과 + 회귀 확인**

Run: `pytest tests/e2e/test_service_detail_page.py tests/e2e -q`
Expected: 전부 PASS (상태/재발급/삭제/관리자할당 폼 action·csrf가 그대로면 기존 기능 테스트 통과)

- [ ] **Step 5: 커밋**

```bash
git add app/admin/templates/services/detail.html tests/e2e/test_service_detail_page.py
git commit -m "feat(admin): 서비스 상세 개요 영역에 키/상태·관리자·허용IP 배치"
```

---

### Task 5: 서비스 상세 — 구독 리스트 (필터+페이징)

**Files:**
- Modify: `app/admin/routes/services.py` (services_detail — 구독 페이징 쿼리)
- Modify: `app/admin/templates/services/detail.html` (하단 구독 카드)
- Test: `tests/e2e/test_service_detail_page.py`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/e2e/test_service_detail_page.py`에 추가:

```python
async def test_detail_subscriptions_list_scoped_and_filtered(client, db, redis_client,
                                                             cipher):
    svc, _, _ = await create_service(db, cipher, name="sublist-svc")
    other, _, _ = await create_service(db, cipher, name="sublist-other")
    plan = await create_plan(db, svc, name="sub-plan")
    other_plan = await create_plan(db, other, name="other-plan")
    await create_subscription(db, cipher, svc, plan, external_user_id="sub-user-a")
    await create_subscription(db, cipher, svc, plan, external_user_id="sub-user-b",
                              status="CANCELED")
    await create_subscription(db, cipher, other, other_plan,
                              external_user_id="other-svc-user")
    await _admin(client, db, redis_client)
    html = (await client.get(f"/admin/services/{svc.id}")).text
    assert "sub-user-a" in html and "sub-user-b" in html
    assert "other-svc-user" not in html  # 타 서비스 구독 미표시
    # status 필터
    html = (await client.get(f"/admin/services/{svc.id}?status=CANCELED")).text
    assert "sub-user-b" in html and "sub-user-a" not in html
    # 사용자 검색
    html = (await client.get(f"/admin/services/{svc.id}?q=user-a")).text
    assert "sub-user-a" in html and "sub-user-b" not in html


async def test_detail_subscriptions_paging(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="subpage-svc")
    plan = await create_plan(db, svc, name="page-plan")
    for i in range(16):  # PER_PAGE_DEFAULT=15 초과 → 2페이지
        await create_subscription(db, cipher, svc, plan,
                                  external_user_id=f"pg-user-{i:02d}")
    await _admin(client, db, redis_client)
    p1 = (await client.get(f"/admin/services/{svc.id}")).text
    assert "총 16건" in p1
    p2 = (await client.get(f"/admin/services/{svc.id}?page=2")).text
    assert p1 != p2  # 페이지 이동으로 다른 행 표시
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/e2e/test_service_detail_page.py -v -k subscriptions`
Expected: FAIL — 구독 사용자 ID가 페이지에 없음

- [ ] **Step 3: 라우트 구현** — `app/admin/routes/services.py`:

파일 상단 import에 추가 (기존 import 블록에 맞춰):

```python
from app.admin.pagination import PageParams, paginate
from app.models import Plan, Subscription  # 기존 모델 import 줄에 병합
```

모듈 상수 추가 (`_PLAN_SORT`류가 없다면 `_parse_ips` 위에):

```python
_DETAIL_SUB_SORT = {
    "external_user_id": Subscription.external_user_id,
    "status": Subscription.status,
    "current_period_end": Subscription.current_period_end,
    "next_billing_at": Subscription.next_billing_at,
    "created_at": Subscription.created_at,
}
```

`services_detail`에 구독 페이징 추가 (기존 sub_count·managers 로직 유지, render 직전에):

```python
    # 하단 구독 리스트(요청 004) — /admin/subscriptions와 동일 패턴, 서비스 고정
    spp = PageParams.from_request(request, sortable=set(_DETAIL_SUB_SORT),
                                  default_sort="created_at",
                                  filter_keys=("status",))
    sub_base = (select(Subscription, Plan)
                .join(Plan, Subscription.plan_id == Plan.id)
                .where(Subscription.service_id == service_id))
    if spp.q:
        sub_base = sub_base.where(Subscription.external_user_id.ilike(f"%{spp.q}%"))
    if spp.filters.get("status"):
        sub_base = sub_base.where(Subscription.status == spp.filters["status"])
    sub_count_q = select(func.count()).select_from(sub_base.order_by(None).subquery())
    sub_items_q = sub_base.order_by(spp.order_by(_DETAIL_SUB_SORT))
    sub_page = await paginate(db, sub_items_q, sub_count_q, spp)
```

render 호출에 전달 인자 추가:

```python
    return render(request, "services/detail.html", ctx=ctx, service=service,
                  plans=plans, plan_count=len(plans), sub_count=sub_count,
                  managers=managers, assignable_managers=assignable,
                  sub_page=sub_page, spp=spp,
                  sub_status_filter=spp.filters.get("status", ""),
                  error=request.query_params.get("error"))
```

- [ ] **Step 4: 템플릿 구현** — `services/detail.html` 최상단에 매크로 import 추가:

```html
{% import "_list.html" as L %}
```

요금제 관리 카드 아래(`{% endblock %}` 직전)에 구독 카드 추가:

```html
<div class="card">
  <div class="block-head"><h2 style="margin:0">구독</h2></div>
  {% set base_path = '/admin/services/' ~ service.id %}
  {{ L.toolbar(base_path, spp, '사용자 ID 검색',
     [('status', [('','전체 상태'),('TRIAL','TRIAL'),('ACTIVE','ACTIVE'),('PAST_DUE','PAST_DUE'),('SUSPENDED','SUSPENDED'),('CANCELED','CANCELED'),('EXPIRED','EXPIRED')], sub_status_filter)]) }}
  <table>
    <thead><tr>
      {{ L.sort_th(spp, base_path, 'external_user_id', '사용자') }}
      <th>요금제</th>
      {{ L.sort_th(spp, base_path, 'status', '상태') }}
      {{ L.sort_th(spp, base_path, 'current_period_end', '만료일') }}
      {{ L.sort_th(spp, base_path, 'next_billing_at', '다음 결제') }}
      <th></th>
    </tr></thead>
    <tbody>
    {% for sub, plan in sub_page.items %}
      <tr onclick="location.href='/admin/subscriptions/{{ sub.id }}'" style="cursor:pointer">
        <td>{{ sub.external_user_id }}</td>
        <td>{{ plan.name }}</td>
        <td><span class="badge badge-{{ sub.status }}">{{ sub.status }}</span></td>
        <td>{{ sub.current_period_end.strftime("%Y-%m-%d") }}</td>
        <td>{{ sub.next_billing_at.strftime("%Y-%m-%d %H:%M") if sub.next_billing_at else '-' }}</td>
        <td><span class="muted" style="font-size:12px">상세 ›</span></td>
      </tr>
    {% else %}
      <tr><td colspan="6" class="muted">구독이 없습니다</td></tr>
    {% endfor %}
    </tbody>
  </table>
  {{ L.pager(sub_page, base_path, spp) }}
</div>
```

- [ ] **Step 5: 통과 확인**

Run: `pytest tests/e2e/test_service_detail_page.py -v`
Expected: 전부 PASS

- [ ] **Step 6: 전체 회귀**

Run: `pytest -q`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add app/admin/routes/services.py app/admin/templates/services/detail.html tests/e2e/test_service_detail_page.py
git commit -m "feat(admin): 서비스 상세에 구독 리스트(필터+페이징) 추가"
```
