"""토스페이먼트 API 응답을 담는 DTO(Data Transfer Object) 정의.

토스 API의 JSON 응답에서 서비스 로직에 필요한 필드만 추려 타입 안전하게
전달하기 위해 dataclass로 정의한다. 원본 JSON은 raw 필드에 보존한다.
"""

from dataclasses import dataclass, field


@dataclass
class BillingKeyResult:
    """빌링키 발급 결과 DTO.

    토스 POST /v1/billing/authorizations/issue 응답을 파싱한 결과.
    billing_key는 이후 자동결제(charge) 호출에 사용되는 핵심 식별자이며,
    암호화하여 DB에 저장한다.

    Attributes:
        billing_key: 자동결제에 사용할 빌링키 문자열.
        method: 결제수단 (예: "카드"). 없을 수 있음.
        card: 카드 정보 딕셔너리 (카드번호 마스킹, 발급사 코드 등). 없을 수 있음.
        raw: 토스 원본 JSON 응답 전체 (감사·디버깅용).
    """

    billing_key: str
    method: str | None
    card: dict | None
    raw: dict = field(default_factory=dict)


@dataclass
class ChargeResult:
    """자동결제 청구 결과 DTO.

    토스 POST /v1/billing/{billingKey} 응답을 파싱한 결과.
    status가 "DONE"이면 승인 완료. 그 외 값(예: "ABORTED")은 실패를 의미한다.

    Attributes:
        payment_key: 토스가 발급한 결제 고유키 (환불·조회에 사용).
        order_id: 요청 시 전달한 주문 ID (서비스 측 멱등성 키로도 활용).
        status: 결제 상태 문자열. "DONE" = 승인 완료.
        approved_at: 승인 시각 ISO 8601 문자열 (KST 오프셋 포함). 미승인이면 None.
        raw: 토스 원본 JSON 응답 전체 (감사·디버깅용).
    """

    payment_key: str
    order_id: str
    status: str
    approved_at: str | None = None
    raw: dict = field(default_factory=dict)
