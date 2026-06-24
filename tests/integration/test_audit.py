import uuid

from sqlalchemy import select

from app.models import AuditLog
from app.notifications.email import RecordingEmailSender
from app.services.audit import record_audit


async def test_record_audit_persists(db):
    await record_audit(db, actor_type="SYSTEM", action="test.action",
                       target_type="service", target_id="t-1",
                       detail={"k": "v"}, ip_address="127.0.0.1")
    await db.commit()
    row = await db.scalar(select(AuditLog).where(AuditLog.action == "test.action"))
    assert row is not None
    assert row.detail == {"k": "v"}


async def test_record_audit_actor_service_id(db):
    sid = uuid.uuid4()
    await record_audit(db, actor_type="SERVICE", action="test.svc_actor",
                       actor_service_id=sid)
    await db.commit()
    row = await db.scalar(select(AuditLog).where(AuditLog.action == "test.svc_actor"))
    assert row.actor_service_id == sid


async def test_recording_email_sender():
    sender = RecordingEmailSender()
    await sender.send("a@b.com", "제목", "본문")
    # html 기본 None(평문 전용) — HTML 멀티파트 지원 추가에 따라 html 키 포함
    assert sender.sent == [{"to": "a@b.com", "subject": "제목", "body": "본문", "html": None}]
