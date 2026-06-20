"""API 예외 → HTTP JSON 응답 핸들러 등록.

register_error_handlers를 앱 기동 시 한 번 호출하면 아래 세 핸들러가 등록된다.

- DomainError          → exc.http_status / exc.code / exc.message 그대로 반환.
                          비즈니스 규칙 위반(AuthenticationError 401,
                          PermissionDeniedError 403, RateLimitedError 429 등)을
                          일관된 JSON 형태로 클라이언트에 전달한다.
- RequestValidationError → 422 + 잘못된 필드 목록 (Pydantic 검증 실패).
                           body 위치 키는 중복 노출을 막기 위해 필터링한다.
- Exception (catch-all) → 500 + 고정 메시지. 내부 스택 트레이스는 로그에만 남기고
                           클라이언트에게 내부 정보가 노출되지 않도록 한다.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import DomainError

logger = logging.getLogger("payment.api")


def register_error_handlers(app: FastAPI) -> None:
    """FastAPI 앱에 전역 예외 핸들러 세 가지를 등록한다.

    앱 팩토리(create_app)에서 한 번 호출한다. 이후 모든 API 요청에서
    예외가 처리되지 않으면 여기 등록된 핸들러가 JSON 응답으로 변환한다.
    """
    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        # DomainError 서브클래스마다 http_status/code가 다르므로 exc 속성을 그대로 사용
        return JSONResponse(status_code=exc.http_status,
                            content={"error": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        # 중복 제거 후 정렬 — 어느 필드가 잘못됐는지 클라이언트에 알린다
        fields = sorted({".".join(str(p) for p in e["loc"] if p != "body")
                         for e in exc.errors()})
        return JSONResponse(status_code=422, content={"error": {
            "code": "VALIDATION_ERROR",
            "message": f"요청 형식이 올바르지 않습니다: {', '.join(fields)}"}})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("unhandled error")
        # 내부 정보 비노출
        return JSONResponse(status_code=500, content={"error": {
            "code": "INTERNAL_ERROR", "message": "서버 오류가 발생했습니다"}})
