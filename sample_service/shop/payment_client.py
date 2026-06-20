"""구독서버(payment_system) API 클라이언트 — 외부 서비스와 동일한 3중 인증 경로.

서명 형식은 payment_system app/core/security.py:sign_request의 미러:
HMAC_SHA256(secret, "METHOD\n{path}\n{timestamp}\n{nonce}\n{sha256_hex(body)}")

creds=(api_key, hmac_secret) 인자로 활성 서비스 자격증명을 전달하면 해당 키로 서명.
None이면 settings.SERVICE_* 폴백(단일 서비스 하위호환).
"""
import hashlib
import hmac
import json
import time
import uuid

import requests
from django.conf import settings


def sign_request(secret: str, method: str, path: str, timestamp: str,
                 nonce: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    message = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


class PaymentAPIError(Exception):
    """구독서버 에러 응답({"error": {code, message}})."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _request(method: str, path: str, json_body: dict | None = None,
             creds: tuple[str, str] | None = None) -> dict:
    """HTTP 요청 후 JSON 반환.

    creds=(api_key, hmac_secret) 지정 시 그 키로 서명.
    None이면 settings 폴백(단일 서비스 하위호환).
    """
    # 활성 서비스 자격증명 우선, 없으면 settings 폴백
    api_key, hmac_secret = creds if creds else (settings.SERVICE_API_KEY, settings.SERVICE_HMAC_SECRET)
    body = b""
    if json_body is not None:
        body = json.dumps(json_body).encode()
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    # 3중 인증 헤더: 서비스 키 + 타임스탬프 + nonce + HMAC 서명
    headers = {
        "x-service-key": api_key,
        "x-timestamp": timestamp,
        "x-nonce": nonce,
        "x-signature": sign_request(hmac_secret, method, path, timestamp, nonce, body),
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    resp = requests.request(method, settings.PAYMENT_API_BASE + path,
                            headers=headers, data=body or None, timeout=30)
    if resp.status_code >= 400:
        try:
            err = resp.json()["error"]
            raise PaymentAPIError(resp.status_code, err["code"], err["message"])
        except (ValueError, KeyError):
            raise PaymentAPIError(resp.status_code, "UNKNOWN",
                                  resp.text[:200]) from None
    return resp.json()


def list_services() -> list[dict]:
    """서버의 서비스 목록(id/name/status) — 무인증, GET /api/v1/services.

    인증 헤더 없이 호출하는 공개 엔드포인트. 서비스 선택 화면에서 사용.
    """
    # list_services는 무인증이지만 _request 인터페이스를 통해 호출
    # (settings 폴백 키가 설정되지 않아도 서버 측에서 무인증 처리)
    return _request("GET", "/api/v1/services")["services"]


def get_plans(creds=None) -> list[dict]:
    """서비스의 요금제 목록 — GET /api/v1/plans."""
    return _request("GET", "/api/v1/plans", creds=creds)["plans"]


# ─────────────────────────────────────────────
# 카드 보관함(Card Vault) — 카드 등록/조회/삭제
# 서버가 카드 보관함 모델로 전환되어, 구독·결제는 사전 등록한 카드를 사용한다.
# 카드 재등록(POST /cards)이 곧 "카드 변경"이다(별도 구독 엔드포인트 불필요).
# ─────────────────────────────────────────────

def register_card(*, external_user_id: str, customer_key: str, auth_key: str,
                  creds=None) -> dict:
    """카드 등록 또는 교체 — POST /api/v1/cards.

    토스 SDK 빌링 인증(requestBillingAuth)에서 받은 authKey로 빌링키를 발급·보관한다.
    (service, external_user_id) 당 카드 1건만 유지하며, 다시 호출하면 기존 카드를
    교체한다(이것이 "카드 변경"). 응답에는 마스킹된 카드 정보만 포함된다(billingKey 미반환).
    """
    return _request("POST", "/api/v1/cards", {
        "external_user_id": external_user_id, "customer_key": customer_key,
        "auth_key": auth_key},
        creds=creds)


def get_card(external_user_id: str, creds=None) -> dict:
    """등록 카드 조회 — GET /api/v1/cards/{external_user_id}.

    등록된 카드의 마스킹 정보를 반환한다. 카드가 없으면 서버가 404를 반환한다.
    """
    return _request("GET", f"/api/v1/cards/{external_user_id}", creds=creds)


def delete_card(external_user_id: str, creds=None) -> None:
    """등록 카드 삭제 — DELETE /api/v1/cards/{external_user_id}.

    빌링키가 사용 중인 구독(TRIAL/ACTIVE/PAST_DUE/SUSPENDED 등)이 있으면 서버가
    409(CONFLICT)를 반환한다. 카드가 없으면 404. 성공 시 204(본문 없음).
    """
    _request("DELETE", f"/api/v1/cards/{external_user_id}", creds=creds)


def create_subscription(*, plan_id: str, external_user_id: str, trial: bool,
                        creds=None) -> dict:
    """구독 생성 — POST /api/v1/subscriptions.

    카드 보관함 전환(서버 Task 7): auth_key/customer_key 불필요. 구독 전에 카드를
    먼저 등록해야 하며(POST /cards), 서버가 등록된 카드의 빌링키로 첫 결제를 처리한다.
    카드가 없으면 서버가 404를 반환한다.
    """
    return _request("POST", "/api/v1/subscriptions", {
        "plan_id": plan_id, "external_user_id": external_user_id, "trial": trial},
        creds=creds)


def create_one_off_payment(*, order_id: str, order_name: str, amount: int,
                           external_user_id: str, creds=None) -> dict:
    """구독과 무관한 단건(일반) 결제 — POST /api/v1/payments.

    카드 보관함 전환(서버 Task 9): auth_key/customer_key 불필요. 사전 등록된 카드
    (POST /cards)의 빌링키를 서버가 자동 조회해 즉시 1회 청구한다. 카드 미등록 시 404.
    금액은 이 서비스가 정한 값(서명된 본문으로 전달 — successUrl 변조와 무관).
    """
    return _request("POST", "/api/v1/payments", {
        "order_id": order_id, "order_name": order_name, "amount": amount,
        "external_user_id": external_user_id},
        creds=creds)


def get_subscription(external_user_id: str, creds=None) -> dict:
    """구독 정보 조회 — GET /api/v1/subscriptions/{external_user_id}."""
    return _request("GET", f"/api/v1/subscriptions/{external_user_id}", creds=creds)


def cancel(external_user_id: str, creds=None) -> dict:
    """구독 취소 — POST /api/v1/subscriptions/{external_user_id}/cancel."""
    return _request("POST", f"/api/v1/subscriptions/{external_user_id}/cancel",
                    creds=creds)


def resume(external_user_id: str, creds=None) -> dict:
    """구독 재개 — POST /api/v1/subscriptions/{external_user_id}/resume."""
    return _request("POST", f"/api/v1/subscriptions/{external_user_id}/resume",
                    creds=creds)


def manual_pay(external_user_id: str, creds=None) -> dict:
    """수동 결제 — POST /api/v1/subscriptions/{external_user_id}/pay."""
    return _request("POST", f"/api/v1/subscriptions/{external_user_id}/pay",
                    creds=creds)


# 카드 변경(change_card)은 제거됨 — 서버 Task 10에서 change-card 엔드포인트가 삭제되고
# 카드 보관함 재등록(register_card → POST /api/v1/cards)으로 통합되었다.
# 카드를 다시 등록하면 기존 구독이 자동으로 새 카드를 참조한다(별도 호출 불필요).


def add_usage_days(external_user_id: str, days: int, creds=None) -> dict:
    """구독 사용일 추가 — POST /api/v1/subscriptions/{external_user_id}/add-days.

    이용 중(ACTIVE/EXTENDED/PAST_DUE) 구독의 만료일·다음 결제일을 days만큼 미룬다(상태 유지).
    대상 상태가 아니면 서버가 409(CONFLICT)를 반환한다.
    """
    return _request("POST", f"/api/v1/subscriptions/{external_user_id}/add-days",
                    {"days": days}, creds=creds)


def cancel_one_off_payment(order_id: str, reason: str = "사용자 취소",
                           creds=None) -> dict:
    """단건 결제 취소 — POST /api/v1/payments/{order_id}/cancel.

    서비스 취소 정책(cancellation_enabled)이 꺼진 경우 서버가 CANCEL_DISABLED 에러를
    반환하고 PaymentAPIError가 발생한다. 수수료가 있으면 서버 측에서 환불액을 계산한다.
    """
    return _request("POST", f"/api/v1/payments/{order_id}/cancel", {"reason": reason},
                    creds=creds)


def get_payments(external_user_id: str, creds=None) -> list[dict]:
    """결제 내역 조회 — GET /api/v1/payments/{external_user_id}.

    구독 정기결제와 단건(ONE_OFF) 결제를 **모두** 반환한다(kind로 구분).
    각 결제에는 취소 수수료 안내 필드가 포함된다:
      cancelable, cancel_fee_percent, cancel_fee, cancel_refund_amount
    (취소 가능 결제는 '취소 시 예상' 값, 이미 취소된 결제는 실제 차감/환불액)
    """
    return _request("GET", f"/api/v1/payments/{external_user_id}",
                    creds=creds)["payments"]
