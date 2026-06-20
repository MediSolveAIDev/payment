# 모든 Admin 리스트 엑셀 다운로드 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 감사로그 외 모든 admin 리스트(서비스·요금제·구독·결제·정산·관리자 + 서비스 상세 3표)에 현재 필터·스코프를 반영한 .xlsx 다운로드를 추가한다.

**Architecture:** 공용 `app/admin/export.py`(`xlsx_safe`, `xlsx_response`)를 만들고 감사로그도 이관. 각 목록 라우트는 base 쿼리를 `_build_*_query(pp, ctx)`로 추출해 목록/export가 공유(필터·정렬 동일, export는 페이지네이션만 생략). 화면 버튼은 공용 `_list.html` toolbar에 `export_url` 옵션 추가.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, openpyxl(write-only), Jinja2, pytest

**스펙:** `docs/superpowers/specs/2026-06-08-list-excel-export-design.md`
**테스트:** `uv run pytest <경로> -q`

## 파일 구조
- `app/admin/export.py` — 공용 유틸(신설)
- `app/admin/routes/audit.py` — 공용 유틸로 이관
- `app/admin/templates/_list.html` — toolbar `export_url` 옵션
- `app/admin/routes/{services,plans,subscriptions,users,settlement}.py` — `_build_*_query` 추출 + export 엔드포인트
- 각 목록 템플릿 — toolbar에 export_url 전달; 정산/서비스상세는 버튼 직접 추가
- 테스트: `tests/unit/test_export.py`(신설), `tests/e2e/test_list_export.py`(신설)

공용 셀 규칙: 시각은 `kst_format(value, "%Y-%m-%d %H:%M")`(만료일은 `"%Y-%m-%d"`), 금액은 정수, None/빈값은 `"-"`, 종류 코드 `SUBSCRIPTION→"구독"/ONE_OFF→"일반"`.

---

### Task 1: 공용 export 유틸 + 감사로그 이관

**Files:**
- Create: `app/admin/export.py`
- Modify: `app/admin/routes/audit.py`
- Test: `tests/unit/test_export.py`

- [ ] **Step 1: 단위 테스트 작성** — `tests/unit/test_export.py`:
```python
from io import BytesIO

from openpyxl import load_workbook

from app.admin.export import XLSX_MEDIA, xlsx_response, xlsx_safe


def test_xlsx_safe_guards_formula():
    assert xlsx_safe("=SUM(A1)") == "'=SUM(A1)"
    assert xlsx_safe("정상") == "정상"
    assert xlsx_safe(1000) == 1000           # 숫자는 그대로


def test_xlsx_response_headers_and_content():
    resp = xlsx_response("services", ["이름", "값"], [["x", 1], ["=y", 2]])
    assert resp.media_type == XLSX_MEDIA
    cd = resp.headers["content-disposition"]
    assert "attachment" in cd and "services-" in cd and cd.endswith('.xlsx"')
    wb = load_workbook(BytesIO(resp.body))
    ws = wb.active
    assert [c.value for c in ws[1]] == ["이름", "값"]
    assert ws[3][0].value == "'=y"           # 수식 방어
    assert ws[2][1].value == 1
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/unit/test_export.py -x -q` → ModuleNotFoundError.

- [ ] **Step 3: 유틸 구현** — `app/admin/export.py`:
```python
"""리스트 엑셀(.xlsx) 다운로드 공용 유틸."""
from collections.abc import Iterable
from io import BytesIO

from fastapi.responses import Response
from openpyxl import Workbook

from app.core.clock import kst_format, utcnow

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx_safe(value):
    """수식 주입 방어 — '='로 시작하는 문자열 셀에 ' 프리픽스."""
    return f"'{value}" if isinstance(value, str) and value.startswith("=") else value


def xlsx_response(filename_prefix: str, header: list[str],
                  rows: Iterable[list], *, sheet_title: str = "Sheet1") -> Response:
    """헤더 + 행들을 write-only 워크북으로 만들어 첨부 다운로드 응답 생성.

    rows의 각 셀은 호출측이 표시용으로 포맷(시각=KST 문자열, 금액=정수).
    파일명: {prefix}-{YYYYmmdd-HHMM(KST)}.xlsx"""
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(sheet_title)
    ws.append(list(header))
    for row in rows:
        ws.append([xlsx_safe(c) for c in row])
    buf = BytesIO()
    wb.save(buf)
    filename = f"{filename_prefix}-{kst_format(utcnow(), '%Y%m%d-%H%M')}.xlsx"
    return Response(buf.getvalue(), media_type=XLSX_MEDIA,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
```

- [ ] **Step 4: 단위 통과 확인** — Run: `uv run pytest tests/unit/test_export.py -q` → PASS.

- [ ] **Step 5: 감사로그 이관** — `app/admin/routes/audit.py`
  `_XLSX_MEDIA`/`_xlsx_safe` 정의와 `audit_export`의 워크북 생성부를 공용 유틸 사용으로 교체.
  import 추가: `from app.admin.export import xlsx_response`. `audit_export` 본문을:
```python
@router.get("/audit/export.xlsx")
async def audit_export(request: Request, ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    """현재 필터/검색이 적용된 감사로그 전체를 xlsx로 다운로드 (페이지네이션 무시)."""
    pp = PageParams.from_request(request, sortable=set(_AUDIT_SORT),
                                 default_sort="created_at",
                                 filter_keys=("actor_type", "action"))
    items_q = _build_audit_query(pp).order_by(pp.order_by(_AUDIT_SORT))
    logs = list((await db.scalars(items_q)).all())
    rows = await _build_rows(db, logs)
    out = []
    for r in rows:
        actor = (f"외부 서비스 ({r['actor_service_name']})"
                 if r["actor_service_name"] else r["actor"])
        out.append([kst_format(r["time"], "%Y-%m-%d %H:%M:%S"), actor,
                    r["action"], r["target"], r["detail"], r["ip"]])
    return xlsx_response("audit-log", ["시각", "행위자", "활동", "대상", "상세", "IP"],
                         out, sheet_title="감사로그")
```
  이후 사용되지 않는 `_XLSX_MEDIA`, `_xlsx_safe`, `from io import BytesIO`, `from openpyxl import Workbook`는 제거(다른 곳에서 안 쓰면). `kst_format` import 추가(`from app.core.clock import kst_format, utcnow`).
  주의: 기존 감사 export는 시각을 UTC로 출력했으나 화면(`|kst`)과 맞춰 **KST로 통일**된다.

- [ ] **Step 6: 감사 회귀 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py -k audit -q` → PASS.
  (시각 tz를 단언하는 테스트가 있으면 KST 기준으로 갱신.)

- [ ] **Step 7: 커밋**
```bash
git add app/admin/export.py app/admin/routes/audit.py tests/unit/test_export.py
git commit -m "feat(admin): 엑셀 export 공용 유틸 추가 + 감사로그 이관

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: toolbar에 export 버튼 옵션

**Files:**
- Modify: `app/admin/templates/_list.html`
- Modify: `app/admin/templates/audit/_table.html` (기존 버튼을 toolbar 옵션으로 통일)
- Test: 기존 audit 버튼 e2e로 검증

- [ ] **Step 1: toolbar 매크로에 export_url 추가** — `_list.html` `toolbar` 시그니처와 본문:
```jinja
{%- macro toolbar(action, pp, placeholder='검색', extra_selects=None, target=None, date_inputs=None, export_url=None) -%}
```
  검색 버튼(`<button class="btn btn-sub" type="submit">검색</button>`) 바로 다음에 추가:
```jinja
  {%- if export_url -%}
    <a class="btn btn-sm btn-ghost" href="{{ export_url }}{% if pp.query_without('page') %}?{{ pp.query_without('page') }}{% endif %}">
      <span data-lucide="download"></span>엑셀</a>
  {%- endif -%}
```

- [ ] **Step 2: 감사로그 버튼 통일** — `audit/_table.html`에서 기존 export `<a>` 버튼을 제거하고, 그 화면의 `L.toolbar(...)` 호출에 `export_url='/admin/audit/export.xlsx'` 인자를 추가. (toolbar를 쓰지 않고 별도 버튼만 있었다면, 버튼 href는 유지하되 `pp.query_without('page')` 형식으로 정렬.)

- [ ] **Step 3: 확인** — Run: `uv run pytest tests/e2e/test_admin_operations.py -k "audit_page_has_export" -q` → PASS.
  (필요 시 테스트의 버튼 탐색 문자열을 새 마크업에 맞게 갱신 — 여전히 `/admin/audit/export.xlsx` 링크 존재.)

- [ ] **Step 4: 커밋**
```bash
git add app/admin/templates/_list.html app/admin/templates/audit/_table.html tests/e2e/test_admin_operations.py
git commit -m "feat(admin): 리스트 toolbar에 엑셀 다운로드 버튼 옵션 추가

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 서비스 · 관리자(사용자) export (admin 전용)

**Files:**
- Modify: `app/admin/routes/services.py`, `app/admin/routes/users.py`
- Modify: `app/admin/templates/services/list.html`, `app/admin/templates/users/list.html`
- Test: `tests/e2e/test_list_export.py`(신설)

- [ ] **Step 1: 실패 테스트 작성** — `tests/e2e/test_list_export.py`:
```python
"""모든 리스트 엑셀 다운로드 e2e."""
from io import BytesIO

from openpyxl import load_workbook

from app.admin.export import XLSX_MEDIA
from tests.factories import create_plan, create_service, create_subscription, create_user
from tests.helpers import admin_login


def _wb(resp):
    assert resp.status_code == 200
    assert resp.headers["content-type"] == XLSX_MEDIA
    assert "attachment" in resp.headers["content-disposition"]
    return load_workbook(BytesIO(resp.content)).active


async def test_services_export(client, db, redis_client, cipher):
    await create_service(db, cipher, name="엑셀서비스A")
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    ws = _wb(await client.get("/admin/services/export.xlsx"))
    assert [c.value for c in ws[1]] == ["서비스명", "담당자 이메일", "허용 IP", "상태"]
    names = [row[0].value for row in ws.iter_rows(min_row=2)]
    assert "엑셀서비스A" in names


async def test_users_export(client, db, redis_client, cipher):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    ws = _wb(await client.get("/admin/users/export.xlsx"))
    assert [c.value for c in ws[1]] == ["이메일", "역할", "주 서비스", "상태"]
    assert any(admin.email == row[0].value for row in ws.iter_rows(min_row=2))
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_list_export.py -x -q` → 404.

- [ ] **Step 3: services 쿼리 추출 + export** — `app/admin/routes/services.py`
  `services_list`의 base 구성을 헬퍼로 추출:
```python
def _build_services_query(pp: PageParams):
    base = select(Service)
    if pp.q:
        base = base.where(Service.name.ilike(f"%{pp.q}%")
                          | Service.manager_email.ilike(f"%{pp.q}%"))
    if pp.filters.get("status"):
        base = base.where(Service.status == pp.filters["status"])
    return base
```
  `services_list`에서 base 구성 부분을 `base = _build_services_query(pp)`로 교체(나머지 동일).
  import 추가: `from app.admin.export import xlsx_response`, `from app.core.clock import kst_format`(있으면 생략).
  export 엔드포인트 추가(목록 라우트 근처):
```python
@router.get("/services/export.xlsx")
async def services_export(request: Request, ctx: AdminContext = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)):
    pp = PageParams.from_request(request, sortable=set(_SVC_SORT),
                                 default_sort="created_at", filter_keys=("status",))
    items_q = _build_services_query(pp).order_by(pp.order_by(_SVC_SORT))
    services = list((await db.scalars(items_q)).all())
    rows = [[s.name, s.manager_email or "-",
             ", ".join(s.allowed_ips or []) or "-", s.status] for s in services]
    return xlsx_response("services", ["서비스명", "담당자 이메일", "허용 IP", "상태"],
                         rows, sheet_title="서비스")
```
  (`Service.allowed_ips`가 리스트가 아니면 해당 표시 로직을 그 타입에 맞게 조정 — 모델 확인.)

- [ ] **Step 4: users 쿼리 추출 + export** — `app/admin/routes/users.py`
```python
def _build_users_query(pp: PageParams):
    base = (select(User, Service).outerjoin(Service, User.service_id == Service.id)
            .where(User.status != UserStatus.DELETED))
    if pp.q:
        base = base.where(User.email.ilike(f"%{pp.q}%"))
    if pp.filters.get("role"):
        base = base.where(User.role == pp.filters["role"])
    if pp.filters.get("status"):
        base = base.where(User.status == pp.filters["status"])
    return base
```
  `users_list`에서 base 구성을 `base = _build_users_query(pp)`로 교체. import `from app.admin.export import xlsx_response`. export:
```python
@router.get("/users/export.xlsx")
async def users_export(request: Request, ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    pp = PageParams.from_request(request, sortable=set(_USER_SORT),
                                 default_sort="created_at", filter_keys=("role", "status"))
    items_q = _build_users_query(pp).order_by(pp.order_by(_USER_SORT))
    rows = [[u.email, u.role, (svc.name if svc else "-"), u.status]
            for u, svc in (await db.execute(items_q)).all()]
    return xlsx_response("users", ["이메일", "역할", "주 서비스", "상태"],
                         rows, sheet_title="관리자")
```

- [ ] **Step 5: 버튼** — 두 목록 화면의 `L.toolbar(...)` 호출에 `export_url` 추가:
  - `services/list.html`(또는 `services/_table.html`의 toolbar 호출): `export_url='/admin/services/export.xlsx'`
  - `users/list.html`(또는 `_table`): `export_url='/admin/users/export.xlsx'`

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/e2e/test_list_export.py -q` → 2 PASS. `uv run pytest tests/e2e/test_admin_operations.py -q` 회귀.

- [ ] **Step 7: 커밋**
```bash
git add app/admin/routes/services.py app/admin/routes/users.py app/admin/templates/services/list.html app/admin/templates/users/list.html tests/e2e/test_list_export.py
git commit -m "feat(admin): 서비스·관리자 목록 엑셀 다운로드

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 요금제 export

**Files:**
- Modify: `app/admin/routes/plans.py`, `app/admin/templates/plans/list.html`
- Test: `tests/e2e/test_list_export.py`

- [ ] **Step 1: 실패 테스트 추가** — `test_list_export.py` 끝에:
```python
async def test_plans_export(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="요금제서비스")
    await create_plan(db, svc, name="베이직요금")
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    ws = _wb(await client.get("/admin/plans/export.xlsx"))
    assert [c.value for c in ws[1]] == ["서비스", "요금제", "결제주기", "정가",
                                        "첫 결제", "정기 결제", "상태"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    assert any(r[1] == "베이직요금" for r in rows)
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_list_export.py -k plans -x -q` → 404.

- [ ] **Step 3: plans 쿼리 추출 + export** — `app/admin/routes/plans.py`
  `plans_list`의 base+필터(서비스 옵션 빌드 제외) 구성을 헬퍼로 추출:
```python
def _build_plans_query(pp: PageParams, ctx):
    base = select(Plan, Service).join(Service, Plan.service_id == Service.id)
    if ctx.service_ids is not None:
        base = base.where(Plan.service_id.in_(ctx.service_ids))
    if pp.q:
        base = base.where(Plan.name.ilike(f"%{pp.q}%"))
    if pp.filters.get("status"):
        base = base.where(Plan.status == pp.filters["status"])
    if pp.filters.get("billing_cycle"):
        base = base.where(Plan.billing_cycle == pp.filters["billing_cycle"])
    if pp.filters.get("plan_name"):
        base = base.where(Plan.name == pp.filters["plan_name"])
    sid = pp.filters.get("service_id", "")
    if sid:
        try:
            base = base.where(Plan.service_id == uuid.UUID(sid))
        except ValueError:
            pp.filters.pop("service_id", None)
    return base
```
  `plans_list`에서 해당 base/필터 블록(서비스 필터 포함)을 `base = _build_plans_query(pp, ctx)`로 교체. `service_filter`는 이후에도 옵션 표시에 쓰이므로 `service_filter = pp.filters.get("service_id", "")`로 다시 읽어 사용. import `from app.admin.export import xlsx_response`.
  export:
```python
@router.get("/plans/export.xlsx")
async def plans_export(request: Request, ctx: AdminContext = Depends(require_any),
                       db: AsyncSession = Depends(get_db)):
    pp = PageParams.from_request(request, sortable=set(_PLAN_SORT),
                                 default_sort="created_at",
                                 filter_keys=("status", "service_id", "billing_cycle",
                                              "plan_name"))
    items_q = _build_plans_query(pp, ctx).order_by(pp.order_by(_PLAN_SORT))
    rows = []
    for plan, svc in (await db.execute(items_q)).all():
        cycle = plan.billing_cycle + (f" {plan.cycle_days}일" if plan.cycle_days else "")
        rows.append([svc.name, plan.name, cycle, plan.price,
                     plan_first_amount(plan), plan_recurring_amount(plan), plan.status])
    return xlsx_response("plans", ["서비스", "요금제", "결제주기", "정가",
                                   "첫 결제", "정기 결제", "상태"], rows, sheet_title="요금제")
```
  (`plan_first_amount`/`plan_recurring_amount`는 plans.py에 이미 import됨.)

- [ ] **Step 4: 버튼** — `plans/list.html`(또는 `_table`)의 `L.toolbar(...)`에 `export_url='/admin/plans/export.xlsx'` 추가.

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/e2e/test_list_export.py tests/e2e/test_admin_operations.py -q` → PASS(요금제 목록 회귀 포함).

- [ ] **Step 6: 커밋**
```bash
git add app/admin/routes/plans.py app/admin/templates/plans/list.html tests/e2e/test_list_export.py
git commit -m "feat(admin): 요금제 목록 엑셀 다운로드

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 구독 · 결제이력 export (스코프 적용)

**Files:**
- Modify: `app/admin/routes/subscriptions.py`, `app/admin/templates/subscriptions/list.html`, `app/admin/templates/payments/list.html`
- Test: `tests/e2e/test_list_export.py`

- [ ] **Step 1: 실패 테스트 추가** — `test_list_export.py` 끝에(스코프 격리 포함):
```python
async def test_subscriptions_export(client, db, redis_client, cipher):
    svc, _, _ = await create_service(db, cipher, name="구독서비스")
    plan = await create_plan(db, svc)
    await create_subscription(db, cipher, svc, plan, external_user_id="exp-user")
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    ws = _wb(await client.get("/admin/subscriptions/export.xlsx"))
    assert [c.value for c in ws[1]] == ["서비스", "사용자", "요금제", "상태",
                                        "만료일", "다음 결제"]
    assert any(r[1] == "exp-user" for r in ws.iter_rows(min_row=2, values_only=True))


async def test_payments_export_scoped_to_manager(client, db, redis_client, cipher):
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc_a, _, _ = await create_service(db, cipher, name="결제A")
    svc_b, _, _ = await create_service(db, cipher, name="결제B")
    for svc, oid in [(svc_a, "exp-a"), (svc_b, "exp-b")]:
        db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="u",
                       order_id=oid, amount=1000, payment_type=PaymentType.ONE_OFF,
                       kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                       idempotency_key=oid, requested_at=utcnow(), approved_at=utcnow()))
    await db.commit()
    mgr, pw = await create_user(db, role="SERVICE_MANAGER", service_id=svc_a.id)
    await admin_login(client, mgr.email, pw)
    ws = _wb(await client.get("/admin/payments/export.xlsx"))
    assert [c.value for c in ws[1]] == ["주문번호", "서비스", "종류", "사용자", "유형",
                                        "금액", "상태", "실패코드", "요청시각"]
    orders = [r[0] for r in ws.iter_rows(min_row=2, values_only=True)]
    assert "exp-a" in orders and "exp-b" not in orders   # 매니저 스코프 격리
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_list_export.py -k "subscriptions_export or payments_export" -x -q` → 404.

- [ ] **Step 3: subscriptions 쿼리 추출 + export** — `app/admin/routes/subscriptions.py`
```python
def _build_subscriptions_query(pp: PageParams, ctx):
    base = (select(Subscription, Plan, Service)
            .join(Plan, Subscription.plan_id == Plan.id)
            .join(Service, Subscription.service_id == Service.id))
    scope = _scope(ctx)
    if scope is not None:
        base = base.where(Subscription.service_id.in_(scope))
    if pp.q:
        base = base.where(Subscription.external_user_id.ilike(f"%{pp.q}%"))
    if pp.filters.get("status"):
        base = base.where(Subscription.status == pp.filters["status"])
    sid = pp.filters.get("service_id", "")
    if sid:
        try:
            base = base.where(Subscription.service_id == uuid.UUID(sid))
        except ValueError:
            pp.filters.pop("service_id", None)
    start, end = date_range(pp)
    if start:
        base = base.where(Subscription.created_at >= start)
    if end:
        base = base.where(Subscription.created_at < end)
    return base
```
  `subscriptions_list`의 base/필터 블록을 `base = _build_subscriptions_query(pp, ctx)`로 교체(service_filter는 옵션 표시용으로 `pp.filters.get("service_id","")` 재읽기). import `from app.admin.export import xlsx_response`(파일 상단; 이 라우트 파일에 payments export도 추가하므로 한 번만).
  export:
```python
@router.get("/subscriptions/export.xlsx")
async def subscriptions_export(request: Request, ctx: AdminContext = Depends(require_any),
                               db: AsyncSession = Depends(get_db)):
    pp = PageParams.from_request(request, sortable=set(SUB_SORT),
                                 default_sort="created_at",
                                 filter_keys=("status", "service_id", "from", "to"))
    items_q = _build_subscriptions_query(pp, ctx).order_by(pp.order_by(SUB_SORT))
    rows = [[svc.name, sub.external_user_id, plan.name, sub.status,
             kst_format(sub.current_period_end, "%Y-%m-%d"),
             kst_format(sub.next_billing_at, "%Y-%m-%d %H:%M")]
            for sub, plan, svc in (await db.execute(items_q)).all()]
    return xlsx_response("subscriptions",
                         ["서비스", "사용자", "요금제", "상태", "만료일", "다음 결제"],
                         rows, sheet_title="구독")
```
  `kst_format` import 추가(`from app.core.clock import kst_format`).

- [ ] **Step 4: payments 쿼리 추출 + export** — 같은 파일
```python
def _build_payments_query(pp: PageParams, ctx):
    base = (select(Payment, Subscription, Service)
            .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
            .join(Service, Payment.service_id == Service.id))
    scope = _scope(ctx)
    if scope is not None:
        base = base.where(Payment.service_id.in_(scope))
    if pp.q:
        base = base.where(Payment.order_id.ilike(f"%{pp.q}%")
                          | Payment.external_user_id.ilike(f"%{pp.q}%"))
    if pp.filters.get("status"):
        base = base.where(Payment.status == pp.filters["status"])
    if pp.filters.get("kind"):
        base = base.where(Payment.kind == pp.filters["kind"])
    sid = pp.filters.get("service_id", "")
    if sid:
        try:
            base = base.where(Payment.service_id == uuid.UUID(sid))
        except ValueError:
            pp.filters.pop("service_id", None)
    start, end = date_range(pp)
    if start:
        base = base.where(Payment.requested_at >= start)
    if end:
        base = base.where(Payment.requested_at < end)
    return base
```
  `payments_list`의 base/필터 블록을 `base = _build_payments_query(pp, ctx)`로 교체.
  export:
```python
@router.get("/payments/export.xlsx")
async def payments_export(request: Request, ctx: AdminContext = Depends(require_any),
                          db: AsyncSession = Depends(get_db)):
    pp = PageParams.from_request(request, sortable=set(_PAY_SORT),
                                 default_sort="requested_at",
                                 filter_keys=("status", "from", "to", "kind", "service_id"))
    items_q = _build_payments_query(pp, ctx).order_by(pp.order_by(_PAY_SORT))
    rows = []
    for p, _sub, svc in (await db.execute(items_q)).all():
        kind_ko = "구독" if p.kind == "SUBSCRIPTION" else "일반"
        rows.append([p.order_id, svc.name, kind_ko, p.external_user_id or "-",
                     p.payment_type, p.amount, p.status, p.failure_code or "-",
                     kst_format(p.requested_at, "%Y-%m-%d %H:%M")])
    return xlsx_response("payments",
                         ["주문번호", "서비스", "종류", "사용자", "유형", "금액",
                          "상태", "실패코드", "요청시각"], rows, sheet_title="결제")
```

- [ ] **Step 5: 버튼** — `subscriptions/list.html` toolbar에 `export_url='/admin/subscriptions/export.xlsx'`, `payments/list.html` toolbar에 `export_url='/admin/payments/export.xlsx'` 추가.

- [ ] **Step 6: 통과 확인** — Run: `uv run pytest tests/e2e/test_list_export.py tests/e2e/test_admin_operations.py -q` → PASS(구독·결제 목록·필터 회귀 포함).

- [ ] **Step 7: 커밋**
```bash
git add app/admin/routes/subscriptions.py app/admin/templates/subscriptions/list.html app/admin/templates/payments/list.html tests/e2e/test_list_export.py
git commit -m "feat(admin): 구독·결제이력 엑셀 다운로드(스코프 적용)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 정산 export (모드별)

**Files:**
- Modify: `app/admin/routes/settlement.py`, `app/admin/templates/settlement/index.html`
- Test: `tests/e2e/test_list_export.py`

- [ ] **Step 1: 실패 테스트 추가** — `test_list_export.py` 끝에:
```python
async def test_settlement_export_all_mode(client, db, redis_client, cipher):
    from datetime import datetime, timezone
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc, _, _ = await create_service(db, cipher, name="정산서비스")
    when = datetime(2026, 6, 3, tzinfo=timezone.utc)
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="u",
                   order_id="set-oo", amount=5000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="set-oo", requested_at=when, approved_at=when))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    ws = _wb(await client.get(
        "/admin/settlement/export.xlsx?from=2026-06-01&to=2026-06-30"))
    assert [c.value for c in ws[1]] == ["서비스", "건수", "구독매출", "일반매출", "합계"]
    assert any(r[0] == "정산서비스" for r in ws.iter_rows(min_row=2, values_only=True))


async def test_settlement_export_service_mode(client, db, redis_client, cipher):
    from datetime import datetime, timezone
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc, _, _ = await create_service(db, cipher, name="정산상세")
    when = datetime(2026, 6, 3, tzinfo=timezone.utc)
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="su",
                   order_id="set-detail", amount=7000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="set-detail", requested_at=when, approved_at=when))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    ws = _wb(await client.get(
        f"/admin/settlement/export.xlsx?from=2026-06-01&to=2026-06-30&service_id={svc.id}"))
    assert [c.value for c in ws[1]] == ["승인시각", "사용자", "주문번호", "유형",
                                        "종류", "금액"]
    assert any(r[2] == "set-detail" for r in ws.iter_rows(min_row=2, values_only=True))
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_list_export.py -k settlement -x -q` → 404.

- [ ] **Step 3: export 엔드포인트** — `app/admin/routes/settlement.py`
  import 추가: `from app.admin.export import xlsx_response`, `from app.core.clock import kst_format`(있으면 생략), `from app.models import PaymentKind`(없으면).
  `settlement_view` 아래에 추가(모드 판정·스코프·기간은 목록과 동일 로직 재사용):
```python
@router.get("/settlement/export.xlsx")
async def settlement_export(request: Request, ctx: AdminContext = Depends(require_any),
                            db: AsyncSession = Depends(get_db)):
    pp = PageParams.from_request(request, sortable=set(_SETTLE_SORT),
                                 default_sort="approved_at",
                                 filter_keys=("from", "to", "service_id"))
    if "from" not in request.query_params and "to" not in request.query_params:
        now = utcnow()
        pp.filters["from"] = now.strftime("%Y-%m-01")
        pp.filters["to"] = now.strftime("%Y-%m-%d")
    start, end = date_range(pp)
    scope = ctx.service_ids

    selected: Service | None = None
    raw_sid = pp.filters.get("service_id", "")
    if raw_sid:
        try:
            sid = uuid.UUID(raw_sid)
        except ValueError:
            pp.filters.pop("service_id", None)
        else:
            if scope is not None and sid not in scope:
                raise NotFoundError("서비스를 찾을 수 없습니다")
            selected = await db.get(Service, sid)
            if selected is None:
                raise NotFoundError("서비스를 찾을 수 없습니다")

    if selected:   # 서비스별 모드 — 결제 건별
        base = (select(Payment, Subscription)
                .outerjoin(Subscription, Payment.subscription_id == Subscription.id)
                .where(Payment.status == PaymentStatus.DONE,
                       Payment.service_id == selected.id))
        if start:
            base = base.where(Payment.approved_at >= start)
        if end:
            base = base.where(Payment.approved_at < end)
        rows = []
        for p, _sub in (await db.execute(base.order_by(pp.order_by(_SETTLE_SORT)))).all():
            kind_ko = "구독" if p.kind == "SUBSCRIPTION" else "일반"
            rows.append([kst_format(p.approved_at, "%Y-%m-%d %H:%M"),
                         p.external_user_id or "-", p.order_id, p.payment_type,
                         kind_ko, p.amount])
        return xlsx_response(f"settlement-{selected.name}",
                             ["승인시각", "사용자", "주문번호", "유형", "종류", "금액"],
                             rows, sheet_title="정산")

    # 전체 모드 — 서비스별 합계
    _c, _a, summary = await settlement_summary(db, scope, start, end)
    rows = [[r.service_name, r.count, r.sub_amount, r.one_off_amount, r.amount]
            for r in summary]
    return xlsx_response("settlement",
                         ["서비스", "건수", "구독매출", "일반매출", "합계"],
                         rows, sheet_title="정산")
```
  (`settlement_summary`가 반환하는 행 객체 속성명이 `service_name/count/sub_amount/one_off_amount/amount`인지 확인하고 맞춰라.)

- [ ] **Step 4: 버튼** — `settlement/index.html` 상단(검색 폼 근처)에 현재 모드/필터를 유지한 다운로드 버튼 추가:
```jinja
<a class="btn btn-sm btn-ghost"
   href="/admin/settlement/export.xlsx?from={{ from_filter }}&to={{ to_filter }}{% if selected %}&service_id={{ selected.id }}{% endif %}">
  <span data-lucide="download"></span>엑셀</a>
```

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/e2e/test_list_export.py tests/e2e/test_settlement_page.py -q` → PASS.

- [ ] **Step 6: 커밋**
```bash
git add app/admin/routes/settlement.py app/admin/templates/settlement/index.html tests/e2e/test_list_export.py
git commit -m "feat(admin): 정산 엑셀 다운로드(전체/서비스별 모드)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 서비스 상세 내 표 3개 export

**Files:**
- Modify: `app/admin/routes/services.py`
- Modify: `app/admin/templates/services/_subs_table.html`, `_oneoff_table.html`, `_plans_table.html`
- Test: `tests/e2e/test_list_export.py`

- [ ] **Step 1: 실패 테스트 추가** — `test_list_export.py` 끝에:
```python
async def test_service_detail_exports(client, db, redis_client, cipher):
    from app.core.clock import utcnow
    from app.models import Payment, PaymentKind, PaymentStatus, PaymentType
    svc, _, _ = await create_service(db, cipher, name="상세서비스")
    plan = await create_plan(db, svc, name="상세요금")
    await create_subscription(db, cipher, svc, plan, external_user_id="d-sub")
    db.add(Payment(subscription_id=None, service_id=svc.id, external_user_id="d-oo",
                   order_id="d-oo-1", amount=3000, payment_type=PaymentType.ONE_OFF,
                   kind=PaymentKind.ONE_OFF, status=PaymentStatus.DONE,
                   idempotency_key="d-oo-1", requested_at=utcnow(), approved_at=utcnow()))
    await db.commit()
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    subs = _wb(await client.get(f"/admin/services/{svc.id}/subs.xlsx"))
    assert [c.value for c in subs[1]] == ["사용자", "요금제", "상태", "만료일", "다음 결제"]
    assert any(r[0] == "d-sub" for r in subs.iter_rows(min_row=2, values_only=True))
    oneoff = _wb(await client.get(f"/admin/services/{svc.id}/oneoff.xlsx"))
    assert [c.value for c in oneoff[1]] == ["승인시각", "사용자", "주문번호", "금액", "상태"]
    assert any(r[2] == "d-oo-1" for r in oneoff.iter_rows(min_row=2, values_only=True))
    plans = _wb(await client.get(f"/admin/services/{svc.id}/plans.xlsx"))
    assert [c.value for c in plans[1]] == ["요금제", "결제주기", "정가", "첫 결제",
                                           "정기 결제", "상태"]
    assert any(r[0] == "상세요금" for r in plans.iter_rows(min_row=2, values_only=True))
```

- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/e2e/test_list_export.py -k service_detail_exports -x -q` → 404.

- [ ] **Step 3: export 엔드포인트 3개** — `app/admin/routes/services.py`(상세 라우트와 동일 `require_admin`):
```python
@router.get("/services/{service_id}/subs.xlsx")
async def service_subs_export(service_id: uuid.UUID, request: Request,
                              ctx: AdminContext = Depends(require_admin),
                              db: AsyncSession = Depends(get_db)):
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    spp = PageParams.from_request(request, sortable=set(SUB_SORT),
                                  default_sort="created_at", filter_keys=("status",))
    base = (select(Subscription, Plan).join(Plan, Subscription.plan_id == Plan.id)
            .where(Subscription.service_id == service_id))
    if spp.q:
        base = base.where(Subscription.external_user_id.ilike(f"%{spp.q}%"))
    if spp.filters.get("status"):
        base = base.where(Subscription.status == spp.filters["status"])
    rows = [[sub.external_user_id, plan.name, sub.status,
             kst_format(sub.current_period_end, "%Y-%m-%d"),
             kst_format(sub.next_billing_at, "%Y-%m-%d %H:%M")]
            for sub, plan in (await db.execute(base.order_by(spp.order_by(SUB_SORT)))).all()]
    return xlsx_response(f"{service.name}-subs",
                         ["사용자", "요금제", "상태", "만료일", "다음 결제"],
                         rows, sheet_title="구독")


@router.get("/services/{service_id}/oneoff.xlsx")
async def service_oneoff_export(service_id: uuid.UUID, request: Request,
                                ctx: AdminContext = Depends(require_admin),
                                db: AsyncSession = Depends(get_db)):
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    base = (select(Payment).where(Payment.service_id == service_id,
                                  Payment.kind == PaymentKind.ONE_OFF)
            .order_by(Payment.requested_at.desc()))
    rows = [[kst_format(p.approved_at, "%Y-%m-%d %H:%M") if p.approved_at else "-",
             p.external_user_id or "-", p.order_id, p.amount, p.status]
            for p in (await db.scalars(base)).all()]
    return xlsx_response(f"{service.name}-oneoff",
                         ["승인시각", "사용자", "주문번호", "금액", "상태"],
                         rows, sheet_title="일반결제")


@router.get("/services/{service_id}/plans.xlsx")
async def service_plans_export(service_id: uuid.UUID, request: Request,
                               ctx: AdminContext = Depends(require_admin),
                               db: AsyncSession = Depends(get_db)):
    service = await db.get(Service, service_id)
    if service is None:
        raise NotFoundError("서비스를 찾을 수 없습니다")
    plans = (await db.scalars(select(Plan).where(Plan.service_id == service_id)
                              .order_by(Plan.created_at))).all()
    rows = []
    for plan in plans:
        cycle = plan.billing_cycle + (f" {plan.cycle_days}일" if plan.cycle_days else "")
        rows.append([plan.name, cycle, plan.price, plan_first_amount(plan),
                     plan_recurring_amount(plan), plan.status])
    return xlsx_response(f"{service.name}-plans",
                         ["요금제", "결제주기", "정가", "첫 결제", "정기 결제", "상태"],
                         rows, sheet_title="요금제")
```
  import 확인: `Payment`, `PaymentKind`, `Subscription`, `Plan`, `kst_format`, `xlsx_response`, `NotFoundError`(services_detail가 이미 사용). 라우트 충돌 주의: `/services/{service_id}/subs.xlsx`는 `/services/{service_id}`(detail)와 구분되는 별도 경로라 충돌 없음.

- [ ] **Step 4: 버튼** — 각 partial 헤더(block-head)에 다운로드 버튼 추가(현재 필터 유지):
  - `_subs_table.html`: `<a class="btn btn-sm btn-ghost" href="/admin/services/{{ service.id }}/subs.xlsx{% if spp.query_without('page') %}?{{ spp.query_without('page') }}{% endif %}"><span data-lucide="download"></span>엑셀</a>`
  - `_oneoff_table.html`: `href="/admin/services/{{ service.id }}/oneoff.xlsx"`
  - `_plans_table.html`: `href="/admin/services/{{ service.id }}/plans.xlsx"`
  (block-head의 `<h2>` 옆에 배치.)

- [ ] **Step 5: 통과 확인** — Run: `uv run pytest tests/e2e/test_list_export.py tests/e2e/test_service_detail_page.py -q` → PASS.

- [ ] **Step 6: 커밋**
```bash
git add app/admin/routes/services.py app/admin/templates/services/_subs_table.html app/admin/templates/services/_oneoff_table.html app/admin/templates/services/_plans_table.html tests/e2e/test_list_export.py
git commit -m "feat(admin): 서비스 상세 구독·일반결제·요금제 엑셀 다운로드

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 전체 검증
- [ ] **Step 1: 전체 테스트** — Run: `uv run pytest -q` → 전체 PASS.
- [ ] **Step 2: 버튼 노출 일괄 확인** — Run: `uv run pytest tests/e2e/test_list_export.py -q` 외, 각 목록 화면에 export 링크가 보이는지 스폿 확인용 테스트가 있으면 통과. 누락 시 해당 목록 화면 응답에 `export.xlsx` 링크 존재 단언 추가.
- [ ] **Step 3: 잔여 확인**
  - `grep -rn "Workbook(" app/admin` → `export.py` 1곳만(라우트에서 직접 openpyxl 사용 0건).
  - `grep -rn "export.xlsx\|/subs.xlsx\|/oneoff.xlsx\|/plans.xlsx" app/admin/templates` → 9개 리스트 버튼 존재.
- [ ] **Step 4: 커밋(잔여 정리 시)**
```bash
git add -A app tests
git commit -m "test: 엑셀 다운로드 잔여 정리"
```

## 변경하지 않는 것 (스펙 동일)
- 목록 화면의 데이터/필터 로직(쿼리 빌더 추출은 동작 동일).
- 도메인/모델/마이그레이션. 외부 API.
