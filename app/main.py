"""FastAPI 앱 진입점.

lifespan에서 의존성(설정·DB·Redis·암호화 키·스케줄러)을 초기화/정리하고,
외부 API(`/api/v1`)와 Admin 콘솔(`/admin`) 라우터, 정적 파일, 예외 핸들러를 등록한다.
"""
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.admin import router as admin_router
from app.admin.deps import register_admin_exception_handlers
from app.api.errors import register_error_handlers
from app.api.v1 import router as api_v1_router
from app.core.config import Settings
from app.core.crypto import AesGcmCipher
from app.core.db import create_engine, create_session_factory
from app.notifications.email import ConsoleEmailSender, EmailSender, GmailEmailSender
from app.notifications.service_notify import HttpServiceNotifier, ServiceNotifier
from app.scheduler.runner import start_scheduler
from app.toss.client import TossClient
from app.toss.provider import TossClientProvider

# ── Swagger(OpenAPI) 문서 메타데이터 ────────────────────────────────────────
# 외부 서비스 개발자가 /docs(Swagger UI)만 보고도 인증·호출 방법을 알 수 있도록
# 앱 레벨 설명(인증 헤더·HMAC 서명 알고리즘·에러 코드·태그별 설명)을 상세히 제공한다.

# 인증·서명·에러·흐름을 담은 Swagger 상단 설명(Markdown). description에 그대로 주입된다.
API_DESCRIPTION = """
사내 여러 서비스가 공통으로 사용하는 **구독/결제 API 서버**입니다.
외부 서비스는 발급받은 **서비스 API 키 + HMAC 서명**으로 인증한 뒤 아래 엔드포인트를 호출합니다.

> ⚠️ Swagger UI의 **Try it out**은 서명을 자동 계산하지 못합니다.
> 아래 "요청 서명 만들기"의 헤더 4개를 직접 만들어 넣거나, 서버 측 HMAC 헬퍼로 서명한 요청을 보내세요.

---

## 1. 사전 준비

1. 운영자가 어드민 콘솔에서 **서비스를 등록**하면 `서비스 API 키`(`svc_...`)와 `HMAC 시크릿`이 발급됩니다.
2. 서비스 담당자에게 전달된 **API 키·HMAC 시크릿**을 안전하게 보관하세요. (HMAC 시크릿은 재발급 외 재확인 불가)
3. 호출하는 서버의 **출발지 IP**가 서비스의 **허용 IP 목록**에 등록되어 있어야 합니다.

## 2. 인증 — 모든 요청에 필요한 헤더

`GET /api/v1/services`(서비스 목록)와 `POST /api/v1/webhooks/toss`(토스 → 서버 웹훅)를 **제외한** 모든 엔드포인트는
다음 4개 헤더가 모두 필요합니다.

| 헤더 | 설명 | 예시 |
|------|------|------|
| `x-service-key` | 발급받은 서비스 API 키 | `svc_AbC123...` |
| `x-timestamp`   | 현재 Unix epoch **초**(정수 문자열). 서버 허용 오차(기본 ±300초) 내여야 함 | `1717977600` |
| `x-nonce`       | 요청마다 고유한 임의 문자열. **1회용**(재전송 방지, 약 10분간 재사용 불가) | `f3a9c1...` |
| `x-signature`   | 아래 규칙으로 만든 HMAC-SHA256 서명(hex) | `9b1d...` |

## 3. 요청 서명 만들기 (`x-signature`)

서명 대상 문자열(canonical string)은 다음 5개 값을 **개행(`\\n`)으로 연결**합니다.
각 구성요소에는 개행 문자가 들어갈 수 없습니다.

```
canonical = METHOD + "\\n" + PATH + "\\n" + TIMESTAMP + "\\n" + NONCE + "\\n" + SHA256_HEX(BODY)
signature = HMAC_SHA256(hmac_secret, canonical).hexdigest()
```

- `METHOD`: 대문자 HTTP 메서드 (`POST`, `GET`)
- `PATH`: 쿼리스트링을 제외한 경로 (`/api/v1/subscriptions`)
- `TIMESTAMP` / `NONCE`: 위 헤더와 **동일한 값**
- `SHA256_HEX(BODY)`: 요청 본문 바이트의 SHA-256 hex. **본문이 없으면 빈 바이트**(`b""`)의 해시를 사용

### Python 예시

```python
import hashlib, hmac, json, time, secrets, requests

API_KEY = "svc_..."          # x-service-key
HMAC_SECRET = "..."          # 발급받은 HMAC 시크릿
BASE = "http://localhost:8000"

method, path = "POST", "/api/v1/subscriptions"
body = json.dumps({
    "external_user_id": "user-123",
    "plan_id": "00000000-0000-0000-0000-000000000000",
    "auth_key": "toss_auth_key",
    "customer_key": "cust-123",
}, separators=(",", ":")).encode()       # 전송 본문과 바이트가 정확히 일치해야 함

ts = str(int(time.time()))
nonce = secrets.token_hex(16)
body_hash = hashlib.sha256(body).hexdigest()
canonical = "\\n".join([method, path, ts, nonce, body_hash])
sig = hmac.new(HMAC_SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()

resp = requests.post(BASE + path, data=body, headers={
    "content-type": "application/json",
    "x-service-key": API_KEY,
    "x-timestamp": ts,
    "x-nonce": nonce,
    "x-signature": sig,
})
print(resp.status_code, resp.json())
```

## 4. 처리율 제한(Rate limit)

- 일반 API: 서비스당 분당 요청 수 제한 (`RATE_LIMITED`, HTTP 429)
- 결제성 API(구독 생성·카드변경·수동결제·단건결제·취소): 더 엄격한 결제 전용 제한이 추가로 적용됩니다.

## 5. 에러 응답 형식

모든 에러는 동일한 JSON 형태로 반환됩니다.

```json
{ "error": { "code": "UNAUTHORIZED", "message": "인증에 실패했습니다" } }
```

| HTTP | code | 의미 |
|------|------|------|
| 401 | `UNAUTHORIZED`     | API 키·타임스탬프·nonce·서명 누락 또는 불일치 |
| 403 | `FORBIDDEN`        | 허용되지 않은 IP |
| 402 | `PAYMENT_FAILED`   | 토스 결제 승인/자동결제 실패 |
| 404 | `NOT_FOUND`        | 구독·결제·요금제 등 리소스 없음 |
| 409 | `CONFLICT`         | 서비스+사용자 당 구독은 1개만 가능(중복) 등 상태 충돌 |
| 422 | `VALIDATION_ERROR` | 요청 형식 오류 또는 비즈니스 규칙 위반(예: 체험 미지원 요금제에 trial 요청) |
| 429 | `RATE_LIMITED`     | 요청 한도 초과 |
| 503 | `SERVER_DISABLED`  | 결제서버 전체 비활성화(킬스위치) 상태 |

## 6. 전형적인 사용 흐름

1. `GET /plans` 로 구독 가능한 요금제 목록과 실제 청구 금액(`amount`)을 조회
2. 토스 결제창에서 카드 등록 → `authKey` / `customerKey` 획득
3. `POST /subscriptions` 로 구독 생성(빌링키 발급 + 첫 결제 또는 체험 시작)
4. 이후 만료일마다 서버가 자동으로 정기 결제를 청구(자동연장)
5. 외부 서비스는 `GET /subscriptions/{external_user_id}` 의 `access_allowed`(true/false)로 사용자 접근을 판단
"""

# 태그(엔드포인트 그룹)별 설명 — Swagger UI에서 섹션 헤더 아래에 표시된다.
OPENAPI_TAGS = [
    {"name": "services",
     "description": "등록된 서비스 목록 조회. **무인증**(API 키 입력 전 단계). id·이름·상태만 노출하며 민감정보는 포함하지 않는다."},
    {"name": "plans",
     "description": "인증된 서비스의 **활성 요금제** 목록 조회. 청구 금액·결제주기·체험·자동갱신 정보를 제공한다."},
    {"name": "subscriptions",
     "description": "구독 생성·조회·취소·재개·카드변경·수동결제. 서비스+사용자 당 **구독 1개** 규칙이 적용된다."},
    {"name": "payments",
     "description": "구독과 무관한 **단건(1회성) 결제** 생성·취소 및 결제 내역 조회."},
    {"name": "webhooks",
     "description": "토스페이먼츠가 서버로 **푸시**하는 결제 이벤트 수신 엔드포인트. 외부 서비스가 직접 호출하지 않는다."},
]


_swagger_security = HTTPBasic(auto_error=False)


def _register_protected_docs(app: FastAPI, settings: Settings) -> None:
    """Swagger UI(/docs)·OpenAPI 스키마(/openapi.json)를 HTTP Basic 인증으로 보호한다.

    SWAGGER_ID / SWAGGER_PW가 모두 설정된 경우에만 라우트를 등록한다.
    비워두면 docs 라우트 자체가 없어 404가 되어(운영 기본값), 의도치 않은 노출을 막는다.
    """
    swagger_id = settings.swagger_id
    swagger_pw = settings.swagger_pw
    if not (swagger_id and swagger_pw):
        return  # 자격증명 미설정 → docs 비활성화

    def verify(credentials: HTTPBasicCredentials | None = Depends(_swagger_security)) -> None:
        """입력한 id/pw를 설정값과 타이밍 안전 비교. 불일치 시 401 + Basic 인증 요구."""
        # compare_digest로 타이밍 공격 방지. 미입력(None)도 동일하게 실패 처리.
        id_ok = credentials is not None and secrets.compare_digest(
            credentials.username, swagger_id)
        pw_ok = credentials is not None and secrets.compare_digest(
            credentials.password, swagger_pw)
        if not (id_ok and pw_ok):
            # WWW-Authenticate 헤더로 브라우저 기본 로그인 팝업을 띄운다.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="인증이 필요합니다",
                headers={"WWW-Authenticate": "Basic"})

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi_schema(_: None = Depends(verify)):
        """인증 통과 시에만 OpenAPI 스키마(JSON)를 반환한다."""
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui(_: None = Depends(verify)):
        """인증 통과 시에만 Swagger UI 페이지를 반환한다."""
        # Swagger UI가 동일 출처로 /openapi.json을 다시 요청할 때 브라우저가
        # 저장된 Basic 자격증명을 자동 재전송하므로 스키마도 보호된 채로 로드된다.
        return get_swagger_ui_html(openapi_url="/openapi.json",
                                   title=f"{app.title} - Swagger UI")


def _default_email_sender(settings: Settings) -> EmailSender:
    """GMAIL_ID/PW가 설정되면 실제 Gmail 발송, 아니면 콘솔 출력."""
    if settings.gmail_id and settings.gmail_pw:
        return GmailEmailSender(
            host=settings.smtp_host, port=settings.smtp_port,
            username=settings.gmail_id, password=settings.gmail_pw,
            from_name=settings.mail_from_name)
    return ConsoleEmailSender()


def create_app(settings: Settings | None = None, *,
               toss_client: TossClient | None = None,
               email_sender: EmailSender | None = None,
               notifier: "ServiceNotifier | None" = None,
               engine: AsyncEngine | None = None) -> FastAPI:
    app_settings = settings or Settings()
    own_engine = engine is None
    # T7 컷오버: own_toss 제거 — 전역 HttpTossClient를 앱이 직접 소유하지 않음

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = app_settings
        # 암호화 키를 가장 먼저 검증 — 키가 없으면 다른 리소스를 만들기 전에 즉시 실패
        app.state.cipher = AesGcmCipher(app_settings.encryption_key)
        # 커넥션 풀은 .env(DB_POOL_SIZE 등)로 조정 가능 — 감사 Phase 1(성능 M3)
        app.state.engine = engine or create_engine(
            app_settings.database_url,
            pool_size=app_settings.db_pool_size,
            max_overflow=app_settings.db_max_overflow,
            pool_timeout=app_settings.db_pool_timeout,
            pool_recycle=app_settings.db_pool_recycle)
        app.state.session_factory = create_session_factory(app.state.engine)
        app.state.redis = Redis.from_url(app_settings.redis_url, decode_responses=True)
        # T7 컷오버: app.state.toss(전역 HttpTossClient) 제거.
        # 서비스별 토스 클라이언트 해석기. 테스트가 toss_client(Fake)를 주입하면
        # override로 사용해 모든 서비스에 동일 Fake를 반환한다(키 불필요).
        # 운영은 override_client=None → 서비스별 암호화 키로 HttpTossClient 생성.
        app.state.toss_provider = TossClientProvider(
            app.state.cipher, app_settings.toss_api_base_url,
            override_client=toss_client)
        app.state.email_sender = email_sender or _default_email_sender(app_settings)
        # 서비스 알림 발송기(아웃고잉 웹훅) — 기본은 실 전송(HTTP), 테스트는 Recording 주입.
        app.state.notifier = notifier or HttpServiceNotifier(app.state.cipher)
        scheduler = start_scheduler(app) if app_settings.scheduler_enabled else None
        yield
        # shutdown(wait=False)는 실행 중인 잡에 CancelledError를 주입한다.
        # run_renewals의 finally가 락을 해제하므로 redis 닫기 전에 먼저 호출.
        # 잡 중도 취소 시 PENDING으로 남은 결제는 다음 주기 정산 스윕이 확정.
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await app.state.redis.aclose()
        # provider 캐시 내 모든 HttpTossClient를 정리한다(override는 provider가 소유하지 않으므로 건드리지 않음).
        # T7 컷오버: app.state.toss(전역 HttpTossClient) 제거 — aclose 블록도 삭제.
        await app.state.toss_provider.aclose()
        if own_engine:
            await app.state.engine.dispose()

    app = FastAPI(
        title="구독/결제 API 서버",
        version="1.0.0",
        # Swagger UI 상단에 인증·서명·에러·흐름 가이드를 노출 (외부 서비스 개발자용)
        description=API_DESCRIPTION,
        # 엔드포인트 그룹(태그)별 설명
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        # 기본 docs/openapi 라우트는 끄고, 아래에서 HTTP Basic 인증으로 보호한 커스텀 라우트로 제공
        docs_url=None, redoc_url=None, openapi_url=None,
        redirect_slashes=False)
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """모든 응답에 보안 헤더를 부착한다(감사 Phase 2 — 보안 M-3).

        - X-Frame-Options: DENY — 어드민 화면 클릭재킹 차단(iframe 삽입 불가)
        - X-Content-Type-Options: nosniff — MIME 스니핑으로 인한 콘텐츠 오해석 방지
        - Referrer-Policy: same-origin — 외부 사이트로 어드민 URL(쿼리 포함) 유출 방지
        - Strict-Transport-Security: prod에서만 — HTTPS 강제(개발 HTTP 환경 배려)

        Content-Security-Policy는 의도적으로 제외 — 어드민 템플릿이 인라인
        스크립트/스타일(htmx 패턴)을 사용해 무차별 적용 시 화면이 깨진다.
        도입하려면 템플릿 정리와 함께 별도 작업으로 진행할 것.
        """
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if app_settings.environment == "prod":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    register_error_handlers(app)
    _register_protected_docs(app, app_settings)
    app.include_router(api_v1_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/admin")
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")),
              name="static")
    # 서비스 담당자 매뉴얼(docs/manual)을 정적 사이트로 공개 서빙한다.
    #  - 로그인 페이지에서 인증 없이 열람할 수 있도록 /admin 바깥(공개)에 마운트한다.
    #  - html=True : "/manual/" 요청 시 index.html을 자동 반환(다중 페이지·상대 링크 동작).
    #  - check_dir=False : 운영 이미지에 docs/manual이 없어도 기동이 깨지지 않게 함
    #    (없으면 요청 시 404). .dockerignore에서 docs/manual만 이미지에 포함시킨다.
    manual_dir = Path(__file__).parent.parent / "docs" / "manual"
    app.mount("/manual", StaticFiles(directory=str(manual_dir), html=True, check_dir=False),
              name="manual")
    # 새 '사용·개발 매뉴얼'(docs/user_manual)을 /user-manual 로 공개 서빙한다.
    #  - 로그인 페이지의 '전체 매뉴얼 보기' 링크가 이 경로를 가리킨다(login.html).
    #  - html=True : "/user-manual/" 요청 시 index.html 자동 반환(상대 링크 동작).
    #  - check_dir=False : 이미지에 없어도 기동이 깨지지 않게(없으면 404).
    #    .dockerignore에서 docs/user_manual 도 이미지에 포함시킨다.
    user_manual_dir = Path(__file__).parent.parent / "docs" / "user_manual"
    app.mount("/user-manual", StaticFiles(directory=str(user_manual_dir), html=True, check_dir=False),
              name="user_manual")
    register_admin_exception_handlers(app)

    @app.get("/", include_in_schema=False)
    async def root():
        """루트 접근 시 어드민 콘솔로 이동.

        미로그인 상태면 /admin 대시보드가 다시 /admin/login으로 보내므로,
        결과적으로 로그인 화면이 노출된다.
        """
        return RedirectResponse("/admin", status_code=307)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
