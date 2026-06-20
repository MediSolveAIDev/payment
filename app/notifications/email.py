"""이메일 발송 추상화 계층과 구현체.

EmailSender Protocol을 중심으로 아래 세 구현체를 제공한다.

- ConsoleEmailSender : 개발/로컬 — 실제 발송 없이 로그만 출력.
- GmailEmailSender   : 운영 — Gmail SMTP(STARTTLS)로 실제 발송.
                       동기 smtplib을 asyncio.to_thread로 오프로드해
                       이벤트 루프를 블로킹하지 않는다.
                       발송 실패는 결제 등 핵심 트랜잭션에 영향을 주면 안 되므로
                       예외를 잡아 로깅만 하고 False를 반환한다.
- RecordingEmailSender: 테스트 — 발송 내역을 메모리에 기록하고
                        fail=True로 실패를 시뮬레이션한다.

의존성 주입 시 EmailSender Protocol 타입 힌트를 사용하면
구현체를 바꿔도 호출 측 코드를 수정할 필요가 없다.
"""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

logger = logging.getLogger("payment.email")


class EmailSender(Protocol):
    """이메일 발송 인터페이스.

    send()는 발송 성공 시 True, 실패(예외 포함) 시 False를 반환한다.
    구현체는 예외를 외부로 전파하지 않아야 하며, 발송 실패가 호출 측의
    트랜잭션(결제, 계정 생성 등)을 중단시키지 않도록 한다.
    """

    async def send(self, to: str, subject: str, body: str) -> bool: ...


class ConsoleEmailSender:
    """개발/로컬용 — 콘솔(로그)로 출력. 운영 SMTP 구현체는 추후 교체."""

    async def send(self, to: str, subject: str, body: str) -> bool:
        logger.info("EMAIL to=%s subject=%s\n%s", to, subject, body)
        return True


class GmailEmailSender:
    """Gmail SMTP(STARTTLS) 발송. 앱 비밀번호 사용.

    동기 smtplib을 스레드로 오프로드해 이벤트 루프를 막지 않는다. 발송 실패가
    핵심 트랜잭션(결제 등)을 중단시키면 안 되므로 예외는 로깅만 하고 삼킨다.
    """

    def __init__(self, *, host: str, port: int, username: str, password: str,
                 from_name: str = "결제시스템") -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_name = from_name

    def _send_sync(self, to: str, subject: str, body: str) -> None:
        """동기 SMTP 발송. asyncio.to_thread에서 별도 스레드로 실행된다."""
        msg = EmailMessage()
        msg["From"] = formataddr((self._from_name, self._username))
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP(self._host, self._port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(self._username, self._password)
            smtp.send_message(msg)

    async def send(self, to: str, subject: str, body: str) -> bool:
        """이메일을 비동기로 발송한다. 예외는 모두 잡아 False를 반환하므로 호출 측이 중단되지 않는다."""
        try:
            await asyncio.to_thread(self._send_sync, to, subject, body)
            logger.info("EMAIL sent to=%s subject=%s", to, subject)
            return True
        except Exception:  # noqa: BLE001 — 발송 실패가 결제/계정 흐름을 깨면 안 됨
            logger.exception("EMAIL 발송 실패 to=%s subject=%s", to, subject)
            return False


class RecordingEmailSender:
    """테스트용 — 발송 내역 기록. fail=True면 발송 실패 시뮬레이션."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail = False

    async def send(self, to: str, subject: str, body: str) -> bool:
        if self.fail:
            return False
        self.sent.append({"to": to, "subject": subject, "body": body})
        return True
