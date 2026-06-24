"""관리자 이벤트 알림 메일 — EmailSender HTML 지원·템플릿·디스패치 단위 테스트.

DB가 필요한 수신자 조회(_active_admin_emails)와 3개 흐름 연동은 통합 테스트에서 다룬다.
여기서는 DB 없이 동작을 검증한다(_active_admin_emails는 monkeypatch).
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.enums import SubscriptionStatus, UserRole, UserStatus
from app.notifications import admin_notify
from app.notifications.admin_notify import (
    EmailAdminNotifier,
    RecordingAdminNotifier,
    _benefit_label,
    _cycle_label,
    _render,
)
from app.notifications.email import GmailEmailSender, RecordingEmailSender

_DT = datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc)  # → 14:00 KST


# ── EmailSender HTML 멀티파트 ──────────────────────────────────────────────────

def test_gmail_send_sync_builds_multipart_when_html(monkeypatch):
    """html이 있으면 text/plain + text/html 멀티파트 메시지를 구성한다."""
    captured = {}

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): captured["msg"] = msg

    monkeypatch.setattr("app.notifications.email.smtplib.SMTP", _FakeSMTP)
    sender = GmailEmailSender(host="h", port=587, username="u@x.com", password="p")
    sender._send_sync("to@x.com", "subj", "plain body", "<b>html body</b>")
    msg = captured["msg"]
    assert msg.is_multipart()
    types = {p.get_content_type() for p in msg.iter_parts()}
    assert types == {"text/plain", "text/html"}


def test_gmail_send_sync_plain_only_when_no_html(monkeypatch):
    """html=None이면 단일(text/plain) 메시지 — 멀티파트가 아니다."""
    captured = {}

    class _FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, *a): pass
        def send_message(self, msg): captured["msg"] = msg

    monkeypatch.setattr("app.notifications.email.smtplib.SMTP", _FakeSMTP)
    sender = GmailEmailSender(host="h", port=587, username="u@x.com", password="p")
    sender._send_sync("to@x.com", "subj", "plain only")
    assert not captured["msg"].is_multipart()
    assert captured["msg"].get_content_type() == "text/plain"


async def test_recording_email_sender_records_html():
    sender = RecordingEmailSender()
    await sender.send("to@x.com", "s", "body", html="<i>h</i>")
    assert sender.sent[0]["html"] == "<i>h</i>"


# ── 템플릿(_render) ─────────────────────────────────────────────────────────────

def test_render_includes_values_and_escapes_html():
    text, html = _render("제목", [("이름", "<script>x</script>"), ("값", "abc")])
    # 평문: 라벨/값 그대로
    assert "- 이름: <script>x</script>" in text and "- 값: abc" in text
    # HTML: 인젝션 방지(escape) + 라벨/값 포함
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "abc" in html and "제목" in html


# ── EmailAdminNotifier 디스패치(수신자 monkeypatch) ─────────────────────────────

async def test_subscription_created_sends_html_to_all_admins(monkeypatch):
    async def _fake_admins(db):
        return ["a@x.com", "b@x.com"]
    monkeypatch.setattr(admin_notify, "_active_admin_emails", _fake_admins)

    sender = RecordingEmailSender()
    notifier = EmailAdminNotifier(sender)
    service = SimpleNamespace(name="SNS")
    plan = SimpleNamespace(name="베이직")
    sub = SimpleNamespace(external_user_id="user@x.com", status=SubscriptionStatus.ACTIVE,
                          current_period_start=_DT, current_period_end=_DT,
                          next_billing_at=None)
    await notifier.subscription_created(
        db=None, service=service, sub=sub, plan=plan, amount=10000,
        order_id="ord-1", is_first=True)

    assert {m["to"] for m in sender.sent} == {"a@x.com", "b@x.com"}
    msg = sender.sent[0]
    assert "새 구독 생성" in msg["subject"] and "SNS" in msg["subject"]
    assert msg["html"] and "베이직" in msg["html"] and "user@x.com" in msg["html"]
    assert "10,000원" in msg["html"]


async def test_plan_created_dispatch(monkeypatch):
    async def _fake_admins(db):
        return ["admin@x.com"]
    monkeypatch.setattr(admin_notify, "_active_admin_emails", _fake_admins)

    class _DB:  # plan.service_id → Service(name) 해석용 가짜 db
        async def get(self, model, pk):
            return SimpleNamespace(name="SNS")

    sender = RecordingEmailSender()
    notifier = EmailAdminNotifier(sender)
    plan = SimpleNamespace(service_id="svc-1", name="베이직", price=9900,
                           billing_cycle="MONTH", cycle_days=None, cycle_minutes=None,
                           first_payment_type="FREE", first_payment_value=None,
                           recurring_discount_type="DISCOUNT_PERCENT",
                           recurring_discount_value=10,
                           trial_enabled=True, trial_days=7, auto_renew=True)
    await notifier.plan_created(_DB(), plan=plan, actor_user_id=None)
    assert len(sender.sent) == 1
    m = sender.sent[0]
    assert "새 구독 요금제 등록" in m["subject"] and "베이직" in m["subject"]
    assert "SNS" in m["html"] and "9,900원" in m["html"]
    assert "월 단위" in m["html"] and "무료" in m["html"] and "10% 할인" in m["html"]
    assert "7일" in m["html"]


def test_cycle_and_benefit_labels():
    assert _benefit_label("FREE", None, allow_free=True) == "무료"
    assert _benefit_label("FREE", None, allow_free=False) == "없음"   # 상시할인엔 무료 없음
    assert _benefit_label("DISCOUNT_AMOUNT", 1000, allow_free=False) == "1,000원 할인"
    assert _benefit_label("DISCOUNT_PERCENT", 15, allow_free=True) == "15% 할인"
    assert _cycle_label(SimpleNamespace(billing_cycle="DAY", cycle_days=10, cycle_minutes=None)) == "10일마다"
    assert _cycle_label(SimpleNamespace(billing_cycle="YEAR", cycle_days=None, cycle_minutes=None)) == "년 단위"


async def test_account_created_dispatch(monkeypatch):
    async def _fake_admins(db):
        return ["admin@x.com"]
    monkeypatch.setattr(admin_notify, "_active_admin_emails", _fake_admins)

    sender = RecordingEmailSender()
    notifier = EmailAdminNotifier(sender)
    user = SimpleNamespace(email="new@x.com", role=UserRole.SYSTEM_ADMIN,
                           status=UserStatus.PENDING)
    # service_ids=None, actor_user_id=None → 추가 DB 조회 없음
    await notifier.account_created(db=None, user=user, actor_user_id=None,
                                   service_ids=None)
    assert len(sender.sent) == 1
    m = sender.sent[0]
    assert m["to"] == "admin@x.com"
    assert "new@x.com" in m["subject"]
    assert "시스템 관리자" in m["html"]  # 역할 한글 라벨


async def test_no_admins_sends_nothing(monkeypatch):
    async def _fake_admins(db):
        return []
    monkeypatch.setattr(admin_notify, "_active_admin_emails", _fake_admins)
    sender = RecordingEmailSender()
    notifier = EmailAdminNotifier(sender)
    user = SimpleNamespace(email="n@x.com", role=UserRole.SERVICE_MANAGER,
                           status=UserStatus.PENDING)
    await notifier.account_created(db=None, user=user)
    assert sender.sent == []


# ── RecordingAdminNotifier(테스트 페이크) ───────────────────────────────────────

async def test_recording_admin_notifier_records_events():
    rec = RecordingAdminNotifier()
    user = SimpleNamespace(email="u@x.com", role=UserRole.SERVICE_MANAGER)
    service = SimpleNamespace(name="SNS")
    plan = SimpleNamespace(name="프로")
    sub = SimpleNamespace(external_user_id="s@x.com", status=SubscriptionStatus.TRIAL)
    await rec.account_created(db=None, user=user)
    await rec.service_created(db=None, service=service, manager_emails=["m@x.com"])
    await rec.subscription_created(db=None, service=service, sub=sub, plan=plan,
                                   amount=0, order_id="", is_first=True)
    evs = {e["event"]: e for e in rec.events}
    assert evs["account.created"]["email"] == "u@x.com"
    assert evs["service.created"]["managers"] == ["m@x.com"]
    assert evs["subscription.created"]["plan"] == "프로"
