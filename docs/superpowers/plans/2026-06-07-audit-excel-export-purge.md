# 감사로그 엑셀 다운로드 + 과거 데이터 삭제 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 감사로그 화면에 현재 필터가 적용된 xlsx 다운로드 버튼과 기준일 이전 일괄 삭제 기능을 추가한다.

**Architecture:** `audit.py`의 쿼리/행 구성 로직을 헬퍼 2개로 추출해 목록과 다운로드가 공유. 다운로드는 openpyxl write_only로 메모리 효율적으로 생성해 즉시 응답. 삭제는 UTC 자정 기준 bulk DELETE 후 `audit.purge` 감사 기록 + flash 토스트.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, openpyxl(신규 의존성), Jinja2, pytest

**스펙:** `docs/superpowers/specs/2026-06-07-audit-excel-export-purge-design.md`
**테스트 실행:** `uv run pytest <경로> -q`

---

### Task 1: openpyxl 의존성 + 헬퍼 추출 리팩토링 (행동 불변)

**Files:**
- Modify: `pyproject.toml` (dependencies에 openpyxl 추가)
- Modify: `app/admin/routes/audit.py` (쿼리/행 구성 헬퍼 추출)
- Test: 기존 `tests/e2e/test_admin_operations.py` 통과 유지 (신규 테스트 없음 — 리팩토링)

- [ ] **Step 1: openpyxl 추가**

`pyproject.toml`의 dependencies 배열 끝(`"greenlet>=3.1",` 다음)에 추가:

```toml
    "openpyxl>=3.1",
```

Run: `uv sync` → openpyxl 설치 확인.

- [ ] **Step 2: 헬퍼 추출**

`app/admin/routes/audit.py`에서 `audit_list`의 쿼리 구성과 행 구성을 모듈 레벨 헬퍼로 추출. `_TARGET_TABLE`을 모듈 상수로 올린다.

`_resolve_names` 함수 아래에 추가:

```python
_TARGET_TABLE = {"service": "services", "plan": "plans", "user": "users",
                 "subscription": "subscriptions", "payment": "payments"}


def _build_audit_query(pp: PageParams):
    """목록/엑셀 다운로드가 공유하는 검색·필터 쿼리."""
    base = select(AuditLog)
    if pp.q:
        like = f"%{pp.q}%"
        actor_user_sq = select(User.id).where(User.email.ilike(like))
        actor_svc_sq = select(Service.id).where(Service.name.ilike(like))
        base = base.where(
            AuditLog.actor_user_id.in_(actor_user_sq)
            | AuditLog.actor_service_id.in_(actor_svc_sq)
            | AuditLog.target_id.ilike(like)
            | cast(AuditLog.detail, String).ilike(like))
    if pp.filters.get("actor_type"):
        base = base.where(AuditLog.actor_type == pp.filters["actor_type"])
    if pp.filters.get("action"):
        base = base.where(AuditLog.action == pp.filters["action"])
    return base


async def _build_rows(db: AsyncSession, logs: list[AuditLog]) -> list[dict]:
    """AuditLog 목록 → 화면/엑셀 공용 표시 dict 목록."""
    resolved = await _resolve_names(db, logs)
    rows = []
    for log in logs:
        tname = None
        tid = _as_uuid(log.target_id)
        if log.target_type and tid:
            tname = resolved["targets"].get((_TARGET_TABLE.get(log.target_type), tid))
        rows.append({
            "time": log.created_at,
            "actor": actor_label(log.actor_type, resolved["actors"].get(log.actor_user_id)),
            "actor_service_id": log.actor_service_id,
            "actor_service_name": resolved["actor_services"].get(log.actor_service_id),
            "action": action_label(log.action),
            "target": target_label(log.target_type, tname),
            "detail": detail_summary(log.detail),
            "ip": log.ip_address or "-",
        })
    return rows
```

`audit_list` 본문을 다음으로 교체 (PageParams 파싱과 render_list는 유지):

```python
@router.get("/audit")
async def audit_list(request: Request, ctx: AdminContext = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    pp = PageParams.from_request(request, sortable=set(_AUDIT_SORT),
                                 default_sort="created_at",
                                 filter_keys=("actor_type", "action"))
    base = _build_audit_query(pp)
    count_q = select(func.count()).select_from(base.order_by(None).subquery())
    items_q = base.order_by(pp.order_by(_AUDIT_SORT))
    page = await paginate(db, items_q, count_q, pp)
    logs = [r[0] for r in page.items]
    page.items = await _build_rows(db, logs)
    return render_list(request, "audit/list.html", "audit/_table.html",
                      ctx=ctx, page=page, pp=pp,
                      actor_filter=pp.filters.get("actor_type", ""),
                      action_filter=pp.filters.get("action", ""),
                      action_options=[("", "전체 활동")] + list(ACTION_LABELS.items()))
```

(함수 내부의 기존 `_TARGET_TABLE` 지역 정의는 제거 — 모듈 상수로 대체됨)

- [ ] **Step 3: 기존 테스트로 행동 불변 확인**

Run: `uv run pytest tests/e2e/test_admin_operations.py tests/e2e/test_htmx_partials.py -q`
Expected: 전체 PASS (리팩토링이므로 기존 테스트가 그대로 통과해야 함)

- [ ] **Step 4: 커밋**

```bash
git add pyproject.toml uv.lock app/admin/routes/audit.py
git commit -m "refactor(audit): 목록 쿼리/행 구성 헬퍼 추출 + openpyxl 의존성"
```

---

### Task 2: 엑셀 다운로드 라우트 + 버튼

**Files:**
- Modify: `app/admin/routes/audit.py` (export 라우트)
- Modify: `app/admin/templates/audit/_table.html` (다운로드 버튼)
- Test: `tests/e2e/test_admin_operations.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/e2e/test_admin_operations.py` 끝에 추가 (파일에 `create_service`, `create_user`, `admin_login` import와 `_seed_audit_rows` 헬퍼가 이미 있음):

```python
async def test_audit_export_xlsx(client, db, redis_client, cipher):
    """현재 필터가 적용된 감사로그를 xlsx로 다운로드."""
    from io import BytesIO
    from openpyxl import load_workbook
    await _seed_audit_rows(db, cipher)  # USER 로그인 + SERVICE 구독생성 + SYSTEM 만료 3건
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)

    resp = await client.get("/admin/audit/export.xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    assert 'attachment; filename="audit-log-' in resp.headers["content-disposition"]
    wb = load_workbook(BytesIO(resp.content))
    ws = wb.active
    header = [c.value for c in ws[1]]
    assert header == ["시각", "행위자", "활동", "대상", "상세", "IP"]
    # 시드 3건 + 헤더 = 4행 이상 (admin 로그인 감사 로그가 더 있을 수 있음)
    assert ws.max_row >= 4
    actors = [ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1)]
    assert any(a and "외부 서비스 (검색대상서비스)" in a for a in actors)


async def test_audit_export_applies_filters(client, db, redis_client, cipher):
    """필터/검색이 적용된 결과만 내려받는다."""
    from io import BytesIO
    from openpyxl import load_workbook
    await _seed_audit_rows(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)

    resp = await client.get("/admin/audit/export.xlsx?action=subscription.create")
    wb = load_workbook(BytesIO(resp.content))
    ws = wb.active
    actions = [ws.cell(row=r, column=3).value for r in range(2, ws.max_row + 1)]
    assert actions == ["구독 생성"]


async def test_audit_page_has_export_button(client, db, redis_client, cipher):
    await _seed_audit_rows(db, cipher)
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    resp = await client.get("/admin/audit?action=auth.login")
    # 현재 쿼리스트링이 export 링크에 유지된다
    assert "/admin/audit/export.xlsx?" in resp.text
    assert "action=auth.login" in resp.text
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/e2e/test_admin_operations.py -k export -x -q`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: export 라우트 구현**

`app/admin/routes/audit.py` 상단 import에 추가:

```python
from io import BytesIO

from fastapi.responses import Response
from openpyxl import Workbook

from app.core.clock import utcnow
```

(`fastapi.responses`는 기존 import가 없으므로 신규 — `from fastapi import APIRouter, Depends, Request` 아래에 배치)

파일 끝(`audit_list` 아래)에 추가:

```python
_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("감사로그")
    ws.append(["시각", "행위자", "활동", "대상", "상세", "IP"])
    for r in rows:
        actor = (f"외부 서비스 ({r['actor_service_name']})"
                 if r["actor_service_name"] else r["actor"])
        ws.append([r["time"].strftime("%Y-%m-%d %H:%M:%S"), actor, r["action"],
                   r["target"], r["detail"], r["ip"]])
    buf = BytesIO()
    wb.save(buf)
    filename = f"audit-log-{utcnow().strftime('%Y%m%d-%H%M')}.xlsx"
    return Response(buf.getvalue(), media_type=_XLSX_MEDIA,
                    headers={"Content-Disposition":
                             f'attachment; filename="{filename}"'})
```

- [ ] **Step 4: 다운로드 버튼 추가**

`app/admin/templates/audit/_table.html`의 toolbar 매크로 호출 바로 아래(7행 `<div class="card">` 위)에 추가:

```html
<div style="display:flex;justify-content:flex-end;margin-bottom:8px">
  <a class="btn btn-sm btn-ghost" href="/admin/audit/export.xlsx?{{ pp.query_without('page') }}">
    <span data-lucide="download"></span>엑셀 다운로드</a>
</div>
```

(`pp.query_without('page')`는 q/sort/dir/필터를 유지한 쿼리스트링 — `app/admin/pagination.py:62` 참고)

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/e2e/test_admin_operations.py -q`
Expected: 전체 PASS

- [ ] **Step 6: 커밋**

```bash
git add app/admin/routes/audit.py app/admin/templates/audit/_table.html tests/e2e/test_admin_operations.py
git commit -m "feat(audit): 현재 필터 적용 엑셀(xlsx) 다운로드"
```

---

### Task 3: 과거 데이터 삭제 (purge)

**Files:**
- Modify: `app/admin/routes/audit.py` (purge 라우트)
- Modify: `app/admin/audit_labels.py` (라벨 1건)
- Modify: `app/admin/templates/audit/_table.html` (삭제 폼)
- Test: `tests/e2e/test_admin_operations.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/e2e/test_admin_operations.py` 끝에 추가 (`get_csrf` import는 파일에 이미 있음):

```python
async def test_audit_purge_deletes_only_before_date(client, db, redis_client, cipher):
    """기준일 이전 로그만 삭제하고, 삭제 행위를 감사 기록한다."""
    from datetime import datetime, timezone
    from sqlalchemy import select as sa_select
    from app.models import AuditLog
    from app.services.audit import record_audit
    await record_audit(db, actor_type="SYSTEM", action="old.entry")
    await record_audit(db, actor_type="SYSTEM", action="new.entry")
    await db.commit()
    # old.entry를 과거로 보낸다 (created_at은 server_default라 직접 update)
    old = await db.scalar(sa_select(AuditLog).where(AuditLog.action == "old.entry"))
    old.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    await db.commit()

    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    csrf = await get_csrf(redis_client, sid)
    resp = await client.post("/admin/audit/purge",
                             data={"csrf_token": csrf, "before": "2021-01-01"})
    assert resp.status_code == 303
    assert "flash=" in resp.headers["location"]

    db.expire_all()
    assert await db.scalar(sa_select(AuditLog).where(
        AuditLog.action == "old.entry")) is None        # 기준일 이전 → 삭제
    assert await db.scalar(sa_select(AuditLog).where(
        AuditLog.action == "new.entry")) is not None    # 이후 → 보존
    purge_log = await db.scalar(sa_select(AuditLog).where(
        AuditLog.action == "audit.purge"))
    assert purge_log is not None
    assert purge_log.detail["before"] == "2021-01-01"
    assert purge_log.detail["deleted_count"] == 1
    assert purge_log.actor_user_id == admin.id


async def test_audit_purge_invalid_date_shows_error(client, db, redis_client, cipher):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    sid = await admin_login(client, admin.email, pw)
    csrf = await get_csrf(redis_client, sid)
    resp = await client.post("/admin/audit/purge",
                             data={"csrf_token": csrf, "before": "not-a-date"})
    assert resp.status_code == 303
    assert "flash_type=error" in resp.headers["location"]


async def test_audit_page_has_purge_form(client, db, redis_client, cipher):
    admin, pw = await create_user(db, role="SYSTEM_ADMIN")
    await admin_login(client, admin.email, pw)
    resp = await client.get("/admin/audit")
    assert "/admin/audit/purge" in resp.text
    assert 'type="date"' in resp.text
    assert "data-confirm" in resp.text
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/e2e/test_admin_operations.py -k purge -x -q`
Expected: FAIL — 404

- [ ] **Step 3: purge 라우트 구현**

`app/admin/routes/audit.py` 상단 import에 추가:

```python
from datetime import date, datetime, timezone
from urllib.parse import quote

from fastapi.responses import RedirectResponse, Response
from sqlalchemy import String, cast, delete, func, select

from app.admin.deps import AdminContext, require_admin, validate_csrf
from app.services.audit import record_audit
```

(기존 import 라인들을 위 형태로 확장 — `Response`는 Task 2에서 추가됨, `RedirectResponse`/`delete`/`validate_csrf`/`record_audit`/`quote`/`datetime` 계열이 신규)

파일 끝에 추가:

```python
@router.post("/audit/purge")
async def audit_purge(request: Request, ctx: AdminContext = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)):
    """기준일(UTC 자정) 이전 감사로그 일괄 삭제. 삭제 행위는 감사 기록."""
    await validate_csrf(request, ctx)
    form = await request.form()
    raw = str(form.get("before", "")).strip()
    try:
        before = date.fromisoformat(raw)
    except ValueError:
        # audit 화면에는 ?error= 표시 블록이 없으므로 flash 토스트(error)로 통일
        msg = quote("기준일이 올바르지 않습니다")
        return RedirectResponse(f"/admin/audit?flash={msg}&flash_type=error",
                                status_code=303)
    cutoff = datetime(before.year, before.month, before.day, tzinfo=timezone.utc)
    result = await db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    deleted = result.rowcount or 0
    await record_audit(db, actor_type="USER", actor_user_id=ctx.user.id,
                       action="audit.purge",
                       detail={"before": raw, "deleted_count": deleted})
    await db.commit()
    msg = quote(f"감사로그 {deleted:,}건을 삭제했습니다")
    return RedirectResponse(f"/admin/audit?flash={msg}", status_code=303)
```

`app/admin/audit_labels.py`의 `ACTION_LABELS`에서 `"payment.reconciled_failed"` 라인 아래에 추가:

```python
    "audit.purge": "감사로그 삭제",
```


- [ ] **Step 4: 삭제 폼 추가**

`app/admin/templates/audit/_table.html`의 `{{ L.pager(...) }}` 아래, 카드 닫는 `</div>` 안에 추가:

```html
<form method="post" action="/admin/audit/purge" class="toolbar"
      style="margin-top:16px;justify-content:flex-end"
      data-confirm="기준일 이전의 감사로그가 영구 삭제됩니다. 필요하면 먼저 엑셀로 내려받으세요."
      data-confirm-title="과거 로그를 삭제할까요?" data-confirm-ok="삭제">
  <input type="hidden" name="csrf_token" value="{{ ctx.csrf_token }}">
  <input type="date" name="before" required style="width:auto">
  <button class="btn btn-sm btn-danger" type="submit">
    <span data-lucide="trash-2"></span>이전 로그 삭제</button>
</form>
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/e2e/test_admin_operations.py -q`
Expected: 전체 PASS

- [ ] **Step 6: 커밋**

```bash
git add app/admin/routes/audit.py app/admin/audit_labels.py app/admin/templates/audit/_table.html tests/e2e/test_admin_operations.py
git commit -m "feat(audit): 기준일 이전 감사로그 일괄 삭제 (audit.purge 감사 기록)"
```

---

### Task 4: 전체 검증

- [ ] **Step 1: 전체 테스트**

Run: `uv run pytest -q`
Expected: 전체 PASS (기존 340 + 신규 6 = 346 기준)

- [ ] **Step 2: 수동 확인 포인트 점검 (코드 리뷰 수준)**

- export가 `require_admin` 적용(SYSTEM_ADMIN 전용) 확인
- purge 폼이 CSRF hidden 포함 확인
- `uv.lock`에 openpyxl 반영 확인

- [ ] **Step 3: 커밋(잔여 수정 시에만)**

```bash
git add -A app tests
git commit -m "test: 감사로그 다운로드/삭제 잔여 정리"
```

## 변경하지 않는 것 (스펙 동일)

- 감사로그 목록의 기존 필터/검색/정렬/페이지네이션 동작
- AuditLog 모델 (마이그레이션 없음)
- 외부 API, 알림, 스케줄러
