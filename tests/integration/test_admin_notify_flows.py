"""관리자 이벤트 알림 — 수신자 조회(실 DB) + 3개 흐름 트리거 통합 테스트.

- _active_admin_emails: 활성 SYSTEM_ADMIN만 수신처로 조회되는지(역할/상태 필터).
- create_account / register_service / create_subscription가 admin_notifier를 호출하는지
  (RecordingAdminNotifier로 검증) + EmailAdminNotifier 실 발송(RecordingEmailSender).
"""
import pytest

from app.notifications.admin_notify import EmailAdminNotifier, RecordingAdminNotifier, _active_admin_emails
from app.notifications.email import RecordingEmailSender
from app.services import accounts
from app.services import registry
from app.services import subscriptions as subs
from app.services.cards import register_or_replace_card
from app.toss.fake import FakeTossClient
from tests.factories import create_plan, create_service, create_user


@pytest.fixture
def fake():
    return FakeTossClient()


@pytest.fixture
def email():
    return RecordingEmailSender()


# ── 수신자 조회 필터 ────────────────────────────────────────────────────────────

async def test_active_admin_emails_filters_role_and_status(db):
    await create_user(db, email="root@x.com", role="SYSTEM_ADMIN", status="ACTIVE")
    await create_user(db, email="pending@x.com", role="SYSTEM_ADMIN", status="PENDING")  # 비활성 제외
    await create_user(db, email="mgr@x.com", role="SERVICE_MANAGER", status="ACTIVE")  # 역할 제외
    emails = await _active_admin_emails(db)
    assert emails == ["root@x.com"]


# ── EmailAdminNotifier 실 발송(실 DB 수신자) ────────────────────────────────────

async def test_email_admin_notifier_account_created_sends_to_active_admins(db, email):
    await create_user(db, email="a1@x.com", role="SYSTEM_ADMIN", status="ACTIVE")
    await create_user(db, email="a2@x.com", role="SYSTEM_ADMIN", status="ACTIVE")
    new_user, _ = await create_user(db, email="created@x.com", role="SERVICE_MANAGER",
                                    status="PENDING")
    notifier = EmailAdminNotifier(email)
    # send()는 동기 Recording이라 await 시 즉시 기록됨(운영은 QueuedEmailSender가 큐 적재)
    await notifier.account_created(db, user=new_user, actor_user_id=None, service_ids=None)
    assert {m["to"] for m in email.sent} == {"a1@x.com", "a2@x.com"}
    assert all(m["html"] for m in email.sent)               # HTML 본문 동반
    assert "created@x.com" in email.sent[0]["subject"]


# ── 흐름 트리거(RecordingAdminNotifier) ─────────────────────────────────────────

async def test_create_account_triggers_admin_notifier(db, email):
    rec = RecordingAdminNotifier()
    await accounts.create_account(
        db, email, email="mgr2@x.com", role="SYSTEM_ADMIN", service_ids=[],
        base_url="http://x", admin_notifier=rec)
    assert [e["event"] for e in rec.events] == ["account.created"]
    assert rec.events[0]["email"] == "mgr2@x.com"


async def test_register_service_triggers_admin_notifier(db, cipher):
    rec = RecordingAdminNotifier()
    mgr, _ = await create_user(db, email="svc-mgr@x.com", role="SERVICE_MANAGER",
                               status="ACTIVE")
    await registry.register_service(
        db, cipher, name="알림서비스", allowed_ips=[],
        manager_user_ids=[mgr.id], primary_user_id=mgr.id,
        admin_notifier=rec)
    assert [e["event"] for e in rec.events] == ["service.created"]
    assert rec.events[0]["name"] == "알림서비스"
    assert rec.events[0]["managers"] == ["svc-mgr@x.com"]


async def test_create_plan_triggers_admin_notifier(db, cipher):
    rec = RecordingAdminNotifier()
    svc, _, _ = await create_service(db, cipher)
    from app.services import plans as plan_service
    await plan_service.create_plan(
        db, service_id=svc.id, name="베이직", price=9900, billing_cycle="MONTH",
        admin_notifier=rec)
    assert [e["event"] for e in rec.events] == ["plan.created"]
    assert rec.events[0]["name"] == "베이직" and rec.events[0]["price"] == 9900


async def test_create_subscription_triggers_admin_notifier(db, cipher, fake):
    rec = RecordingAdminNotifier()
    svc, _, _ = await create_service(db, cipher)
    plan = await create_plan(db, svc, price=10000, billing_cycle="MONTH")
    await register_or_replace_card(db, fake, cipher, service=svc,
                                   external_user_id="sub-u@e.com",
                                   customer_key="ck-valid-1", auth_key="auth-1")
    await subs.create_subscription(
        db, fake, cipher, service=svc, plan_id=plan.id,
        external_user_id="sub-u@e.com", admin_notifier=rec)
    assert [e["event"] for e in rec.events] == ["subscription.created"]
    assert rec.events[0]["service"] == svc.name
    assert rec.events[0]["amount"] == 10000
    assert rec.events[0]["is_first"] is True
