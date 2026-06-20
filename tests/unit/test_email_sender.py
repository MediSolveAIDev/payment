from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.main import _default_email_sender
from app.notifications.email import ConsoleEmailSender, GmailEmailSender, RecordingEmailSender


def _settings(**kw) -> Settings:
    # gmail_id/pw를 빈값으로 명시 — .env의 실제 자격증명이 새지 않도록 차단
    base = dict(environment="test", encryption_key="x", database_url="d", redis_url="r",
                gmail_id="", gmail_pw="")
    base.update(kw)
    return Settings(**base)


def test_selects_gmail_when_credentials_set():
    sender = _default_email_sender(_settings(gmail_id="a@gmail.com", gmail_pw="app-pw"))
    assert isinstance(sender, GmailEmailSender)


def test_selects_console_when_no_credentials():
    assert isinstance(_default_email_sender(_settings()), ConsoleEmailSender)
    # 한쪽만 있으면 콘솔(부분 설정은 발송하지 않음)
    assert isinstance(_default_email_sender(_settings(gmail_id="a@gmail.com")),
                      ConsoleEmailSender)


async def test_gmail_send_builds_message_and_uses_starttls():
    sender = GmailEmailSender(host="smtp.gmail.com", port=587,
                              username="me@gmail.com", password="app-pw",
                              from_name="결제시스템")
    smtp = MagicMock()
    smtp_ctx = MagicMock()
    smtp_ctx.__enter__.return_value = smtp
    with patch("app.notifications.email.smtplib.SMTP", return_value=smtp_ctx) as smtp_cls:
        result = await sender.send("user@x.com", "제목", "본문 내용")

    assert result is True
    smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("me@gmail.com", "app-pw")
    sent_msg = smtp.send_message.call_args[0][0]
    assert sent_msg["To"] == "user@x.com"
    assert sent_msg["Subject"] == "제목"
    assert "me@gmail.com" in sent_msg["From"]
    assert sent_msg.get_content().strip() == "본문 내용"


async def test_gmail_send_swallows_errors():
    """발송 실패가 호출자(결제/계정 흐름)를 깨뜨리지 않는다 — False만 반환."""
    sender = GmailEmailSender(host="h", port=587, username="u", password="p")
    with patch("app.notifications.email.smtplib.SMTP", side_effect=OSError("conn refused")):
        result = await sender.send("user@x.com", "제목", "본문")  # 예외 전파 없음
    assert result is False


async def test_console_sender_returns_true():
    assert await ConsoleEmailSender().send("u@x.com", "s", "b") is True


async def test_recording_sender_returns_true_and_records():
    sender = RecordingEmailSender()
    assert await sender.send("u@x.com", "s", "b") is True
    assert len(sender.sent) == 1


async def test_recording_sender_fail_flag_returns_false_without_recording():
    sender = RecordingEmailSender()
    sender.fail = True
    assert await sender.send("u@x.com", "s", "b") is False
    assert sender.sent == []
