"""인메모리 이메일 발송 큐 — 메모리에 먼저 적재하고 순서대로(FIFO) 한 건씩 발송한다.

요청: "이메일 전송은 메모리에 우선 담고 순서대로 보내라" + "이메일 전송 관련 감사로그를 반드시 남겨라".

구성
- EmailQueue        : asyncio.Queue(무제한)에 작업을 적재하고, **단일 워커 태스크**가
                      한 건씩 순차 발송한다(동시 발송 없음 → 순서 보장). 발송 직후
                      성공/실패를 **감사로그(audit_logs)** 에 기록한다(action=email.sent/email.failed).
- QueuedEmailSender : EmailSender 프로토콜 어댑터. send()가 실제 발송 대신 큐에 적재하고
                      즉시 True(적재 성공)를 반환한다 → 호출 측(요청 핸들러)은 발송 완료를
                      기다리지 않는다. 실제 전송은 워커가 백그라운드에서 순서대로 수행.

발송 실패는 핵심 처리(계정/구독 등)와 분리되어 있으므로 로깅·감사기록만 하고 흡수한다.
워커는 어떤 예외에도 죽지 않는다(루프 유지).
"""
import asyncio
import logging
from dataclasses import dataclass

from app.notifications.email import EmailSender
from app.services.audit import record_audit

logger = logging.getLogger("email_queue")

# 감사로그 action 식별자 — 이메일 전송 결과
ACTION_EMAIL_SENT = "email.sent"
ACTION_EMAIL_FAILED = "email.failed"


@dataclass
class _EmailJob:
    """큐에 적재되는 단일 이메일 작업."""
    to: str
    subject: str
    body: str
    html: str | None = None


# 워커 정상 종료 신호(이 값을 큐에서 받으면 남은 항목을 비우고 루프 종료)
_SENTINEL = object()


class EmailQueue:
    """메모리 FIFO 큐 + 단일 순차 워커 + 발송 감사로그.

    session_factory는 워커가 감사로그를 쓸 때 요청과 무관한 새 DB 세션을 열기 위함이다
    (발송은 요청 종료 후 백그라운드에서 일어나므로 요청 세션을 쓸 수 없다).
    """

    def __init__(self, sender: EmailSender, session_factory) -> None:
        self._sender = sender
        self._session_factory = session_factory
        self._queue: asyncio.Queue = asyncio.Queue()  # 무제한(maxsize=0)
        self._task: asyncio.Task | None = None

    def enqueue(self, to: str, subject: str, body: str,
                html: str | None = None) -> bool:
        """이메일을 메모리 큐에 적재한다(즉시 반환). 적재 성공 시 True."""
        try:
            self._queue.put_nowait(_EmailJob(to=to, subject=subject, body=body, html=html))
            return True
        except Exception as exc:  # noqa: BLE001 — 적재 실패가 본 처리를 깨면 안 됨
            logger.warning("이메일 큐 적재 실패 to=%s subject=%s: %s", to, subject, exc)
            return False

    def start(self) -> None:
        """순차 발송 워커를 기동한다(앱 시작 시 1회)."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        """큐에서 한 건씩 꺼내 순서대로 발송하고 결과를 감사로그에 남긴다."""
        while True:
            job = await self._queue.get()
            try:
                if job is _SENTINEL:
                    self._queue.task_done()
                    break  # 종료 신호 — 앞선 항목은 모두 처리된 뒤다(FIFO)
                await self._send_one(job)
            finally:
                if job is not _SENTINEL:
                    self._queue.task_done()

    async def _send_one(self, job: _EmailJob) -> None:
        """한 건 발송 + 감사로그(어떤 예외도 워커를 멈추지 않는다)."""
        ok = False
        try:
            ok = await self._sender.send(job.to, job.subject, job.body, job.html)
        except Exception:  # noqa: BLE001 — 발송 예외 흡수(워커 유지)
            logger.exception("이메일 발송 예외 to=%s subject=%s", job.to, job.subject)
            ok = False
        await self._audit(job, ok)

    async def _audit(self, job: _EmailJob, ok: bool) -> None:
        """이메일 전송 결과를 감사로그에 기록한다(발송과 별개로 best-effort)."""
        try:
            async with self._session_factory() as db:
                await record_audit(
                    db, actor_type="SYSTEM",
                    action=ACTION_EMAIL_SENT if ok else ACTION_EMAIL_FAILED,
                    target_type="email", target_id=job.to,
                    detail={"to": job.to, "subject": job.subject, "ok": ok})
                await db.commit()
        except Exception:  # noqa: BLE001 — 감사기록 실패가 워커를 멈추면 안 됨
            logger.exception("이메일 감사로그 기록 실패 to=%s", job.to)

    async def stop(self, *, drain_timeout: float = 10.0) -> None:
        """워커를 정상 종료한다 — 적재된(센티넬 이전) 이메일은 모두 발송 후 종료.

        drain_timeout 안에 비우지 못하면 워커를 취소한다(미발송분은 로깅).
        """
        if self._task is None:
            return
        self._queue.put_nowait(_SENTINEL)  # 남은 큐를 비우고 종료하도록 신호
        try:
            await asyncio.wait_for(self._task, timeout=drain_timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            remaining = self._queue.qsize()
            logger.warning("이메일 큐 종료 타임아웃 — 미발송 %d건", remaining)
            self._task.cancel()
        finally:
            self._task = None


class QueuedEmailSender:
    """EmailSender 어댑터 — send()가 실제 발송 대신 큐에 적재하고 즉시 반환한다."""

    def __init__(self, queue: EmailQueue) -> None:
        self._queue = queue

    async def send(self, to: str, subject: str, body: str,
                   html: str | None = None) -> bool:
        # 메모리 큐에 적재만 하고 즉시 반환 — 실제 전송은 워커가 순서대로 수행한다.
        return self._queue.enqueue(to, subject, body, html)
