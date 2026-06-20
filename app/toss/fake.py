"""테스트·개발용 토스페이먼트 가짜(Fake) 클라이언트.

실제 토스 HTTP 호출 없이 단위/통합 테스트를 가능하게 한다.
FakeTossClient는 실제 TossClient와 동일한 인터페이스를 구현하며,
아래 기능을 제공한다.

- **호출 기록**: issued / charges / deleted / canceled 리스트에 모든 호출 인수를 저장해
  테스트에서 호출 여부·인수를 검증할 수 있다.
- **상시 실패 주입**: fail_issue_with / fail_charge_with / fail_cancel_with 등을 설정하면
  해당 메서드가 항상 지정된 예외를 발생시킨다.
- **소진형 실패 큐**: charge_failure_queue에 예외를 넣으면 앞에서부터 1개씩
  소진하며 실패한다. 큐가 비면 이후 호출은 정상 처리된다.
- **타임아웃 후 성공 시나리오**: succeed_despite_timeout=True 상태에서
  TossTimeoutError를 큐에 넣으면, 응답은 타임아웃이지만 토스 내부에서는
  승인이 완료된 상황을 재현한다. 재조회(get_payment_by_order_id)가
  올바로 동작하는지 검증하는 데 사용한다.
- **멱등키 충실도**: 같은 orderId에 같은 멱등키로 재시도하면 첫 응답을
  그대로 재생하고, 다른 멱등키로 시도하면 ALREADY_PROCESSED_PAYMENT 오류를
  발생시켜 실제 토스 동작을 재현한다.
"""

import itertools

from app.toss.errors import TossError, TossTimeoutError
from app.toss.types import BillingKeyResult, ChargeResult


class FakeTossClient:
    """테스트용 토스 클라이언트. 호출 기록 + 실패 주입."""

    def __init__(self) -> None:
        self.issued: list[dict] = []
        self.charges: list[dict] = []
        self.deleted: list[str] = []
        self.canceled: list[dict] = []                        # cancel_payment 호출 기록
        self.fail_issue_with: TossError | None = None
        self.fail_charge_with: TossError | None = None       # 상시 실패
        self.fail_lookup_with: TossError | None = None       # 재조회 실패 주입
        self.fail_delete_with: TossError | None = None       # 빌링키 삭제 실패 주입
        self.fail_cancel_with: TossError | None = None        # 취소 실패 주입
        self.charge_failure_queue: list[TossError] = []      # 소진형 실패(앞에서부터 1회씩)
        self.succeed_despite_timeout: bool = False           # 타임아웃이지만 실제 승인된 상황 재현
        self.payments_by_order: dict[str, ChargeResult] = {}  # get_payment_by_order_id 응답
        self._idem_by_order: dict[str, str] = {}             # 멱등키 재시도 충실도용
        self._seq = itertools.count(1)

    @staticmethod
    def _result_for(order_id: str, amount: int) -> ChargeResult:
        """orderId·금액으로 성공 ChargeResult를 생성하는 내부 팩토리."""
        return ChargeResult(payment_key=f"pay_{order_id}", order_id=order_id,
                            status="DONE", approved_at="2026-06-05T10:00:00+09:00",
                            raw={"paymentKey": f"pay_{order_id}", "orderId": order_id,
                                 "status": "DONE", "totalAmount": amount})

    async def issue_billing_key(self, auth_key: str, customer_key: str) -> BillingKeyResult:
        """빌링키를 발급하고 issued에 기록한다. fail_issue_with가 설정되면 즉시 예외를 발생시킨다."""
        if self.fail_issue_with is not None:
            raise self.fail_issue_with
        billing_key = f"bk_{next(self._seq)}"
        self.issued.append({"auth_key": auth_key, "customer_key": customer_key,
                            "billing_key": billing_key})
        return BillingKeyResult(
            billing_key=billing_key, method="카드",
            card={"number": "1234-****-****-5678", "issuerCode": "61"},
            raw={"billingKey": billing_key})

    async def charge(self, billing_key: str, customer_key: str, amount: int,
                     order_id: str, order_name: str, idempotency_key: str) -> ChargeResult:
        """자동결제를 청구한다. 실패 큐·멱등키·타임아웃 시나리오를 순서대로 처리한다."""
        self.charges.append({"billing_key": billing_key, "customer_key": customer_key,
                             "amount": amount, "order_id": order_id,
                             "order_name": order_name, "idempotency_key": idempotency_key})
        # 실제 토스 충실도: 같은 멱등키 재시도는 첫 응답 재생, 다른 키로 같은 주문은 거부
        if order_id in self.payments_by_order:
            if self._idem_by_order.get(order_id) == idempotency_key:
                return self.payments_by_order[order_id]
            raise TossError("ALREADY_PROCESSED_PAYMENT", "이미 처리된 주문입니다", 400)
        if self.charge_failure_queue:
            error = self.charge_failure_queue.pop(0)
            if self.succeed_despite_timeout and isinstance(error, TossTimeoutError):
                # 타임아웃으로 응답은 못 받았지만 토스 쪽에선 승인된 케이스
                self.payments_by_order[order_id] = self._result_for(order_id, amount)
                self._idem_by_order[order_id] = idempotency_key
            raise error
        if self.fail_charge_with is not None:
            raise self.fail_charge_with
        result = self._result_for(order_id, amount)
        self.payments_by_order[order_id] = result
        self._idem_by_order[order_id] = idempotency_key
        return result

    async def get_payment_by_order_id(self, order_id: str) -> ChargeResult | None:
        """orderId로 결제를 재조회한다. 타임아웃 후 승인 여부 확인 시 호출된다."""
        if self.fail_lookup_with is not None:
            raise self.fail_lookup_with
        return self.payments_by_order.get(order_id)

    async def delete_billing_key(self, billing_key: str) -> None:
        """빌링키를 삭제하고 deleted에 기록한다. fail_delete_with가 설정되면 예외를 발생시킨다."""
        if self.fail_delete_with is not None:
            raise self.fail_delete_with
        self.deleted.append(billing_key)

    async def cancel_payment(self, payment_key: str, reason: str,
                             *, cancel_amount: int | None = None) -> dict:
        """결제를 취소하고 canceled에 기록한다. fail_cancel_with가 설정되면 예외를 발생시킨다.

        cancel_amount 지정 시 부분취소(환불액 명시), 생략 시 전액취소.
        반환값은 HttpTossClient와 동일한 raw dict 구조로 맞춘다.
        """
        if self.fail_cancel_with is not None:
            raise self.fail_cancel_with
        self.canceled.append({"payment_key": payment_key, "reason": reason,
                              "cancel_amount": cancel_amount})
        return {"paymentKey": payment_key, "status": "CANCELED",
                "cancelAmount": cancel_amount}
