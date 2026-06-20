"""도메인 예외 계층 모듈.

모든 비즈니스 오류를 ``DomainError`` 하위 클래스로 표현하며,
각 클래스는 HTTP 상태 코드와 머신 리더블 ``code`` 문자열을 클래스 속성으로 가진다.
FastAPI 예외 핸들러에서 이 속성을 읽어 일관된 JSON 오류 응답을 만든다.
"""


class DomainError(Exception):
    """모든 도메인 예외의 기반 클래스.

    ``code``와 ``http_status``를 클래스 속성으로 정의해 서브클래스가
    각자의 HTTP 매핑을 명시적으로 선언하도록 유도한다.
    생성자에서 ``code`` / ``http_status``를 오버라이드할 수 있어
    동적 오류 코드가 필요할 때 서브클래스 없이도 사용할 수 있다.
    """

    code = "DOMAIN_ERROR"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None,
                 http_status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status


class NotFoundError(DomainError):
    """요청한 리소스(서비스·요금제·구독 등)가 존재하지 않을 때 (HTTP 404)."""

    code = "NOT_FOUND"
    http_status = 404


class ConflictError(DomainError):
    """중복 생성·동시 수정 등 상태 충돌 시 (HTTP 409).

    예: 동일 서비스·사용자에 대한 구독이 이미 존재하는 경우.
    """

    code = "CONFLICT"
    http_status = 409


class AuthenticationError(DomainError):
    """인증 정보가 없거나 유효하지 않을 때 (HTTP 401).

    예: 세션 만료, 잘못된 API 키, HMAC 서명 불일치.
    """

    code = "UNAUTHORIZED"
    http_status = 401


class PermissionDeniedError(DomainError):
    """인증은 됐지만 해당 작업 권한이 없을 때 (HTTP 403).

    예: 일반 관리자가 SYSTEM_ADMIN 전용 기능에 접근하는 경우.
    """

    code = "FORBIDDEN"
    http_status = 403


class InputValidationError(DomainError):
    """비즈니스 규칙 수준의 입력 오류 (HTTP 422).

    pydantic 스키마 검증은 FastAPI가 422로 처리하며, 이 클래스는
    스키마를 통과한 뒤 서비스 레이어에서 발견하는 의미론적 오류에 사용한다.
    """

    code = "VALIDATION_ERROR"
    http_status = 422


class RateLimitedError(DomainError):
    """분당 요청 한도를 초과했을 때 (HTTP 429).

    Redis 슬라이딩 윈도우 카운터로 판단하며, 일반 API와 결제 API의 한도가 다르다.
    """

    code = "RATE_LIMITED"
    http_status = 429


class PaymentFailedError(DomainError):
    """토스페이먼트 결제 승인 또는 자동결제 실패 시 (HTTP 402).

    재시도 정책(``retry_limit``, ``retry_interval_hours``)에 따라
    일정 횟수 재시도 후에도 실패하면 구독이 Suspended 상태로 전환된다.
    """

    code = "PAYMENT_FAILED"
    http_status = 402


class ServerDisabledError(DomainError):
    """결제서버 전체 비활성화(킬스위치) 상태 — 외부 API 차단 (HTTP 503).

    GlobalSettings.server_disabled=True 일 때 외부 API 진입 직후
    ``ensure_server_enabled`` 게이트 헬퍼가 이 예외를 발생시킨다.
    어드민 라우트는 영향을 받지 않는다.
    """

    code = "SERVER_DISABLED"
    http_status = 503
