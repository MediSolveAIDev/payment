"""admin 인증 라우트.

로그인·로그아웃·비밀번호 설정(초기 설정 및 재설정) 기능을 제공한다.
세션은 Redis에 저장하고, 클라이언트에는 HttpOnly 쿠키로 세션 ID를 전달한다.
"""

import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import render
from app.admin.deps import SESSION_COOKIE, AdminContext, require_any, validate_csrf
from app.core.deps import get_client_ip, get_db, get_redis, get_settings
from app.core.config import Settings, default_settings
from app.core.errors import AuthenticationError, InputValidationError
from app.services import auth as auth_service

router = APIRouter()

# 로그인 시도 IP당 분당 상한(감사 Phase 2 — 보안 M-2).
# 계정당 잠금(5회/15분)은 '존재하는 계정'만 보호한다 — 존재하지 않는 이메일을
# 바꿔가며 시도하는 패스워드 스프레이와 감사 로그 팽창(DoS)은 IP 단위로 막는다.
LOGIN_RATE_LIMIT_PER_MINUTE = default_settings().admin_login_rate_limit_per_minute  # .env로 조정
LOGIN_RATE_WINDOW_TTL = 90  # 1분 윈도우 + 여유(외부 API rate limit과 동일 패턴)


async def _login_rate_limited(redis: Redis, ip: str) -> bool:
    """IP 기준 로그인 시도 카운트 후 상한 초과 여부 반환.

    상한 초과 시 인증 로직(DB 조회·감사 기록) 자체를 건너뛰므로
    무차별 시도가 감사 테이블을 채우는 것도 함께 차단된다.
    """
    window = int(time.time() // 60)
    key = f"rl:login:{ip}:{window}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, LOGIN_RATE_WINDOW_TTL)
    return count > LOGIN_RATE_LIMIT_PER_MINUTE


@router.get("/login")
async def login_page(request: Request, settings: Settings = Depends(get_settings)):
    """로그인 폼 렌더.

    로컬 개발(environment == "dev")에서만 설정의 dev_login_email / dev_login_password로
    입력 필드를 미리 채워 개발 편의를 돕는다.
    스테이징(stg)·운영(prod) 등 그 외 환경에서는 절대 기본값을 채우지 않는다
    (외부에 노출되는 환경에서 자격증명이 화면에 보이지 않도록 보안 사고 방지).
    """
    # 로컬 개발(dev)에서만 자동입력. stg·prod 등은 제외한다.
    dev = settings.environment == "dev"
    return render(request, "login.html", error=None,
                  prefill_email=settings.dev_login_email if dev else "",
                  prefill_password=settings.dev_login_password if dev else "")


@router.get("/intro", response_class=HTMLResponse)
async def intro_page():
    """서비스 소개 가이드 페이지를 반환한다. (로그인 미필요)"""
    intro_path = Path(__file__).resolve().parent.parent.parent.parent / "docs" / "gemini" / "introduce" / "user_guide.html"
    if not intro_path.exists():
        return HTMLResponse(content="가이드 문서를 찾을 수 없습니다.", status_code=404)
    return HTMLResponse(content=intro_path.read_text(encoding="utf-8"))


@router.post("/login")
async def login_submit(request: Request,
                       db: AsyncSession = Depends(get_db),
                       redis: Redis = Depends(get_redis),
                       settings: Settings = Depends(get_settings)):
    """로그인 처리.

    인증 성공 시 Redis에 세션을 생성하고 클라이언트에 세션 쿠키를 설정한다.

    쿠키 속성:
        httponly=True  — JavaScript로 쿠키 접근 불가(XSS 탈취 방지).
        samesite="lax" — CSRF 공격을 완화한다. (strict보다 완화해 일반 링크 이동은 허용.)
        secure         — 운영(prod)에서만 HTTPS 전용으로 전송. 개발 환경은 HTTP도 허용.
        max_age        — 설정의 session_ttl_seconds를 따른다.
        path="/"       — /admin 하위뿐 아니라 전체 경로에서 쿠키가 전송되도록 설정.
                         (하위 경로 고정 시 /admin/login 리다이렉트에서 쿠키 누락 가능.)
    """
    form = await request.form()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
    ip = get_client_ip(request, settings)
    # IP 기준 시도 제한(감사 Phase 2 — 보안 M-2) — 초과 시 인증 로직 진입 전 차단
    if await _login_rate_limited(redis, ip):
        return render(request, "login.html",
                      error="로그인 시도가 너무 많습니다. 잠시 후 다시 시도해주세요")
    try:
        session_id, _user = await auth_service.login(
            db, redis, settings, email=email, password=password, ip=ip)
    except AuthenticationError as exc:
        return render(request, "login.html", error=exc.message)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE, session_id, httponly=True, samesite="lax",
        secure=settings.environment == "prod",
        max_age=settings.session_ttl_seconds, path="/")
    return response


@router.post("/logout")
async def logout(request: Request,
                 ctx: AdminContext = Depends(require_any),
                 redis: Redis = Depends(get_redis)):
    """로그아웃 처리.

    CSRF 토큰을 검증해 로그아웃 요청의 진정성을 확인한다.
    (로그아웃도 POST로 처리해 GET 방식 CSRF 로그아웃 공격을 방지.)
    Redis에서 세션을 삭제하고 클라이언트 쿠키를 제거한 뒤 로그인 페이지로 리다이렉트.
    """
    await validate_csrf(request, ctx)
    await auth_service.logout(redis, ctx.session_id)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/setup-password")
async def setup_password_page(request: Request, token: str = ""):
    """비밀번호 설정 폼 렌더.

    계정 생성 후 메일로 전달된 token을 쿼리 파라미터로 받아 폼에 hidden으로 포함시킨다.
    초기 설정과 비밀번호 재설정 모두 이 폼을 공용으로 사용한다.
    """
    return render(request, "setup_password.html", token=token, error=None)


@router.post("/setup-password")
async def setup_password_submit(request: Request,
                                db: AsyncSession = Depends(get_db),
                                redis: Redis = Depends(get_redis)):
    """비밀번호 설정 처리.

    비밀번호 확인 불일치는 서비스 레이어를 거치지 않고 폼 단에서 즉시 오류 처리한다.
    성공 시 로그인 페이지로 리다이렉트한다.

    redis 전달:
        비밀번호 재설정 시 해당 사용자의 기존 세션을 모두 파기하기 위해
        서비스 레이어에 Redis를 함께 전달한다.
    """
    form = await request.form()
    token = str(form.get("token", ""))
    password = str(form.get("password", ""))
    confirm = str(form.get("password_confirm", ""))
    if password != confirm:
        return render(request, "setup_password.html", token=token,
                      error="비밀번호가 일치하지 않습니다")
    try:
        # redis 전달 — 비밀번호 재설정 시 해당 사용자의 기존 세션을 모두 파기
        await auth_service.setup_password(db, token=token, password=password, redis=redis)
    except InputValidationError as exc:
        return render(request, "setup_password.html", token=token, error=exc.message)
    return RedirectResponse("/admin/login", status_code=303)
