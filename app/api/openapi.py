"""Swagger(OpenAPI) 문서화용 공용 `responses` 조각 모음.

라우트 데코레이터의 ``responses=`` 인자에 재사용해, 각 엔드포인트가
어떤 에러 코드를 반환하는지 Swagger UI에 명시한다. 모든 에러는
``ErrorResponse`` 형태({"error": {"code", "message"}})로 반환된다.
"""

from app.schemas.api import ErrorResponse

# 인증이 필요한 모든 엔드포인트의 공통 에러 응답 (HMAC 인증·IP·Rate limit·킬스위치).
AUTH_RESPONSES: dict = {
    401: {"model": ErrorResponse,
          "description": "UNAUTHORIZED — API 키/타임스탬프/nonce/서명 누락 또는 불일치"},
    403: {"model": ErrorResponse, "description": "FORBIDDEN — 허용되지 않은 IP"},
    429: {"model": ErrorResponse, "description": "RATE_LIMITED — 요청 한도 초과"},
    503: {"model": ErrorResponse, "description": "SERVER_DISABLED — 결제서버 비활성화(킬스위치)"},
}

# 결제성 엔드포인트가 추가로 반환할 수 있는 에러.
PAYMENT_RESPONSES: dict = {
    **AUTH_RESPONSES,
    402: {"model": ErrorResponse, "description": "PAYMENT_FAILED — 토스 결제 승인/자동결제 실패"},
}

# 리소스를 찾지 못할 수 있는 엔드포인트.
NOT_FOUND_RESPONSE: dict = {
    404: {"model": ErrorResponse, "description": "NOT_FOUND — 리소스 없음"},
}

# 상태 충돌(예: 구독 중복)이 발생할 수 있는 엔드포인트.
CONFLICT_RESPONSE: dict = {
    409: {"model": ErrorResponse, "description": "CONFLICT — 상태 충돌(예: 구독 중복)"},
}

# 비즈니스 규칙 위반/입력 오류.
VALIDATION_RESPONSE: dict = {
    422: {"model": ErrorResponse, "description": "VALIDATION_ERROR — 요청 형식 오류 또는 규칙 위반"},
}
