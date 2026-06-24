"""인메모리 이메일 큐 — 실 DB 감사로그 영속 + 순차 발송 통합 테스트."""
from sqlalchemy import select

from app.models import AuditLog
from app.notifications.email import RecordingEmailSender
from app.notifications.email_queue import ACTION_EMAIL_SENT, EmailQueue


async def test_queue_persists_email_audit_rows_in_order(db, session_factory):
    sender = RecordingEmailSender()
    q = EmailQueue(sender, session_factory)
    q.start()
    for i in range(3):
        q.enqueue(f"q{i}@x.com", f"[결제시스템] 테스트{i}", "본문")
    await q.stop()

    # 적재 순서대로 발송됨(단일 워커)
    assert [m["to"] for m in sender.sent] == ["q0@x.com", "q1@x.com", "q2@x.com"]

    # 감사로그(email.sent) 3건이 실제 DB에 적재됨
    rows = (await db.scalars(
        select(AuditLog).where(AuditLog.action == ACTION_EMAIL_SENT)
        .order_by(AuditLog.created_at))).all()
    targets = [r.target_id for r in rows if r.target_id and r.target_id.startswith("q")]
    assert set(targets) == {"q0@x.com", "q1@x.com", "q2@x.com"}
    assert all(r.actor_type == "SYSTEM" and r.target_type == "email" for r in rows)
