"""시스템 관리자 이벤트 알림 메일 — 계정/서비스/구독 생성 시 SYSTEM_ADMIN에게 통지.

세 가지 운영 이벤트가 발생하면 **활성(ACTIVE) SYSTEM_ADMIN 계정 전원**에게 상세 내용을
HTML 메일로 보낸다(평문 대체 본문 동반).

- 계정 생성(account.created)      : 새 관리자 계정이 생성됨
- 서비스 생성(service.created)    : 새 서비스가 등록됨
- 구독 생성(subscription.created) : 새 구독이 생성됨(외부 API 포함 — 모든 구독)

설계는 서비스 알림(service_notify.py)과 동일한 패턴을 따른다.
- **best-effort(fire-and-forget)**: 실제 SMTP 발송은 백그라운드 태스크로 보내고,
  실패해도 본 처리(계정·서비스·구독 생성)에는 영향을 주지 않는다(이미 커밋된 뒤 호출).
- 수신자 조회(_active_admin_emails)는 호출자의 DB 세션으로 즉시 수행하고,
  네트워크가 필요한 SMTP만 백그라운드로 흘려 응답 지연을 막는다.

테스트는 RecordingAdminNotifier로 호출 내역을 검사한다(실제 메일/네트워크 없음).
"""
import logging
from html import escape
from typing import Protocol

from sqlalchemy import select

from app.core.clock import kst_format, utcnow
from app.models import Service, User
from app.models.enums import SubscriptionStatus, UserRole, UserStatus
from app.notifications.email import EmailSender

logger = logging.getLogger("admin_notify")

# ── 이벤트 식별자 ───────────────────────────────────────────────────────────────
EVENT_ACCOUNT_CREATED = "account.created"            # 새 관리자 계정 생성
EVENT_SERVICE_CREATED = "service.created"            # 새 서비스 등록
EVENT_PLAN_CREATED = "plan.created"                  # 새 구독 요금제 등록
EVENT_SUBSCRIPTION_CREATED = "subscription.created"  # 새 구독 생성

# 결제주기 코드 → 한글
_CYCLE_KO = {"YEAR": "년", "MONTH": "월", "WEEK": "주", "DAY": "일", "MINUTE": "분"}


def _cycle_label(plan) -> str:
    """요금제 결제주기를 사람이 읽는 문구로(일/분 주기는 실제 수치 포함)."""
    if plan.billing_cycle == "DAY" and plan.cycle_days:
        return f"{plan.cycle_days}일마다"
    if plan.billing_cycle == "MINUTE" and plan.cycle_minutes:
        return f"{plan.cycle_minutes}분마다"
    return f"{_CYCLE_KO.get(plan.billing_cycle, plan.billing_cycle)} 단위"


def _benefit_label(type_: str, value, *, allow_free: bool) -> str:
    """첫 결제 혜택/상시 할인 표기 — 정액(원)·정률(%)·무료·없음."""
    if type_ == "FREE" and allow_free:
        return "무료"
    if type_ == "DISCOUNT_AMOUNT":
        return f"{(value or 0):,}원 할인"
    if type_ == "DISCOUNT_PERCENT":
        return f"{value or 0}% 할인"
    return "없음"

# ── 코드값 → 한글 라벨(메일 가독성) ─────────────────────────────────────────────
_ROLE_KO = {UserRole.SYSTEM_ADMIN: "시스템 관리자",
            UserRole.SERVICE_MANAGER: "서비스 담당자"}
_USER_STATUS_KO = {UserStatus.PENDING: "비밀번호 설정 대기", UserStatus.ACTIVE: "정상",
                   UserStatus.LOCKED: "잠김", UserStatus.DISABLED: "비활성",
                   UserStatus.DELETED: "삭제"}
_SUB_STATUS_KO = {SubscriptionStatus.TRIAL: "체험(TRIAL)",
                  SubscriptionStatus.ACTIVE: "활성(ACTIVE)"}


async def _active_admin_emails(db) -> list[str]:
    """알림 수신처 — 활성(ACTIVE) SYSTEM_ADMIN 계정 이메일(중복 제거·정렬)."""
    rows = await db.scalars(
        select(User.email).where(User.role == UserRole.SYSTEM_ADMIN,
                                 User.status == UserStatus.ACTIVE))
    return sorted({e for e in rows.all() if e})


async def _email_of(db, user_id) -> str:
    """감사용 — actor_user_id의 이메일(없으면 빈 문자열). 생성자 표시에 사용."""
    if not user_id:
        return ""
    user = await db.get(User, user_id)
    return user.email if user else ""


def _render(title: str, rows: list[tuple[str, str]]) -> tuple[str, str]:
    """(제목, 라벨/값 목록) → (평문 본문, HTML 본문) 한 쌍을 만든다.

    HTML은 이메일 클라이언트 호환을 위해 인라인 스타일 표를 사용한다(<style> 미지원 대비).
    값은 escape로 이스케이프해 HTML 인젝션을 방지한다.
    """
    # 평문(대체 본문)
    text = title + "\n\n" + "\n".join(f"- {label}: {value}" for label, value in rows)
    # HTML 본문
    tr = "".join(
        f'<tr>'
        f'<th align="left" style="padding:8px 14px;background:#f1f5f9;color:#334155;'
        f'font-weight:600;border-bottom:1px solid #e2e8f0;white-space:nowrap">{escape(label)}</th>'
        f'<td style="padding:8px 14px;color:#0f172a;border-bottom:1px solid #e2e8f0">'
        f'{escape(str(value))}</td>'
        f'</tr>'
        for label, value in rows)
    html = (
        '<div style="font-family:Pretendard,-apple-system,sans-serif;max-width:640px;'
        'margin:0 auto;padding:24px;color:#0f172a">'
        f'<h2 style="font-size:18px;margin:0 0 4px">{escape(title)}</h2>'
        '<p style="color:#64748b;font-size:13px;margin:0 0 16px">구독·결제 서버 관리자 알림</p>'
        '<table style="border-collapse:collapse;width:100%;border:1px solid #e2e8f0;'
        f'border-radius:8px;overflow:hidden;font-size:14px">{tr}</table>'
        '<p style="color:#94a3b8;font-size:12px;margin:16px 0 0">'
        '본 메일은 시스템 관리자에게 자동 발송된 알림입니다.</p>'
        '</div>')
    return text, html


class AdminNotifier(Protocol):
    """관리자 이벤트 알림 인터페이스(운영 EmailAdminNotifier / 테스트 Recording)."""

    async def account_created(self, db, *, user, actor_user_id=None,
                              service_ids=None) -> None: ...

    async def service_created(self, db, *, service, manager_emails,
                              actor_user_id=None) -> None: ...

    async def plan_created(self, db, *, plan, actor_user_id=None) -> None: ...

    async def subscription_created(self, db, *, service, sub, plan, amount,
                                   order_id, is_first) -> None: ...


class EmailAdminNotifier:
    """운영 구현 — 활성 SYSTEM_ADMIN 전원에게 HTML 메일을 메모리 큐로 적재한다."""

    def __init__(self, email_sender: EmailSender) -> None:
        self._email = email_sender

    async def _dispatch(self, db, subject: str, text: str, html: str) -> None:
        """수신자 조회 후 각 수신처로 메일을 순서대로 적재한다(best-effort).

        email_sender(QueuedEmailSender)는 큐에 적재만 하고 즉시 반환하므로, 여기서
        await해도 SMTP를 기다리지 않는다(실제 발송·감사로그는 큐 워커가 순차 처리).
        """
        try:
            recipients = await _active_admin_emails(db)
            if not recipients:
                logger.info("관리자 알림 %s — 활성 SYSTEM_ADMIN 없음, 발송 생략", subject)
                return
            for to in recipients:
                await self._email.send(to, subject, text, html=html)  # 큐 적재(즉시)
        except Exception as exc:  # noqa: BLE001 — best-effort: 알림 실패가 본 처리를 깨면 안 됨
            logger.warning("관리자 알림 '%s' 구성 실패: %s", subject, exc)

    async def account_created(self, db, *, user, actor_user_id=None,
                              service_ids=None) -> None:
        names = []
        if service_ids:
            rows = await db.scalars(select(Service.name).where(Service.id.in_(service_ids)))
            names = sorted(rows.all())
        actor = await _email_of(db, actor_user_id)
        subject = f"[결제시스템] 새 관리자 계정 생성 — {user.email}"
        text, html = _render("새 관리자 계정이 생성되었습니다", [
            ("이메일", user.email),
            ("역할", _ROLE_KO.get(user.role, user.role)),
            ("담당 서비스", ", ".join(names) if names else "(없음)"),
            ("상태", _USER_STATUS_KO.get(user.status, user.status)),
            ("생성자", actor or "(시스템)"),
            ("생성시각", kst_format(utcnow(), "%Y-%m-%d %H:%M:%S")),
        ])
        await self._dispatch(db, subject, text, html)

    async def service_created(self, db, *, service, manager_emails,
                              actor_user_id=None) -> None:
        actor = await _email_of(db, actor_user_id)
        ips = service.allowed_ips or []
        subject = f"[결제시스템] 새 서비스 등록 — {service.name}"
        text, html = _render("새 서비스가 등록되었습니다", [
            ("서비스명", service.name),
            ("서비스 ID", str(service.id)),
            ("대표 담당자", service.manager_email or "(없음)"),
            ("담당자 전체", ", ".join(manager_emails) if manager_emails else "(없음)"),
            ("허용 IP", f"{len(ips)}개" + (f" ({', '.join(ips)})" if ips else " (제한 없음)")),
            ("취소 정책", ("허용" if service.cancellation_enabled else "비허용")
                + f" · 수수료율 {service.cancellation_fee_percent}%"),
            ("토스 시크릿 키", "설정됨" if service.toss_secret_key_encrypted else "미설정"),
            ("생성자", actor or "(시스템)"),
            ("생성시각", kst_format(utcnow(), "%Y-%m-%d %H:%M:%S")),
        ])
        await self._dispatch(db, subject, text, html)

    async def plan_created(self, db, *, plan, actor_user_id=None) -> None:
        svc = await db.get(Service, plan.service_id)
        service_name = svc.name if svc else "(알 수 없음)"
        actor = await _email_of(db, actor_user_id)
        subject = f"[결제시스템] 새 구독 요금제 등록 — {service_name} / {plan.name}"
        text, html = _render("새 구독 요금제가 등록되었습니다", [
            ("서비스명", service_name),
            ("요금제명", plan.name),
            ("가격", f"{plan.price:,}원"),
            ("결제주기", _cycle_label(plan)),
            ("첫 결제 혜택", _benefit_label(plan.first_payment_type,
                                       plan.first_payment_value, allow_free=True)),
            ("상시 할인", _benefit_label(plan.recurring_discount_type,
                                     plan.recurring_discount_value, allow_free=False)),
            ("체험", f"{plan.trial_days}일" if plan.trial_enabled and plan.trial_days else "없음"),
            ("자동결제", "예" if plan.auto_renew else "아니오(첫 주기 후 만료)"),
            ("생성자", actor or "(시스템)"),
            ("생성시각", kst_format(utcnow(), "%Y-%m-%d %H:%M:%S")),
        ])
        await self._dispatch(db, subject, text, html)

    async def subscription_created(self, db, *, service, sub, plan, amount,
                                   order_id, is_first) -> None:
        subject = f"[결제시스템] 새 구독 생성 — {service.name} / {plan.name}"
        text, html = _render("새 구독이 생성되었습니다", [
            ("서비스명", service.name),
            ("요금제", plan.name),
            ("구독자(이메일)", sub.external_user_id or "(없음)"),
            ("상태", _SUB_STATUS_KO.get(sub.status, sub.status)),
            ("첫 구독 여부", "예" if is_first else "아니오"),
            ("체험 여부", "예" if sub.status == SubscriptionStatus.TRIAL else "아니오"),
            ("청구 금액", f"{amount:,}원" + (" (체험·결제 없음)" if amount == 0 else "")),
            ("주문번호", order_id or "(없음)"),
            ("구독 기간", f"{kst_format(sub.current_period_start, '%Y-%m-%d %H:%M')}"
                      f" ~ {kst_format(sub.current_period_end, '%Y-%m-%d %H:%M')}"),
            ("다음 결제일", kst_format(sub.next_billing_at, "%Y-%m-%d %H:%M")
                if sub.next_billing_at else "(없음 — 기간 종료 시 만료)"),
            ("생성시각", kst_format(utcnow(), "%Y-%m-%d %H:%M:%S")),
        ])
        await self._dispatch(db, subject, text, html)


class RecordingAdminNotifier:
    """테스트용 — 호출 이벤트를 events에 동기 기록(실제 메일/네트워크 없음)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def account_created(self, db, *, user, actor_user_id=None,
                              service_ids=None) -> None:
        self.events.append({"event": EVENT_ACCOUNT_CREATED, "email": user.email,
                            "role": user.role})

    async def service_created(self, db, *, service, manager_emails,
                              actor_user_id=None) -> None:
        self.events.append({"event": EVENT_SERVICE_CREATED, "name": service.name,
                            "managers": list(manager_emails or [])})

    async def plan_created(self, db, *, plan, actor_user_id=None) -> None:
        self.events.append({"event": EVENT_PLAN_CREATED, "name": plan.name,
                            "price": plan.price, "service_id": str(plan.service_id)})

    async def subscription_created(self, db, *, service, sub, plan, amount,
                                   order_id, is_first) -> None:
        self.events.append({"event": EVENT_SUBSCRIPTION_CREATED, "service": service.name,
                            "plan": plan.name, "email": sub.external_user_id,
                            "amount": amount, "status": sub.status, "is_first": is_first})
