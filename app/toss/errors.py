"""토스페이먼트 API 호출에서 발생하는 예외 계층.

TossError   : 토스 서버가 명시적 오류 응답을 반환한 경우 (HTTP 4xx/5xx).
TossTimeoutError : 네트워크 단절·타임아웃 — 결제 승인 여부 자체가 불명확한
                   '결과 미확정' 케이스. 이 예외를 잡은 쪽은 반드시 orderId로
                   get_payment_by_order_id를 재조회하여 실제 승인 여부를 확인한
                   뒤 후속 처리(성공/실패/재시도)를 결정해야 한다.
"""


class TossError(Exception):
    """토스 API 에러 응답.

    토스 서버가 JSON 에러 바디(code, message)를 반환하거나,
    클라이언트 측에서 의미 있는 오류 코드를 할당할 때 사용한다.

    Attributes:
        code: 토스가 반환한 에러 코드 문자열 (예: "NOT_FOUND_PAYMENT").
        message: 사람이 읽을 수 있는 한국어 오류 설명.
        http_status: 토스 응답의 HTTP 상태 코드. 네트워크 오류 등으로
                     HTTP 응답 자체가 없으면 0.
    """

    def __init__(self, code: str, message: str, http_status: int = 0) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.http_status = http_status


class TossTimeoutError(TossError):
    """타임아웃/네트워크 단절 — 결제 성공 여부 불명. orderId 재조회 필요.

    이 예외는 "토스가 실패했다"가 아니라 "결과를 알 수 없다"를 의미한다.
    호출자는 예외를 잡으면 즉시 실패 처리하지 말고, orderId로 결제를 조회해
    DONE/CANCELED 등의 최종 상태를 확인한 후 처리해야 한다.
    (미확인 상태에서 재청구하면 이중 과금이 발생할 수 있다.)
    """

    def __init__(self, message: str = "토스 API 응답 시간 초과") -> None:
        super().__init__("TIMEOUT", message, 0)
