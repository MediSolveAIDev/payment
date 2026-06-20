"""토스페이먼츠 클라이언트 — Protocol 인터페이스 + HTTP 구현체.

TossClient: 타입 힌트 전용 Protocol. 테스트에서 FakeTossClient 등으로
DI를 교체할 수 있도록 인터페이스를 명시적으로 분리한다.

HttpTossClient: 실제 HTTP 요청을 수행하는 구현체. secret_key를 Basic
인증 헤더로 인코딩해 모든 요청에 적용한다. 자동결제 승인(charge)은
토스 명세상 최대 60초가 소요될 수 있어 read timeout을 65초로 설정한다.
"""

import base64
from typing import Protocol
from urllib.parse import quote

import httpx

from app.core.config import default_settings
from app.toss.errors import TossError, TossTimeoutError
from app.toss.types import BillingKeyResult, ChargeResult


class TossClient(Protocol):
    """토스페이먼츠 API 메서드 인터페이스 — 테스트 DI 교체용 Protocol."""

    async def issue_billing_key(self, auth_key: str, customer_key: str) -> BillingKeyResult: ...

    async def charge(self, billing_key: str, customer_key: str, amount: int,
                     order_id: str, order_name: str, idempotency_key: str) -> ChargeResult: ...

    async def get_payment_by_order_id(self, order_id: str) -> ChargeResult | None: ...

    async def delete_billing_key(self, billing_key: str) -> None: ...

    async def cancel_payment(self, payment_key: str, reason: str,
                             *, cancel_amount: int | None = None) -> dict: ...


def _charge_result(data: dict) -> ChargeResult:
    return ChargeResult(
        payment_key=data.get("paymentKey", ""),
        order_id=data.get("orderId", ""),
        status=data.get("status", ""),
        approved_at=data.get("approvedAt"),
        raw=data,
    )


class HttpTossClient:
    """토스페이먼츠 코어 API 클라이언트. 자동결제 승인은 최대 60초(명세)."""

    def __init__(self, secret_key: str, base_url: str = "https://api.tosspayments.com") -> None:
        token = base64.b64encode(f"{secret_key}:".encode()).decode()
        # 자동결제 승인은 최대 60초(토스 명세) — read에 여유를 두고 connect는 짧게.
        # .env(toss_read_timeout_seconds / toss_connect_timeout_seconds)로 조정 가능.
        _s = default_settings()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Basic {token}"},
            timeout=httpx.Timeout(_s.toss_read_timeout_seconds,
                                  connect=_s.toss_connect_timeout_seconds),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, json: dict | None = None,
                       idempotency_key: str | None = None) -> dict:
        """토스 API에 HTTP 요청을 보내고 응답 dict를 반환한다.

        - TimeoutException → TossTimeoutError: 결과 불명 상태를 상위로 전파.
        - 4xx/5xx → TossError: 토스 에러 코드·메시지·HTTP 상태를 포함.
        - 빈 응답(204 등) → {}: 본문 없는 성공(예: 빌링키 삭제)을 정상 처리.
        - 2xx이지만 JSON 파싱 실패(프록시 오류 등) → TossTimeoutError:
          HTTP 200이 왔어도 실제 처리 결과를 알 수 없으므로 타임아웃과
          동일하게 취급해 호출측이 orderId 재조회로 결과를 확정하게 한다.
          절대 FAILED 처리해선 안 된다(이중 결제 위험).
        """
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            resp = await self._client.request(method, path, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise TossTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise TossError("NETWORK_ERROR", str(exc)) from exc
        if resp.status_code >= 400:
            try:
                err = resp.json()
            except ValueError:
                err = {}
            raise TossError(err.get("code", "UNKNOWN"),
                            err.get("message", "토스 API 오류"), resp.status_code)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            # 2xx인데 본문을 해석할 수 없음(프록시 오류 등) — 처리 결과 불명.
            # TossTimeoutError로 매핑해 호출측이 orderId 재조회로 확정하게 한다.
            raise TossTimeoutError("토스 응답을 해석할 수 없습니다 — 처리 결과 불명") from exc

    async def issue_billing_key(self, auth_key: str, customer_key: str) -> BillingKeyResult:
        """카드 인증 결과(auth_key)로 자동결제용 빌링키를 발급한다.

        발급된 빌링키는 암호화해 DB에 보관하고, 이후 charge() 호출에 재사용된다.
        """
        data = await self._request("POST", "/v1/billing/authorizations/issue",
                                   json={"authKey": auth_key, "customerKey": customer_key})
        return BillingKeyResult(billing_key=data["billingKey"], method=data.get("method"),
                                card=data.get("card"), raw=data)

    async def charge(self, billing_key: str, customer_key: str, amount: int,
                     order_id: str, order_name: str, idempotency_key: str) -> ChargeResult:
        """보관된 빌링키로 자동결제(카드 재청구)를 실행한다.

        idempotency_key는 토스 서버에 전달되어 네트워크 재시도 시
        이중 결제를 방지한다.
        """
        data = await self._request(
            "POST", f"/v1/billing/{quote(billing_key, safe='')}",
            json={"amount": amount, "customerKey": customer_key,
                  "orderId": order_id, "orderName": order_name},
            idempotency_key=idempotency_key)
        return _charge_result(data)

    async def get_payment_by_order_id(self, order_id: str) -> ChargeResult | None:
        """order_id로 결제 정보를 조회한다.

        타임아웃 후 결과 불명 상태에서 실제 처리 결과를 확정할 때 사용한다.
        토스가 404를 반환하면 결제 미처리로 간주해 None을 반환한다.
        """
        try:
            data = await self._request("GET", f"/v1/payments/orders/{quote(order_id, safe='')}")
        except TossError as exc:
            if exc.http_status == 404:
                return None
            raise
        return _charge_result(data)

    async def delete_billing_key(self, billing_key: str) -> None:
        """빌링키를 토스 서버에서 삭제(비활성화)한다.

        단건 결제 완료 후, 또는 구독 카드 변경 시 구 빌링키를 정리할 때 호출한다.
        실패해도 업무 흐름을 중단하지 않도록 호출측에서 best-effort 처리한다.
        """
        await self._request("DELETE", f"/v1/billing/{quote(billing_key, safe='')}")

    async def cancel_payment(self, payment_key: str, reason: str,
                             *, cancel_amount: int | None = None) -> dict:
        """승인된 결제를 취소(환불)한다. cancel_amount 지정 시 부분취소.

        POST /v1/payments/{paymentKey}/cancel. 부분취소면 cancelAmount(환불액)를 보낸다.
        전액취소(cancel_amount=None)는 cancelReason만 전송한다.
        _request가 raw dict를 반환하므로 그대로 반환한다(charge와 달리 _charge_result로 감싸지 않음).
        """
        body: dict = {"cancelReason": reason}
        if cancel_amount is not None:
            body["cancelAmount"] = cancel_amount
        return await self._request(
            "POST", f"/v1/payments/{quote(payment_key, safe='')}/cancel", json=body)
