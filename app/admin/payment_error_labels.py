"""결제 실패 코드 → 한글 의미 매핑 (어드민 결제 내역 툴팁용).

Payment.failure_code에 저장되는 값은 (1) 토스페이먼츠 결제/빌링 승인 에러코드
(docs/toss/2.API/10.에러코드.md) 또는 (2) 우리 서버 내부 코드다. 결제 내역 화면에서
실패 코드에 마우스를 올리면 이 표의 의미를 툴팁으로 보여준다.

매핑에 없는 코드는 의미를 알 수 없으므로 빈 문자열을 반환하고, 호출측은
저장된 failure_message로 폴백한다(payment_error_meaning 참고).
"""

# 토스 결제/빌링 승인 단계에서 자주 발생하는 실패 코드(charge 호출 거절/오류) + 우리 내부 코드.
PAYMENT_ERROR_LABELS: dict[str, str] = {
    # ── 카드 거절/한도/잔액 ──
    "REJECT_CARD_COMPANY": "카드사에서 결제 승인을 거절했습니다.",
    "REJECT_CARD_PAYMENT": "한도 초과 또는 잔액 부족으로 결제가 거절되었습니다.",
    "REJECT_ACCOUNT_PAYMENT": "잔액 부족으로 결제가 거절되었습니다.",
    "INVALID_REJECT_CARD": "카드 사용이 거절되었습니다. 카드사 문의가 필요합니다.",
    # ── 카드 정보/상태 ──
    "INVALID_CARD_NUMBER": "카드번호를 다시 확인해야 합니다.",
    "INVALID_CARD_EXPIRATION": "카드 유효기간 정보를 다시 확인해야 합니다.",
    "INVALID_STOPPED_CARD": "정지된 카드입니다.",
    "INVALID_CARD_LOST_OR_STOLEN": "분실 또는 도난 신고된 카드입니다.",
    # ── 금액/한도 ──
    "BELOW_MINIMUM_AMOUNT": "최소 결제금액 미만입니다(신용카드 100원·계좌 200원 이상).",
    "EXCEED_MAX_AMOUNT": "거래금액 한도를 초과했습니다.",
    "EXCEED_MAX_PAYMENT_AMOUNT": "하루 결제 가능 금액을 초과했습니다.",
    "EXCEED_MAX_MONTHLY_PAYMENT_AMOUNT": "당월 결제 가능 금액(100만원)을 초과했습니다.",
    "EXCEED_MAX_DAILY_PAYMENT_COUNT": "하루 결제 가능 횟수를 초과했습니다.",
    "EXCEED_MAX_ONE_DAY_AMOUNT": "일일 한도를 초과했습니다.",
    "EXCEED_MAX_AUTH_COUNT": "최대 인증 횟수를 초과했습니다. 카드사 문의가 필요합니다.",
    # ── 일시/시스템 오류 ──
    "PROVIDER_ERROR": "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해야 합니다.",
    "CARD_PROCESSING_ERROR": "카드사에서 오류가 발생했습니다.",
    "UNKNOWN_PAYMENT_ERROR": "결제에 실패했습니다. 반복되면 은행/카드사 문의가 필요합니다.",
    "NOT_AVAILABLE_PAYMENT": "결제가 불가능한 시간대입니다.",
    "NOT_AVAILABLE_BANK": "은행 서비스 시간이 아닙니다.",
    # ── 인증/요청 ──
    "INVALID_PASSWORD": "결제 비밀번호가 일치하지 않습니다.",
    "INVALID_REQUEST": "잘못된 결제 요청입니다.",
    "UNAUTHORIZED_KEY": "인증되지 않은 키입니다(연동 키 확인 필요).",
    "NOT_FOUND_PAYMENT": "존재하지 않는 결제 정보입니다.",
    "NOT_FOUND_PAYMENT_SESSION": "결제 시간이 만료되었습니다.",
    # ── 우리 서버 내부 코드 ──
    "NO_BILLING_KEY": "등록된 결제수단(빌링키)이 없습니다. 카드를 다시 등록해야 합니다.",
    "CANCEL_DISABLED": "이 서비스는 결제 취소가 허용되지 않습니다.",
    "PAYMENT_UNRESOLVED": "결제 결과가 아직 확인되지 않았습니다(타임아웃). 정산 스윕이 재확인합니다.",
    "SERVER_DISABLED": "결제서버가 일시 비활성화(점검) 상태입니다.",
}


def payment_error_meaning(code: str | None) -> str:
    """실패 코드의 한글 의미를 반환. 매핑에 없으면 빈 문자열(호출측이 메시지로 폴백)."""
    if not code:
        return ""
    return PAYMENT_ERROR_LABELS.get(code, "")
