"""서비스 알림(아웃고잉 웹훅) 발송 — 요청 016.

구독·결제·카드·요금제 상태 변화 시, 서비스가 등록한 ``notification_url``로 JSON을 POST한다.

- **best-effort(fire-and-forget)**: 실제 POST는 백그라운드 태스크로 보내고, 실패해도
  본 처리(결제·구독)에는 영향을 주지 않는다(로그만 남김).
- **서명**: 서비스의 기존 HMAC 시크릿(``hmac_secret_encrypted``)을 재사용해
  ``X-Signature``/``X-Timestamp``/``X-Nonce`` 헤더로 보낸다(수신 측이 진위 검증 가능).
- ``notification_url``이 비어 있으면 발송하지 않는다.

테스트는 ``RecordingServiceNotifier``로 발송 내역을 검사한다(실제 네트워크 없음).
"""
import asyncio
import json
import logging
import secrets
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.core.clock import kst_format, utcnow
from app.core.crypto import AesGcmCipher
from app.core.security import sign_request

logger = logging.getLogger("service_notify")

# ── 이벤트 식별자(payload의 EVENT 필드) ─────────────────────────────────────────
EVENT_SUBSCRIPTION_CREATED = "subscription.created"            # 새 구독자
EVENT_SUBSCRIPTION_STATUS = "subscription.status_changed"      # 구독 상태 변화(취소/재개/정지/미수/만료)
EVENT_SUBSCRIPTION_RENEWED = "subscription.renewed"            # 구독 자동결제
EVENT_SUBSCRIPTION_FORCE_CANCELED = "subscription.force_canceled"  # 관리자 강제 구독취소
EVENT_SUBSCRIPTION_EXTENDED = "subscription.extended"          # 만료일 연장
EVENT_CARD_REGISTERED = "card.registered"                     # 카드 등록
EVENT_CARD_REPLACED = "card.replaced"                         # 카드 변경(교체)
EVENT_CARD_DELETED = "card.deleted"                           # 카드 삭제(취소)
EVENT_CARD_ACTIVATED = "card.activated"                       # 관리자 카드 활성화
EVENT_CARD_DEACTIVATED = "card.deactivated"                   # 관리자 카드 비활성화
EVENT_PAYMENT_ONE_OFF = "payment.one_off"                     # 일반결제
EVENT_PAYMENT_ONE_OFF_CANCELED = "payment.one_off_canceled"   # 사용자 일반결제 취소
EVENT_PAYMENT_ONE_OFF_ADMIN_CANCELED = "payment.one_off_admin_canceled"  # 관리자 일반결제 취소
EVENT_PLAN_ACTIVATED = "plan.activated"                       # 요금제 활성화
EVENT_PLAN_ARCHIVED = "plan.archived"                         # 요금제 비활성화
EVENT_PLAN_DELETED = "plan.deleted"                           # 요금제 삭제
EVENT_PLAN_BONUS_DAYS = "plan.bonus_days"                     # 요금제 사용일 추가
EVENT_TEST = "notification.test"                             # 어드민 '테스트 알림 전송' 버튼


def build_payload(service, *, event: str, subscribe_id: str = "", order_id: str = "",
                  pre_status: str = "", status: str = "", email: str = "",
                  desc: str = "") -> dict:
    """요청 016 구조의 알림 payload를 만든다(없는 값은 빈 문자열, EVENT 식별자 포함)."""
    return {
        "EVENT": event,                  # 이벤트 식별자(요청 구조 확장)
        "subscribe_id": subscribe_id or "",
        "order_id": order_id or "",
        "PRE_STATUS": pre_status or "",
        "STATUS": status or "",
        "service_name": service.name,
        "email": email or "",
        "date": kst_format(utcnow(), "%Y-%m-%d %H:%M:%S"),  # 발생 년월일시간초(KST)
        "DESC": desc or "",
    }


class ServiceNotifier(Protocol):
    """서비스 알림 발송 인터페이스(실 전송 HttpServiceNotifier / 테스트 Recording)."""

    async def send(self, service, *, event: str, subscribe_id: str = "",
                   order_id: str = "", pre_status: str = "", status: str = "",
                   email: str = "", desc: str = "") -> None: ...

    async def send_test(self, service) -> "tuple[bool, str]":
        """테스트 알림을 '동기'로 보내고 (성공여부, 상세) 반환. 어드민 테스트 버튼용."""
        ...


class HttpServiceNotifier:
    """실 전송 — 서명된 JSON을 백그라운드로 POST(best-effort)."""

    def __init__(self, cipher: AesGcmCipher, *, timeout_seconds: float = 5.0) -> None:
        self._cipher = cipher                 # 서비스 HMAC 시크릿 복호화용
        self._timeout = timeout_seconds
        self._tasks: set[asyncio.Task] = set()  # 백그라운드 태스크 참조 보관(GC 방지)

    async def send(self, service, *, event: str, subscribe_id: str = "",
                   order_id: str = "", pre_status: str = "", status: str = "",
                   email: str = "", desc: str = "") -> None:
        # 전체 best-effort — payload 구성·서명·스케줄 중 어떤 예외도 본 처리를 막지 않는다.
        try:
            url = getattr(service, "notification_url", None)
            if not url:
                return  # 알림 URL 미등록 — 발송 안 함
            payload = build_payload(service, event=event, subscribe_id=subscribe_id,
                                    order_id=order_id, pre_status=pre_status, status=status,
                                    email=email, desc=desc)
            body = json.dumps(payload, ensure_ascii=False).encode()
            # 서명 — 서비스의 기존 HMAC 시크릿 재사용. path는 URL의 경로 부분.
            secret = self._cipher.decrypt(service.hmac_secret_encrypted)
            ts = str(int(utcnow().timestamp()))
            nonce = secrets.token_hex(16)
            path = urlsplit(url).path or "/"
            sig = sign_request(secret, "POST", path, ts, nonce, body)
            headers = {"Content-Type": "application/json", "X-Event": event,
                       "X-Signature": sig, "X-Timestamp": ts, "X-Nonce": nonce}
            # fire-and-forget — 실제 POST는 백그라운드. 본 처리 흐름을 막지 않는다.
            task = asyncio.create_task(self._post(url, body, headers, event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except Exception as exc:  # noqa: BLE001 — best-effort: 알림 실패가 본 처리를 깨면 안 됨
            logger.warning("서비스 알림 %s 구성 실패: %s", event, exc)

    async def _post(self, url: str, body: bytes, headers: dict, event: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, content=body, headers=headers)
            if resp.status_code >= 400:
                logger.warning("서비스 알림 %s 응답 오류: HTTP %s (%s)",
                               event, resp.status_code, url)
        except Exception as exc:  # noqa: BLE001 — best-effort: 모든 예외를 흡수(본 처리 보호)
            logger.warning("서비스 알림 %s 전송 실패: %s", event, exc)

    async def send_test(self, service) -> tuple[bool, str]:
        """테스트 알림(EVENT=notification.test)을 동기로 보내고 결과를 반환한다.

        어드민 '테스트 알림 전송' 버튼용 — 일반 send와 달리 백그라운드가 아니라
        즉시 POST해 수신 측 응답(HTTP 코드/네트워크 오류)을 운영자에게 보여준다.
        반환: (성공 여부, 사람이 읽을 상세 문자열).
        """
        url = getattr(service, "notification_url", None)
        if not url:
            return False, "알림 URL이 등록되어 있지 않습니다"
        payload = build_payload(service, event=EVENT_TEST, status="TEST",
                                desc="테스트 알림입니다(설정 확인용).")
        body = json.dumps(payload, ensure_ascii=False).encode()
        secret = self._cipher.decrypt(service.hmac_secret_encrypted)
        ts = str(int(utcnow().timestamp()))
        nonce = secrets.token_hex(16)
        path = urlsplit(url).path or "/"
        sig = sign_request(secret, "POST", path, ts, nonce, body)
        headers = {"Content-Type": "application/json", "X-Event": EVENT_TEST,
                   "X-Signature": sig, "X-Timestamp": ts, "X-Nonce": nonce}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, content=body, headers=headers)
        except Exception as exc:  # noqa: BLE001 — 네트워크 오류 등을 메시지로 전달
            return False, f"전송 실패: {exc}"
        if resp.status_code < 400:
            return True, f"수신 측 응답 HTTP {resp.status_code}"
        return False, f"수신 측 응답 오류 HTTP {resp.status_code}"


class RecordingServiceNotifier:
    """테스트용 — 보낸 알림 payload를 sent에 기록(URL 미등록 서비스는 기록 안 함)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, service, *, event: str, subscribe_id: str = "",
                   order_id: str = "", pre_status: str = "", status: str = "",
                   email: str = "", desc: str = "") -> None:
        if not getattr(service, "notification_url", None):
            return
        self.sent.append(build_payload(
            service, event=event, subscribe_id=subscribe_id, order_id=order_id,
            pre_status=pre_status, status=status, email=email, desc=desc))

    async def send_test(self, service) -> tuple[bool, str]:
        """테스트 알림을 기록하고 (성공, 상세) 반환. URL 미등록이면 실패로 보고."""
        if not getattr(service, "notification_url", None):
            return False, "알림 URL이 등록되어 있지 않습니다"
        self.sent.append(build_payload(service, event=EVENT_TEST, status="TEST",
                                       desc="테스트 알림입니다(설정 확인용)."))
        return True, "기록됨(테스트)"
