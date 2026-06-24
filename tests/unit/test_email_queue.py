"""인메모리 이메일 큐 — 순차(FIFO) 발송 + 발송 감사로그 단위 테스트.

DB 없이 가짜 session_factory(add/commit no-op)로 감사 기록 동작까지 검증한다.
실 DB 영속은 통합 테스트(test_email_queue_db.py)에서 다룬다.
"""
from app.notifications.email import RecordingEmailSender
from app.notifications.email_queue import (
    ACTION_EMAIL_FAILED,
    ACTION_EMAIL_SENT,
    EmailQueue,
    QueuedEmailSender,
)


class _CaptureFactory:
    """add된 객체를 한 리스트에 모으는 가짜 session_factory."""

    def __init__(self):
        self.added = []

    def __call__(self):
        cap = self

        class _S:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def add(self, obj):
                cap.added.append(obj)

            async def commit(self):
                pass

        return _S()


async def test_queue_sends_in_fifo_order_and_audits():
    sender = RecordingEmailSender()
    cap = _CaptureFactory()
    q = EmailQueue(sender, cap)
    q.start()
    for i in range(5):
        assert q.enqueue(f"u{i}@x.com", f"제목{i}", "본문") is True
    await q.stop()   # 센티넬 이전 항목을 모두 처리 후 종료

    # 적재 순서대로 발송(단일 워커 → 순차)
    assert [m["to"] for m in sender.sent] == [f"u{i}@x.com" for i in range(5)]
    # 발송마다 감사로그(email.sent) 1건씩
    sent_logs = [o for o in cap.added if getattr(o, "action", None) == ACTION_EMAIL_SENT]
    assert len(sent_logs) == 5
    assert sent_logs[0].actor_type == "SYSTEM"
    assert sent_logs[0].target_type == "email"
    assert sent_logs[0].target_id == "u0@x.com"


async def test_queue_audits_failure_as_email_failed():
    sender = RecordingEmailSender()
    sender.fail = True                      # 발송 실패 시뮬레이션
    cap = _CaptureFactory()
    q = EmailQueue(sender, cap)
    q.start()
    q.enqueue("x@x.com", "제목", "본문")
    await q.stop()
    assert sender.sent == []                # 실제 발송 안 됨
    failed = [o for o in cap.added if getattr(o, "action", None) == ACTION_EMAIL_FAILED]
    assert len(failed) == 1 and failed[0].detail["ok"] is False


async def test_queued_email_sender_enqueues_and_returns_true():
    sender = RecordingEmailSender()
    cap = _CaptureFactory()
    q = EmailQueue(sender, cap)
    qs = QueuedEmailSender(q)
    # 워커 시작 전: 적재만 하고 즉시 True
    assert await qs.send("a@x.com", "s", "b", html="<b>h</b>") is True
    # 시작→정지하면 적재분이 발송되고 html도 전달된다
    q.start()
    await q.stop()
    assert sender.sent[0]["to"] == "a@x.com" and sender.sent[0]["html"] == "<b>h</b>"
