"""Admin 감사 로그(AuditLog) 관리 라우트.

SYSTEM_ADMIN 전용 기능으로 세 엔드포인트를 제공한다.

GET  /audit          — 감사 로그 목록 (행위자·활동 필터, 키워드 검색, 페이지네이션).
GET  /audit/export.xlsx — 현재 필터 그대로 전체 로그를 xlsx 다운로드.
POST /audit/purge    — 기준일 이전 로그를 일괄 삭제하고 삭제 행위 자체를 감사 기록.

_resolve_names : 목록의 actor/target UUID를 배치 조회해 사람이 읽을 수 있는 이름으로 변환.
_build_audit_query : 목록과 엑셀 다운로드가 동일한 검색·필터 조건을 공유하도록 분리.
_build_rows    : AuditLog ORM 행 → 화면/엑셀 공용 dict 변환.
"""

import uuid
from datetime import date, datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import String, cast, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import kst_format, utcnow

from app.admin import render_list
from app.admin.export import EXPORT_MAX_ROWS, xlsx_response
from app.admin.audit_labels import (
    ACTION_LABELS,
    action_label,
    actor_label,
    detail_summary,
    target_label,
)
from app.admin.deps import AdminContext, require_admin, validate_csrf
from app.admin.pagination import PageParams, paginate
from app.core.deps import get_db
from app.models import AuditLog, Card, Plan, Service, Subscription, User
from app.services.audit import record_audit

router = APIRouter()

_AUDIT_SORT = {"created_at": AuditLog.created_at, "action": AuditLog.action,
               "actor_type": AuditLog.actor_type}


def _as_uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def _resolve_names(db: AsyncSession, logs: list[AuditLog]) -> dict:
    """배치 조회: 행위자 이메일 + 대상 이름 맵."""
    actor_ids = {l.actor_user_id for l in logs if l.actor_user_id}
    actors: dict[uuid.UUID, str] = {}
    if actor_ids:
        for u in (await db.scalars(select(User).where(User.id.in_(actor_ids)))).all():
            actors[u.id] = u.email

    actor_svc_ids = {l.actor_service_id for l in logs if l.actor_service_id}
    actor_services: dict[uuid.UUID, str] = {}
    if actor_svc_ids:
        for s in (await db.scalars(select(Service).where(
                Service.id.in_(actor_svc_ids)))).all():
            actor_services[s.id] = s.name

    targets: dict[tuple, str] = {}
    by_type: dict[str, set] = {}
    for l in logs:
        if l.target_type and l.target_id:
            tid = _as_uuid(l.target_id)
            if tid:
                by_type.setdefault(l.target_type, set()).add(tid)

    async def names(model, ids, attr):
        if not ids:
            return
        for obj in (await db.scalars(select(model).where(model.id.in_(ids)))).all():
            targets[(model.__tablename__, obj.id)] = getattr(obj, attr)

    await names(Service, by_type.get("service"), "name")
    await names(Plan, by_type.get("plan"), "name")
    await names(User, by_type.get("user"), "email")
    await names(Subscription, by_type.get("subscription"), "external_user_id")
    await names(Card, by_type.get("card"), "external_user_id")  # 카드는 사용자ID로 표시
    return {"actors": actors, "actor_services": actor_services, "targets": targets}


_TARGET_TABLE = {"service": "services", "plan": "plans", "user": "users",
                 "subscription": "subscriptions", "payment": "payments",
                 "card": "cards"}


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


@router.get("/audit")
async def audit_list(request: Request, ctx: AdminContext = Depends(require_admin),
                     db: AsyncSession = Depends(get_db)):
    pp = PageParams.from_request(request, sortable=set(_AUDIT_SORT),
                                 default_sort="created_at",
                                 filter_keys=("actor_type", "action"))
    base = _build_audit_query(pp)
    items_q = base.order_by(pp.order_by(_AUDIT_SORT))
    page = await paginate(db, items_q, pp, flatten=True)  # Row → AuditLog
    logs = page.items
    page.items = await _build_rows(db, logs)
    return render_list(request, "audit/list.html", "audit/_table.html",
                      ctx=ctx, page=page, pp=pp,
                      actor_filter=pp.filters.get("actor_type", ""),
                      action_filter=pp.filters.get("action", ""),
                      action_options=[("", "전체 활동")] + list(ACTION_LABELS.items()))


@router.get("/audit/export.xlsx")
async def audit_export(request: Request, ctx: AdminContext = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)):
    """현재 필터/검색이 적용된 감사로그 전체를 xlsx로 다운로드 (페이지네이션 무시)."""
    pp = PageParams.from_request(request, sortable=set(_AUDIT_SORT),
                                 default_sort="created_at",
                                 filter_keys=("actor_type", "action"))
    # 행 상한(감사 Phase 3 — 성능 M2): 감사 로그는 무한 증가 테이블이라 특히 중요
    items_q = (_build_audit_query(pp)
               .order_by(pp.order_by(_AUDIT_SORT)).limit(EXPORT_MAX_ROWS))
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
    if before > utcnow().date():
        msg = quote("기준일은 오늘 이후일 수 없습니다")
        return RedirectResponse(f"/admin/audit?flash={msg}&flash_type=error",
                                status_code=303)
    cutoff = datetime(before.year, before.month, before.day, tzinfo=timezone.utc)
    result = await db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    deleted = result.rowcount if result.rowcount and result.rowcount > 0 else 0
    await record_audit(db, actor_type="USER", actor_user_id=ctx.user.id,
                       action="audit.purge",
                       detail={"before": before.isoformat(), "deleted_count": deleted})
    await db.commit()
    msg = quote(f"감사로그 {deleted:,}건을 삭제했습니다")
    return RedirectResponse(f"/admin/audit?flash={msg}", status_code=303)
